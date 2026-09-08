"""Dispatch the upstream SDLC workflows and apply what they recorded to factory state.

    import sdlc; sdlc.launch("validate", "gh:pr:12")   dispatch one unit of work
    import sdlc; sdlc.reconcile()                      apply every settled dispatch

THE DIVISION OF LABOUR. `archon-admit`, `archon-ship`, `archon-accept`,
`archon-revise-pr`, `archon-regress` and `archon-merge` are generic: they judge, they
build, they merge, and they return a result. NONE of them touch a label, a counter or
a lock. Everything that is this factory's -- the trusted project profile it evaluates
against, the state machine, the attempt cap, the flood cap, the dial, the STOP button,
the escalation channel -- lives here, on this side of the boundary.

A DISPATCH IS A RECORD BEFORE IT IS A RUN. `launch()` writes a journal under the
operator runtime root and appends its path to the lock BEFORE it asks the engine for
anything, and the engine's acknowledged run id is written to that journal the moment
it comes back. Every later decision -- did this settle, what did it produce, which
effects have already been applied -- reads that one record. Nothing keys on the newest
file with the right name, because the newest file with the right name belongs to
whichever run finished last and that is not necessarily this one.

APPLYING IS RESUMABLE, and it has to be: a result carries several separate effects
(labels on two items, a ratchet commit, a notification, a comment) and the machine can
die between any two of them. Each is recorded in `applied` as it lands, so a retry
performs exactly the ones that did not.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import gate
import ledger
import runtime
import state

WORKFLOWS = {"triage": config.WORKFLOW_TRIAGE, "implement": config.WORKFLOW_IMPLEMENT,
             "validate": config.WORKFLOW_VALIDATE, "fix": config.WORKFLOW_FIX,
             "regress": config.WORKFLOW_REGRESS, "merge": config.WORKFLOW_MERGE}

# Where each workflow records the object this factory consumes, relative to the run's
# artifacts directory. Named per workflow rather than discovered, because "the newest
# JSON under the artifacts root" is how a consumer ends up applying another run's
# result to this run's target.
ARTIFACTS = {"triage": "admission.json", "implement": "ship-result.json",
             "validate": "acceptance.json", "fix": "revise-result.json",
             "regress": "regress/result.json", "merge": "merge.json"}

# The dial level each action requires. The same table the dispatcher uses, kept here
# because `launch()` re-asks the question immediately before it spends anything --
# preparation reads GitHub and git and can take a minute, and a STOP raised during
# that minute must still land.
LEVELS = {"triage": 4, "implement": 1, "validate": 2, "fix": 1, "regress": 1, "merge": 3}

# The dial at which the factory may file its own bugs. Below it the scheduled
# regression still runs and reports.
PUBLISH_LEVEL = 4

TERMINAL = {"completed", "failed", "cancelled", "canceled", "abandoned", "error",
            "errored", "timeout", "timed_out", "stopped"}

VERDICTS = {"approve", "request_changes", "reject", "inconclusive"}

# What a delivery may not touch, handed to the upstream publication and acceptance
# policies as repository-relative prefixes. The guard enforces the same list on the
# diff; this stops the work before it is published rather than after.
PROTECTED_PREFIXES = ["factory", "harness", ".factory/locks", ".factory/holdout",
                      ".archon", ".github"]

# WHAT THE JUDGE IS ALLOWED TO KNOW ABOUT THE FIXED GATE, and why it needs telling.
#
# Acceptance keeps a fixed profile's argv and streams private, so without an operator
# attestation the judge cannot tell this factory's whole gate from an arbitrary command
# that exited zero -- and is right to answer `inconclusive`. These two strings are that
# attestation. They describe what the gate covers; they never describe a result, and
# upstream treats them as a declaration that a deterministic failure still overrides.
GATE_DESCRIPTION = (
    "The factory's own structural gate, rebuilt from the base branch rather than from "
    "the candidate: the secret preflight, the protected-path guard, the diff size and "
    "file-scope caps, the tripwire, and then the project's whole validation command. "
    "Its run log is measured for every required marker, for the ratchet floors on how "
    "many checks must have RUN, for the uncalibrated-margin ceiling, and for the "
    "deliberate-defect set, which must be fully injected and fully caught.")
GATE_COMPLETENESS = (
    "These commands are the complete applicable gate for this repository. The single "
    "command runs every structural check the factory enforces and the project's entire "
    "validation harness, and it is reconstructed from the base branch, so the candidate "
    "cannot supply, weaken or skip any part of it.")

# The trusted project context the judge cannot fetch for itself: it has no tools, and a
# pointer from one governance file into another is a pointer it cannot follow. Read from
# the resolved base SHA, so it is the text a human last agreed to.
ACCEPT_CONTEXT = [{"id": "mission", "source": "base:MISSION.md"},
                  {"id": "factory-rules", "source": "base:FACTORY_RULES.md"}]


def command(argv: list[str], cwd: Path | None = None, timeout: int = 300) -> str:
    result = subprocess.run(argv, cwd=str(cwd or config.ROOT), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        # STDOUT WHEN STDERR IS SILENT, because the engine's `--json` mode reports its
        # own refusals as `{"ok": false, "error": ...}` on stdout and exits non-zero.
        # Raising on the empty channel turns a named refusal into "exited 1".
        detail = (result.stderr.strip() or result.stdout.strip())[-600:]
        raise RuntimeError(f"{argv[0]} exited {result.returncode}: {detail}")
    return result.stdout.strip()


def engine(argv: list[str]) -> dict:
    """One `archon ... --json` call, read as the ONE document it promises.

    THE WHOLE BUFFER, PARSED ONCE. `--json` output goes through the CLI's
    `writeJsonLine`, which is `JSON.stringify(value, null, 2)` followed by a newline --
    a PRETTY-PRINTED document as many lines tall as it has fields, whose last line is
    `}`. An earlier version of this read `lines[-1]`, which parses exactly one shape:
    the single-line reply a mock produces. Against the installed CLI every dispatch
    died on `}`.

    Diagnostics go to stderr in `--json` mode, so anything on stdout that is not this
    one document is a corrupted reply -- truncated, doubled, or prefixed by something
    that should not be there. Each of those is refused by name rather than repaired by
    picking whichever fragment happens to parse.
    """
    name = "archon " + " ".join(argv[:2])
    out = command([shutil.which(config.ARCHON_BIN) or config.ARCHON_BIN, *argv])
    if not out:
        raise RuntimeError(f"{name} returned nothing")
    try:
        value = json.loads(out)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{name} did not return one JSON document ({error.msg} at line "
            f"{error.lineno}). Its `--json` mode writes exactly one, and its "
            f"diagnostics go to stderr, so this reply is corrupt: {out[:200]!r}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} did not return an object")
    return value


def git(*args: str) -> str:
    return command(["git", *args])


def repository() -> str:
    return json.loads(state.gh("repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]


def settings(directory: Path) -> dict:
    """This factory's operator settings, read from the `config.py` in `directory`.

    A SEPARATE PROCESS, and that is the point on both of its call paths. Asked about
    the LIVE config it is a genuinely fresh read: the dial an operator edited two
    minutes into a preparation is the dial this dispatch is held to, where the module
    imported at process start still holds the old one. Asked about a config recovered
    from the base tree it is the only way to read a file that must not be imported
    into this process at all.
    """
    code = ("import config,json; print(json.dumps({"
            "'autonomy':config.AUTONOMY,'command':config.VALIDATE_CMD,"
            "'markers':config.REQUIRED_MARKERS,'slack_caps_autonomy':config.SLACK_CAPS_AUTONOMY,"
            "'floor_path':config.FLOOR_FILE.relative_to(config.ROOT).as_posix(),"
            "'gate_timeout_seconds':config.GATE_TIMEOUT_SECONDS,"
            "'require_isolation':config.REQUIRE_ISOLATION,"
            "'accept_report':config.ACCEPT_REPORT,"
            "'public_probe_scope':config.PUBLIC_PROBE_SCOPE,"
            "'accept_races':config.MERGE_ACCEPT_RACES,"
            "'required_checks':config.MERGE_REQUIRED_CHECKS}))")
    return json.loads(command([sys.executable, "-c",
                               f"import sys; sys.path.insert(0,{str(directory)!r}); " + code]))


def live() -> dict:
    return settings(Path(config.__file__).resolve().parent)


def authorized(action: str, manual: bool, dial: int | None = None) -> bool:
    """The STOP button and the dial, re-read now.

    A manual `factory run` is the operator asking for one unit of work by hand, so it
    is not held to the dial -- except for `merge`, whose authority comes from an
    acceptance receipt rather than from whoever typed the command.
    """
    stopped, _ = state.stop_requested()
    if stopped:
        return False
    if manual and action != "merge":
        return True
    return (live()["autonomy"] if dial is None else dial) >= LEVELS[action]


def identity(target: str, repo: str) -> dict:
    """The exact candidate a receipt may certify: repository, PR, head and base.

    Resolved from the GitHub API rather than from anything a run reported, and
    re-resolved before every decision that depends on it. A receipt certifies the
    identity observed when it was issued, not a pull request that has moved since.
    """
    kind, number = state.parse_target(target)
    if kind != "pr":
        raise ValueError(f"{target} is not a pull request; an explicit PR is required")
    pr = json.loads(state.gh("api", f"repos/{repo}/pulls/{number}"))
    head_repo = (pr.get("head") or {}).get("repo") or {}
    if str(head_repo.get("full_name", "")).lower() != repo.lower():
        raise ValueError("The head is not in this repository; fork pull requests are unsupported")
    if pr["base"]["ref"] != config.BASE_BRANCH:
        raise ValueError(f"PR #{number} targets {pr['base']['ref']}, not {config.BASE_BRANCH}")
    return {"repository": repo, "pr": int(number), "head_sha": pr["head"]["sha"],
            "base_sha": pr["base"]["sha"]}


def validate_receipt(receipt: dict, expected: dict) -> None:
    """Acceptance receipt version 1, checked against the candidate we asked about.

    THE IDENTITY IS THE POINT. A receipt is evidence about one repository, one pull
    request and one pair of commits; applied to anything else it is a stale approval
    for code nobody judged. Everything here is a positive assertion about that tuple,
    and the approve branch additionally requires the execution evidence to be complete
    -- because "no findings" is also what a judge that never ran produces.
    """
    repo = receipt.get("repository")
    if not isinstance(repo, dict) or set(repo) != {"owner", "name"}:
        raise ValueError("Acceptance repository must be an {owner, name} object")
    actual = {key: receipt.get(key) for key in ("pr", "head_sha", "base_sha")}
    actual["repository"] = f"{repo['owner']}/{repo['name']}"
    if receipt.get("schema_version") != 1:
        raise ValueError(f"Unsupported acceptance schema_version {receipt.get('schema_version')!r}")
    if any(not isinstance(actual[key], str) or not re.fullmatch("[0-9a-f]{40}", actual[key])
           for key in ("head_sha", "base_sha")):
        raise ValueError("Acceptance needs full lowercase commit identities")
    if actual != expected:
        raise ValueError(f"Acceptance certifies {actual}, not {expected}")
    if receipt.get("verdict") not in VERDICTS:
        raise ValueError(f"Unknown acceptance verdict {receipt.get('verdict')!r}")
    if not receipt.get("summary") or not isinstance(receipt.get("findings"), list):
        raise ValueError("Acceptance carries no explanation")
    if not isinstance(receipt.get("checks"), list):
        raise ValueError("Acceptance carries no execution evidence")
    datetime.fromisoformat(str(receipt.get("timestamp", "")).replace("Z", "+00:00"))
    if receipt["verdict"] != "approve":
        return
    if not receipt["checks"] or receipt["findings"] or receipt.get("clipped") is not False:
        raise ValueError("An approval requires complete, unclipped execution evidence")
    for check in receipt["checks"]:
        if check.get("status") != "passed" or check.get("exit_code") != 0:
            raise ValueError(f"Approval contains check {check.get('id')!r} that did not pass")
        if check.get("identity") != {**expected, "repository": repo}:
            raise ValueError("Approval contains evidence about another candidate")


def snapshot(directory: Path, base: str) -> dict:
    """The trusted evaluator: this factory's own machinery, recovered from the base tree.

    NOT FROM THE CHECKOUT IN FRONT OF US, and not from the candidate. The gate that
    judges a pull request is `factory/*` and `.factory/locks/*`, both of which the
    guard refuses to let a pull request touch -- so the version on the base branch is
    the version a human last agreed to. Reading it out of the base tree makes that
    structural rather than a claim: a candidate cannot supply the judge that judges it,
    and neither can a dirty operator checkout.
    """
    trusted = directory / "trusted"
    trusted.mkdir()
    listing = git("ls-tree", "-r", "--name-only", base).splitlines()
    for name in listing:
        if Path(name).parent.as_posix() == "factory" and name.endswith(".py"):
            (trusted / Path(name).name).write_text(git("show", f"{base}:{name}") + "\n",
                                                   encoding="utf-8")
    for name in ("config.py", "guard.py", "gate.py", "tripwire.py", "fixed_gate.py",
                 "runtime.py"):
        if not (trusted / name).is_file():
            raise RuntimeError(
                f"{base} has no factory/{name}, so there is no trusted gate to evaluate "
                f"against. Land the factory runtime on {config.BASE_BRANCH} first.")
    profile = settings(trusted)
    # THE BUDGET IS REFUSED HERE, not at minute fifty-nine. A dispatch prepared with a
    # deadline the caller will not accept is a run that spends an hour and cannot be
    # judged, and one prepared with a nonsense deadline never had a gate at all.
    profile["gate_timeout_seconds"] = gate.budget(profile["gate_timeout_seconds"])
    floor = git("show", f"{base}:{profile['floor_path']}") if profile["floor_path"] in listing else ""
    profile["floor"] = json.loads(floor or "{}")
    profile.update(base_sha=base, result=str(directory / "measurements.json"))
    runtime.write(directory / "gate.json", profile)
    return profile


def evaluator(directory: Path) -> list[str]:
    return [sys.executable, str(directory / "trusted" / "fixed_gate.py"),
            str(directory / "gate.json")]


def work_order(target: str, directory: Path) -> str:
    """The ORIGINAL accepted request, recorded once and reused by every later run.

    A cold repair and an independent acceptance both judge a candidate against what was
    actually asked for, possibly weeks later and from a fresh clone. Re-reading the
    issue at that point reads whatever it says NOW -- edited, closed, retitled -- so the
    text is captured on the first dispatch that needs it and lives outside every checkout.

    THE LINE ENDINGS ARE PART OF THE EVIDENCE. Acceptance reads this file's literal
    bytes and puts their digest in the receipt, which `consume` re-checks against the
    digest recorded here. `Path.write_text` translates a newline to a carriage return
    and a newline on Windows, so a multi-line request written by this side and hashed
    from the in-memory string produced two digests for one work order and refused
    every receipt about it. Read through universal newlines and written back as LF
    bytes, the file the judge reads is byte-for-byte the file this side hashed.
    """
    issue = state.linked_issue(target) if target.startswith("gh:pr:") else target
    if not issue:
        raise ValueError(f"{target} has no linked issue, so its original request is unknown")
    saved = runtime.root() / "work-orders" / f"{issue.replace(':', '-')}.txt"
    if not saved.exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(state.body_text(issue).encode("utf-8"))
    text = saved.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"The recorded work order for {issue} is empty")
    (directory / "work-order.txt").write_bytes(text.encode("utf-8"))
    return text


def assumption_text(target: str) -> str:
    """Everything recorded as an assumption for a pull request or the issue behind it."""
    issue = state.linked_issue(target) if target.startswith("gh:pr:") else None
    parts = []
    for item in (target, issue):
        if not item:
            continue
        path = config.ASSUMPTIONS_DIR / f"{item.replace(':', '-')}.txt"
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(part for part in parts if part.strip())


def flood_allowed(target: str) -> bool:
    """FACTORY_RULES 1, applied before a triage costs anything.

    Non-owner accounts get ISSUE_CAP_PER_DAY issues per UTC day; the rest are labelled
    and re-evaluated tomorrow, so the cap is a DELAY and not a wastebasket. Yesterday's
    held issues are unstuck first, or the cap is permanent for anyone who ever tripped it.

    DEGRADES OPEN, deliberately: an API hiccup must not be able to stop every triage in
    the factory, and the label it writes is the visible record either way.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        held = json.loads(state.gh("issue", "list", "--state", "open", "--label",
                                   "factory:rate-limited", "--limit", "100",
                                   "--json", "number,createdAt") or "[]")
        for row in held:
            if row["createdAt"][:10] < today:
                state.gh("issue", "edit", str(row["number"]), "--remove-label",
                         "factory:rate-limited", check=False)
        item = state.fetch(target)
        author = (item.get("author") or {}).get("login", "")
        owner = json.loads(state.gh("repo", "view", "--json", "owner"))["owner"]["login"]
        if not author or author == owner:
            return True
        # LISTED, NOT SEARCHED. The search index lags issue creation by minutes, and a
        # cap computed from a stale index lets the flood it exists to stop straight
        # through on exactly the day somebody is running one.
        rows = json.loads(state.gh("issue", "list", "--state", "all", "--limit", "200",
                                   "--json", "number,author,createdAt") or "[]")
        mine = sorted([row for row in rows if row["createdAt"][:10] == today
                       and (row.get("author") or {}).get("login") == author],
                      key=lambda row: (row["createdAt"], row["number"]))
        position = next((i for i, row in enumerate(mine) if row["number"] == item["number"]), None)
    except Exception as error:  # noqa: BLE001
        print(f"FLOOD_CHECK_UNAVAILABLE {target}: {error}", file=sys.stderr)
        return True
    if position is None or position < config.ISSUE_CAP_PER_DAY:
        return True
    state.gh("issue", "edit", str(item["number"]), "--add-label", "factory:rate-limited",
             check=False)
    print(f"RATE_LIMITED {target}: @{author} has filed {len(mine)} issues today and the cap "
          f"for non-owner accounts is {config.ISSUE_CAP_PER_DAY} per UTC day "
          f"(FACTORY_RULES 1). It is re-evaluated after midnight UTC.")
    return False


def merge_policy(record: dict, directory: Path) -> dict:
    """The merge authorization, rewritten immediately before every read of it.

    `archon-merge` rereads this file and the stop path in the instant before it
    mutates, which is only worth anything if this side keeps it current. So the
    next complete decision replaces the last one atomically. A temporary denial
    during every GitHub lookup made the short merge workflow read a false denial
    on every poll. Lookup failures revoke the prior decision and raise. STOP is
    also checked directly by the merge, independently of this polling interval.
    """
    path = directory / "policy.json"
    try:
        operator = live()
        target = record["target"]
        allowed = (authorized("merge", False, operator["autonomy"])
                   and state.fetch(target)["_state"] == "passed"
                   and not assumption_text(target).strip())
    except Exception:
        if path.exists():
            runtime.write(path, {**runtime.read(path), "authorized": False})
        raise
    policy = {"authorized": bool(allowed), "repository": record["repository"],
              "base_branch": config.BASE_BRANCH,
              "required_checks": operator["required_checks"],
              "hold_labels": ["factory:held", "factory:needs-human", config.STOP_LABEL],
              "accept_races": operator["accept_races"],
              "stop_file": str(config.STOP_FILE)}
    runtime.write(path, policy)
    return policy


def acceptance_of(target: str) -> dict:
    """The recorded acceptance for a pull request, or a refusal naming what is missing."""
    path = runtime.root() / "acceptance" / f"{target.replace(':', '-')}.json"
    if not path.exists():
        raise ValueError(f"No acceptance has been recorded for {target}")
    return runtime.read(path)


def prepare(action: str, target: str, directory: Path, manual: bool) -> dict:
    """Everything that can refuse, before anything that can change state.

    A failure here leaves no journal, so `launch()` releases the lock and the target is
    exactly where it was. That ordering is why the eligibility checks live in this
    function and not next to the writes they guard.
    """
    repo = repository()
    record: dict = {"action": action, "target": target, "repository": repo,
                    "manual": manual, "status": "preparing", "applied": [], "run_id": None}
    base = git("rev-parse", f"origin/{config.BASE_BRANCH}")
    if action in {"validate", "fix", "merge"}:
        record["identity"] = identity(target, repo)
        base = record["identity"]["base_sha"]
    record["base_sha"] = base
    profile = snapshot(directory, base)
    gate_argv = evaluator(directory)

    if action == "triage":
        (directory / "policy.txt").write_text(
            "\n\n".join(git("show", f"{base}:{name}")
                        for name in ("MISSION.md", "FACTORY_RULES.md")), encoding="utf-8")
        (directory / "context.txt").write_bytes(state.body_text(target).encode("utf-8"))
        record["inputs"] = {"target": state.fetch(target)["url"],
                            "policy": str(directory / "policy.txt"),
                            "context": str(directory / "context.txt")}
    elif action in {"implement", "fix"}:
        # STRICT ISOLATION FAILS CLOSED. Neither the engine's worktree nor a fresh
        # context is a sandbox, and no provider this factory dispatches to attests one.
        # An operator who asked for enforced isolation gets a refusal rather than a run
        # that quietly did not have it.
        if profile["require_isolation"]:
            raise ValueError(
                "FACTORY_REQUIRE_ISOLATION is set and no dispatched provider attests an "
                "enforced execution boundary; delivery refused.")
        runtime.write(directory / "policy.json",
                      {"command": gate_argv + ["--publication"],
                       "protected_paths": PROTECTED_PREFIXES})
        order = work_order(target, directory)
        record["inputs"] = {"publication_policy": str(directory / "policy.json")}
        if action == "implement":
            current = state.fetch(target)
            if current["_state"] != "accepted":
                raise ValueError(f"{target} is '{current['_state']}', not accepted")
            # Native workflow inputs accept documents. Keep multiline requests and
            # quoted JSON out of Windows batch-launcher argument parsing.
            (directory / "target.txt").write_bytes((current["url"] + "\n\n" + order).encode("utf-8"))
            record["inputs"]["target"] = str(directory / "target.txt")
        else:
            current = state.fetch(target)
            if current["_state"] != "failed":
                raise ValueError(
                    f"{target} is '{current['_state']}', so there is nothing to repair")
            if current["_attempts"] >= config.MAX_FIX_ATTEMPTS:
                raise ValueError(
                    f"{target} has used {current['_attempts']} of {config.MAX_FIX_ATTEMPTS} "
                    f"fix attempts (FACTORY_RULES 8)")
            receipt = runtime.read(Path(acceptance_of(target)["receipt"]))
            validate_receipt(receipt, record["identity"])
            if receipt["verdict"] != "request_changes":
                raise ValueError(
                    f"The recorded acceptance for {target} is '{receipt['verdict']}'; a cold "
                    f"repair needs current public repair findings")
            runtime.write(directory / "findings.json", receipt["findings"])
            record["inputs"].update(target_pr=record["identity"]["pr"],
                                    work_order=str(directory / "work-order.txt"),
                                    findings=str(directory / "findings.json"))
    elif action == "validate":
        current = state.fetch(target)
        if current["_state"] != "open":
            raise ValueError(f"{target} is '{current['_state']}', not awaiting validation")
        work_order(target, directory)
        record["work_order_sha256"] = hashlib.sha256(
            (directory / "work-order.txt").read_bytes()).hexdigest()
        runtime.write(directory / "policy.json", {
            "schema_version": 1,
            "commands": [{"id": "factory-gate", "argv": gate_argv,
                          "environment_exit_codes": [75],
                          # THE OUTER DEADLINE OUTLASTS THE INNER ONE. The gate stops
                          # itself at its own budget and still writes the report below;
                          # a caller-side kill produces neither that report nor any
                          # output, and leaves the gate's process tree holding this
                          # checkout. So acceptance is told the budget plus the margin
                          # the gate needs to clean up after itself.
                          "timeout_seconds": gate.deadline(profile["gate_timeout_seconds"]),
                          "public_description": GATE_DESCRIPTION}],
            "gate": {"complete": True, "description": GATE_COMPLETENESS},
            "context": ACCEPT_CONTEXT,
            # THE ONE THING THE JUDGE MAY SEE OF A PRIVATE GATE'S RESULT. The streams
            # are withheld, so an approval otherwise rests on an exit code alone. The
            # gate writes this report into the candidate checkout, bound to THIS
            # evaluation, and acceptance refuses it if the path was tracked or already
            # present -- so it cannot be a file the candidate committed.
            "required_evidence": [profile["accept_report"]],
            "protected_paths": PROTECTED_PREFIXES,
            "require_isolation": profile["require_isolation"]})
        record["policy_sha256"] = hashlib.sha256(
            (directory / "policy.json").read_bytes()).hexdigest()
        record["inputs"] = {"target": f"{repo}#{record['identity']['pr']}",
                            "work_order": "file:" + str(directory / "work-order.txt"),
                            "policy": str(directory / "policy.json")}
    elif action == "merge":
        current = state.fetch(target)
        if current["_state"] != "passed":
            raise ValueError(f"{target} is '{current['_state']}'; only a passed gate authorizes a merge")
        acceptance = acceptance_of(target)
        receipt = runtime.read(Path(acceptance["receipt"]))
        validate_receipt(receipt, record["identity"])
        if receipt["verdict"] != "approve":
            raise ValueError(f"The recorded acceptance for {target} is '{receipt['verdict']}'")
        shutil.copyfile(acceptance["receipt"], directory / "acceptance.json")
        record["measurements"] = acceptance["measurements"]
        merge_policy(record, directory)
        record["inputs"] = {"target": record["identity"]["pr"],
                            "receipt": str(directory / "acceptance.json"),
                            "policy": str(directory / "policy.json")}
    elif action == "regress":
        runtime.write(directory / "policy.json",
                      {"version": 1, "argv": gate_argv + ["--regression"],
                       "timeout_seconds": gate.deadline(profile["gate_timeout_seconds"])})
        # THE PROBE IS A WORKFLOW INPUT, NOT A FIELD ON THE PROFILE. `archon-regress`
        # declares `public_probe_scope` alongside `policy` and `publish`; setting it on
        # the JSON profile instead is an unknown field, which the profile parser refuses
        # -- and, before that, a run that silently never probes at all.
        #
        # The full private gate above still runs first and still decides. This is only
        # what may be re-run, in public, when that gate comes back non-clean: a green
        # probe can never turn a failed gate green, and nothing the private check
        # produced reaches the probe's evidence, its investigation or its issue.
        record["inputs"] = {"policy": str(directory / "policy.json"),
                            "public_probe_scope": profile["public_probe_scope"],
                            "publish": profile["autonomy"] >= PUBLISH_LEVEL}
    else:
        raise ValueError(f"Unknown action {action!r}; expected one of {sorted(WORKFLOWS)}")
    return record


def launch(action: str, target: str, *, manual: bool = False, detach: bool = True) -> bool:
    """Dispatch one unit of work. Returns False when there was nothing to dispatch.

    The lock, the journal and the run id are taken in that order and each is durable
    before the next is attempted, so there is no window in which work is running and
    nothing on disk knows about it.
    """
    import dispatch

    if action not in WORKFLOWS:
        raise ValueError(f"Unknown action {action!r}; expected one of {sorted(WORKFLOWS)}")
    if not authorized(action, manual):
        raise ValueError(f"The STOP button or the dial refuses to dispatch {action}")
    if action == "triage" and not flood_allowed(target):
        return False
    lock = dispatch.lock_path(action, target)
    if not dispatch.acquire(lock):
        return False
    directory = runtime.root() / "runs" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    journal = directory / "record.json"
    try:
        record = prepare(action, target, directory, manual)
        record["lock"] = str(lock)
        runtime.write(journal, record)
        with lock.open("a", encoding="utf-8") as stream:
            stream.write(f"record {journal}\n")
        if action == "implement":
            state.set_state(target, "in-progress")
        elif action == "validate":
            state.set_state(target, "validating")
        elif action == "fix":
            state.bump_attempt(target)
        # LAST, after preparation has read GitHub and git and possibly taken a minute.
        # A STOP raised during that minute has to land, or the button only works while
        # nothing is happening.
        if not authorized(action, manual):
            raise ValueError("The STOP button or the dial changed during preparation")
        argv = ["workflow", "run", WORKFLOWS[action], "--detach", "--json"]
        if action in {"implement", "fix", "regress"}:
            argv += ["--branch", f"factory/{action}-{directory.name[:12]}",
                     "--from-branch", record["base_sha"], "--base", config.BASE_BRANCH]
        else:
            argv += ["--no-worktree"]
        for key, value in record["inputs"].items():
            argv += ["--input", f"{key}={value if isinstance(value, str) else json.dumps(value)}"]
        argv += [f"factory {action} {directory.name}"]
        record["status"] = "launching"
        runtime.write(journal, record)
        response = engine(argv)
        run_id = str(response.get("runId") or "")
        if response.get("ok") is not True or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", run_id):
            raise RuntimeError(f"The engine did not acknowledge a durable run id: {response}")
        record.update(run_id=run_id, status="running")
        runtime.write(journal, record)
        with lock.open("a", encoding="utf-8") as stream:
            stream.write(f"run {run_id}\n")
        ledger.record(ledger.DISPATCH, action=action, target=target or None,
                      workflow=WORKFLOWS[action], run=run_id)
    except Exception as error:
        if journal.exists():
            # The journal exists, so state may already have moved and a run may already
            # be going. Recording the error and KEEPING the lock is the honest half of
            # that: `reconcile()` reports it every tick until a person looks, where
            # releasing would hand the same target to the next tick underneath a run
            # nobody can name.
            runtime.write(journal, {**runtime.read(journal), "error": str(error)})
        else:
            lock.unlink(missing_ok=True)
        raise
    if not detach:
        while not consume(journal):
            time.sleep(5)
    return True


def artifact_root(run: dict, expected_id: str) -> Path:
    """Where THIS run recorded its artifacts, from the engine's own persisted root.

    `<output_root>/artifacts/runs/<id>` is the layout the engine's writer and every one
    of its readers share. Binding to the run id rather than to a filename is what stops
    a result from a previous lap being applied to this one.
    """
    if run.get("id") != expected_id:
        raise ValueError(f"The engine returned run {run.get('id')!r}, not {expected_id!r}")
    root = run.get("output_root")
    if not isinstance(root, str) or not root:
        raise ValueError(f"Run {expected_id} persisted no output root")
    return Path(root).resolve() / "artifacts" / "runs" / expected_id


def artifact(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Artifact {name!r} escapes its recorded run")
    return path


def verify_evidence(receipt: dict, root: Path) -> None:
    """Re-hash what the receipt says it saw. A digest is only evidence if somebody checks it."""
    for check in receipt["checks"]:
        for channel in ("stdout", "stderr"):
            digest = hashlib.sha256(artifact(root, check[channel]).read_bytes()).hexdigest()
            if digest != check[f"{channel}_sha256"]:
                raise ValueError(f"Check {check['id']!r} {channel} no longer matches its digest")
    evidence = artifact(root, "accept-private/evidence.json")
    if hashlib.sha256(evidence.read_bytes()).hexdigest() != receipt["evidence_sha256"]:
        raise ValueError("The acceptance evidence packet no longer matches its digest")


def effect(journal: Path, record: dict, name: str, action) -> None:
    """Perform one effect exactly once, across any number of retries."""
    if name in record["applied"]:
        return
    action()
    record["applied"].append(name)
    runtime.write(journal, record)


def comment_once(target: str, run_id: str, summary: str) -> None:
    marker = f"<!-- factory-run:{run_id} -->"
    kind, number = state.parse_target(target)
    comments = json.loads(state.gh(kind, "view", number, "--json", "comments"))["comments"]
    if not any(marker in row.get("body", "") for row in comments):
        state.comment(target, marker + "\n" + summary)


def escalate(journal: Path, record: dict, summary: str) -> None:
    """Park it in the ledger, record it, tell someone. All three, or it is not an escalation."""
    def tell() -> None:
        config.NEEDS_HUMAN.parent.mkdir(parents=True, exist_ok=True)
        with config.NEEDS_HUMAN.open("a", encoding="utf-8") as stream:
            stream.write(f"- {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  "
                         f"{record['target'] or 'regression'}  ({record['action']})  {summary}\n")
        ledger.record(ledger.ESCALATE, target=record["target"] or None, reason=summary[:300])
        import notify
        print(notify.send(record["target"] or "the scheduled regression", summary))
    effect(journal, record, "notification", tell)


def apply_triage(journal: Path, record: dict, result: dict, transition) -> None:
    disposition = result["disposition"]
    if disposition not in {"accepted", "deferred", "rejected", "needs-human"}:
        raise ValueError(f"Unknown admission disposition {disposition!r}")
    target = record["target"]
    effect(journal, record, "priority", lambda: state.set_priority(target, result["priority"]))
    if result.get("assumptions"):
        path = config.ASSUMPTIONS_DIR / f"{target.replace(':', '-')}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(result["assumptions"]) + "\n", encoding="utf-8")
    transition(target, disposition)


def apply_delivery(journal: Path, record: dict, result: dict, transition) -> None:
    """A pull request this factory can act on, or nothing.

    THE LINKAGE IS RECORDED, NOT PARSED BACK. `Closes #N` in the body is what makes the
    pull request readable and what closes the issue on merge; it is prose, an agent
    writes it, and one run put it inside backticks so GitHub ignored it entirely. The
    issue this delivery answers is written down here, by the code that knows it.
    """
    action, target = record["action"], record["target"]
    if result["outcome"] != "delivered":
        transition(target, "needs-human")
        return
    pr = result["pr"]
    if pr["repository"].lower() != record["repository"].lower():
        raise ValueError(
            f"Delivery returned a pull request in {pr['repository']}, not {record['repository']}")
    if pr["base"] != config.BASE_BRANCH:
        raise ValueError(f"Delivery returned a pull request based on {pr['base']}, "
                         f"not {config.BASE_BRANCH}")
    pr_target = f"gh:pr:{pr['number']}"
    if action == "fix" and pr_target != target:
        raise ValueError(f"A repair of {target} returned {pr_target}; a replacement is refused")
    current = identity(pr_target, record["repository"])
    if current["head_sha"] != pr["head_sha"] or current["base_sha"] != pr["base_sha"]:
        raise ValueError(f"{pr_target} moved after delivery; it must be judged as it is now")
    if action == "implement":
        effect(journal, record, "link", lambda: runtime.write(
            runtime.root() / "links" / f"{pr_target.replace(':', '-')}.json", {"issue": target}))
        effect(journal, record, "comment:link", lambda: comment_once(
            pr_target, record["run_id"],
            f"Factory issue: {target}\n\nCloses #{target.split(':')[-1]}"))
    transition(pr_target, "open")


def apply_acceptance(journal: Path, record: dict, result: dict, transition) -> str:
    """The verdict, the hold, and the measurements that back both. Returns what to post."""
    target, directory = record["target"], journal.parent
    validate_receipt(result, record["identity"])
    if identity(target, record["repository"]) != record["identity"]:
        # The pull request moved while it was being judged. The receipt is still true
        # about what it saw and is no longer true about what is there, so nothing may
        # act on it.
        transition(target, "needs-human")
        return ("The pull request moved while it was being validated, so this acceptance "
                "certifies commits that are no longer its head. It must be validated again.")
    measured = (runtime.read(directory / "measurements.json")
                if (directory / "measurements.json").exists() else None)
    verdict = result["verdict"]
    if verdict == "approve" and (measured is None or measured["errors"]):
        raise ValueError("An approval must carry passing factory gate measurements")
    runtime.write(directory / "acceptance.json", result)
    runtime.write(runtime.root() / "acceptance" / f"{target.replace(':', '-')}.json",
                  {"receipt": str(directory / "acceptance.json"), "run_id": record["run_id"],
                   "measurements": measured or {}})
    value = {"approve": "passed", "request_changes": "failed", "reject": "rejected",
             "inconclusive": "needs-human"}[verdict]
    summary = result["summary"]
    if result["findings"]:
        # These are the workflow's public repair findings, never private check logs.
        # Preserve the reason for a refusal when the receipt summary is generic.
        summary += "\n\n" + "\n".join(
            f"- {item['code']}: {item['summary']}" for item in result["findings"])
    if value == "passed":
        # HELD IS A STATE, NOT A SENTENCE IN A COMMENT. The hold used to be prose on a
        # pull request that was still labelled `passed`, and the dispatcher merged it
        # forty-five seconds later because `passed` is what a mergeable pull request is
        # called.
        holds = list(measured["holds"])
        assumptions = assumption_text(target).strip()
        if assumptions:
            holds.append(f"{len(gate.assumption_keys(assumptions))} recorded assumption(s)")
        if holds:
            value = "held"
            summary += ("\n\nMerge held: " + ", ".join(holds) + ".\n\n" + assumptions +
                        f"\n\nNothing is wrong. Read the hold, then run "
                        f"`factory accept {target}` -- the command that records who agreed.")
    transition(target, value)
    if value in {"rejected", "needs-human"}:
        issue = state.linked_issue(target)
        if issue:
            transition(issue, "needs-human")
    return summary


def apply_merge(journal: Path, record: dict, result: dict, transition) -> str:
    """What the remote actually did, kept separate from what this side still owes.

    Returns what to post, so the one refusal a person has to act on carries the
    command that clears it rather than only the merge's own description of it.

    `revalidation_required` IS TWO DIFFERENT ANSWERS and they need opposite moves. The
    merge refuses either when the pull request has moved since the receipt was issued,
    or when it has NOT moved and its head still does not contain the base. Requeueing
    was right for the first and a treadmill for the second: acceptance judges a head, it
    never updates one, so the same head is approved and refused again every lap, for as
    many laps as the operator is willing to pay for.

    THE TWO ARE TOLD APART BY ASKING GITHUB, not by reading the refusal's prose. If the
    candidate's identity is no longer the one that was accepted, the evidence changed
    and revalidation is the designed remedy. If it is exactly the one that was accepted,
    nothing this factory can dispatch will change the answer, and it stops.
    """
    target = record["target"]
    status = result["status"]
    if status not in {"merged", "held", "revalidation_required", "failed"}:
        raise ValueError(f"Unknown merge status {status!r}")
    wanted = record["identity"]
    same = (str(result.get("repository", "")).lower() == wanted["repository"].lower()
            and all(result.get(key) == wanted[key] for key in ("pr", "head_sha", "base_sha")))
    summary = result["summary"]
    if status == "merged":
        if not same:
            raise ValueError(f"A merge was reported for another candidate than {wanted}")
        # RECORDED BEFORE ANY LOCAL BOOKKEEPING. The remote merge is done and cannot be
        # undone; a ratchet commit that fails afterwards is a retry, not a lost merge.
        record["remote_merge"] = result["merge_commit"]
        runtime.write(journal, record)
        transition(target, "merged")
        issue = state.linked_issue(target)
        if issue:
            transition(issue, "done")
        import merge
        effect(journal, record, "ratchet",
               lambda: merge.bookkeeping(result, record["measurements"]))
        return summary
    if status == "revalidation_required" and not result.get("merge_commit"):
        if identity(target, record["repository"]) != wanted:
            # Somebody pushed to the branch or to its base, which on any repository
            # with velocity is Tuesday. The receipt is about commits that are no longer
            # there; revalidating the ones that are is the designed remedy.
            transition(target, "open")
            return summary
        transition(target, "needs-human")
        summary = (
            f"{target} is approved at exactly the head that was judged, and the merge "
            f"still refuses it: that head does not contain {wanted['base_sha'][:12]}, "
            f"the tip of {config.BASE_BRANCH}. Nothing the factory dispatches moves a "
            f"pull request onto its base -- acceptance judges a head, it never updates "
            f"one -- so revalidating would approve this same head and be refused again, "
            f"every lap.\n\n"
            f"Bring it forward. This is an ordinary fast-forward of the head branch and "
            f"never a force-push:\n\n"
            f"    gh pr update-branch {wanted['pr']} --repo {wanted['repository']}\n\n"
            f"then remove factory:needs-human. The head that results is a commit nobody "
            f"has judged, so it is validated from scratch.")
        escalate(journal, record, summary)
        return summary
    if status == "held" and same:
        # An operator denial, a hold label, a stopped factory, or a check that has not
        # reported yet. Nothing is wrong and nothing changed; the next tick asks again.
        return summary
    transition(target, "needs-human")
    return summary


def apply_regress(record: dict, result: dict) -> None:
    if result["status"] not in {"clean", "defects", "inconclusive"}:
        raise ValueError(f"Unknown regression status {result['status']!r}")
    if result["revision"] != record["base_sha"]:
        raise ValueError(f"The regression tested {result['revision']}, not {record['base_sha']}")


def apply(journal: Path, record: dict, result: dict) -> None:
    """Turn one recorded result into this factory's state. Idempotent, effect by effect."""
    action, target = record["action"], record["target"]

    def transition(item: str, value: str) -> None:
        effect(journal, record, f"state:{item}:{value}",
               lambda: state.set_state(item, value, force=value == "needs-human"))

    summary = result["summary"]
    if result.get("engine_failure"):
        # No workflow result to interpret. The target parks at needs-human, so nothing
        # picks it up again underneath the person reading the escalation.
        if target:
            transition(target, "needs-human")
    elif action == "triage":
        apply_triage(journal, record, result, transition)
    elif action in {"implement", "fix"}:
        apply_delivery(journal, record, result, transition)
    elif action == "validate":
        summary = apply_acceptance(journal, record, result, transition)
    elif action == "merge":
        summary = apply_merge(journal, record, result, transition)
    elif action == "regress":
        apply_regress(record, result)

    if (result.get("engine_failure")
            or action == "validate" and result["verdict"] in {"reject", "inconclusive"}
            or action in {"implement", "fix"} and result["outcome"] != "delivered"
            or action == "merge" and result["status"] == "failed"
            or action == "regress" and result["status"] != "clean"):
        escalate(journal, record, summary)
    # A held merge says nothing new about a pull request the factory already commented
    # on, and a merged one closes the conversation by merging it.
    if target and not (action == "merge" and result["status"] in {"held", "merged"}):
        effect(journal, record, "comment",
               lambda: comment_once(target, record["run_id"], summary))


def engine_failure(record: dict, run: dict, root: Path) -> dict:
    """The result of a run that ended without one. Escalates; never claims success."""
    action, run_id = record["action"], record["run_id"]
    status = str(run.get("status", "")).lower()
    error = run.get("error")
    detail = f" Engine error: {str(error)[:300]}" if error else ""
    if action in {"implement", "fix"}:
        # A repair can correctly diagnose an inherited red gate and stop before
        # publication. Its spent attempt must end at a human, not an eternal
        # missing-artifact retry.
        report = artifact(root, "implementation.md")
        return {"outcome": "blocked", "pr": None,
                "summary": f"Run {run_id} ended without a verified publication result. "
                           "Inspect the remote pull request and its implementation report "
                           "and execution trace in the Archon run artifacts before retrying."
                           + detail,
                "reports": [str(report)] if report.is_file() else []}
    return {"engine_failure": True,
            "summary": f"Run {run_id} ({WORKFLOWS[action]}) ended {status or 'unknown'} "
                       f"without writing {ARTIFACTS[action]}, so there is no result to apply. "
                       f"Inspect the run in the Archon workspace before retrying.{detail}"}


def consume(journal: Path) -> bool:
    """Settle one dispatch. True once its result is fully applied and the lock is freed.

    Reads the ONE run this journal names. The result is copied out of that run's
    artifacts on the first pass and read from the copy afterwards, so a retry of a
    partially applied result can never pick up different bytes.
    """
    record = runtime.read(journal)
    if record["status"] == "applied":
        Path(record["lock"]).unlink(missing_ok=True)
        return True
    if not record["run_id"]:
        raise RuntimeError(
            f"This dispatch never received a run id, so nothing can be asked about it. "
            f"Inspect {journal}, undo whatever it recorded under 'applied', then delete "
            f"its directory and {record.get('lock')} to release the target.")
    if record["action"] == "merge":
        # Rewritten every poll: the authorization archon-merge reads has to answer "may
        # this merge NOW", not "was it authorized when it was dispatched".
        merge_policy(record, journal.parent)
    run = engine(["workflow", "get", record["run_id"], "--json"])
    if run.get("workflow_name") != WORKFLOWS[record["action"]]:
        raise ValueError(f"Run {record['run_id']} is {run.get('workflow_name')!r}, "
                         f"not {WORKFLOWS[record['action']]!r}")
    if str(run.get("status", "")).lower() not in TERMINAL:
        return False
    result_file = journal.parent / "result.json"
    if not result_file.exists():
        root = artifact_root(run, record["run_id"])
        returned = artifact(root, ARTIFACTS[record["action"]])
        ended = str(run.get("status", "")).lower()
        if ended != "completed" and not returned.exists():
            # The engine ended this run without the result it promised. Asking again
            # returns the same missing file, so an eternal retry is not "not applied
            # yet", it is a lock held forever: one provider error wedged a factory at
            # capacity 1/1 for good (2026-09-08, a Codex binary too old for the model).
            # The spent attempt ends at a human and the target is released. Never
            # synthesize a successful result.
            result = engine_failure(record, run, root)
        else:
            result = runtime.read(returned)
        if record["action"] == "validate" and not result.get("engine_failure"):
            validate_receipt(result, record["identity"])
            for key in ("work_order_sha256", "policy_sha256"):
                if result.get(key) != record[key]:
                    raise ValueError(f"The acceptance judged a different {key[:-8]}")
            verify_evidence(result, root)
        runtime.write(result_file, result)
    # The dial can fall between dispatch and settle. Applying anyway would let a factory
    # turned down to 1 go on validating; refusing to apply a MERGE that already happened
    # would lose it, so a merge settles on the authority the engine already acted under.
    result = runtime.read(result_file)
    if (record["action"] != "merge" and not result.get("engine_failure")
            and not authorized(record["action"], record["manual"])):
        return False
    apply(journal, record, result)
    record["status"] = "applied"
    record.pop("error", None)
    runtime.write(journal, record)
    cost = (run.get("metadata") or {}).get("total_cost_usd")
    ledger.record(ledger.SETTLE, run=record["run_id"], status=run["status"],
                  target=record["target"] or record["action"],
                  cost_usd=float(cost) if cost is not None else None)
    Path(record["lock"]).unlink(missing_ok=True)
    return True


def reconcile() -> None:
    """Settle every dispatch that has not settled. Called first, every tick.

    A FAILED APPLY IS RETRIED, NOT DROPPED. The error goes on the journal and to
    stderr, the lock stays held so the target is not picked up again underneath it, and
    the next tick tries the effects that did not land. That is the difference between a
    result the factory has not applied YET and a result the factory silently lost.
    """
    for journal in sorted((runtime.root() / "runs").glob("*/record.json")):
        try:
            if runtime.read(journal)["status"] == "applied":
                continue
            consume(journal)
        except Exception as error:  # noqa: BLE001
            try:
                runtime.write(journal, {**runtime.read(journal), "error": str(error)})
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            print(f"SDLC_APPLY_PENDING {journal}: {error}", file=sys.stderr)
