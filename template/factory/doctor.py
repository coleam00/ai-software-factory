"""The deterministic audit. `factory doctor` runs this.

    python factory/doctor.py            report
    python factory/doctor.py --level 3  can this repo run at level 3?

A CHECKLIST, not a test suite. It is meant to fail loudly on a fresh install --
that is it working, and working through it is the build.

The one property that makes it worth having: IT BLOCKS THE DIAL. Level 2 needs a
real E2E; level 3 needs a holdout, a mutation set and a ratchet. So "build for
level 3" cannot quietly become "switch on level 3" before the evidence exists.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import state  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# A scaffold marker still present means the check is green about somebody else's
# product, which is worse than no check at all.
SCAFFOLD_MARKER = "SCAFFOLD_EXAMPLE_DELETE_THIS_LINE_WHEN_YOU_WRITE_YOUR_OWN"

OK, WARN, FAIL = "ok", "warn", "FAIL"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, int]] = []

    def add(self, status: str, name: str, detail: str = "", blocks_level: int = 99) -> None:
        self.rows.append((status, name, detail, blocks_level))

    def max_level(self) -> int:
        """The highest dial this repo has earned. Evidence, not intention."""
        blocked = [lvl for status, _, _, lvl in self.rows if status == FAIL]
        return (min(blocked) - 1) if blocked else 5

    def render(self, want: int | None) -> int:
        width = max(len(n) for _, n, _, _ in self.rows) + 2
        for status, name, detail, lvl in self.rows:
            mark = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}[status]
            gate = f"  (blocks level {lvl}+)" if status == FAIL and lvl < 99 else ""
            print(f"[{mark}] {name.ljust(width)}{detail}{gate}")
        ceiling = self.max_level()
        fails = sum(1 for s, _, _, _ in self.rows if s == FAIL)
        warns = sum(1 for s, _, _, _ in self.rows if s == WARN)
        print()
        print(f"{len(self.rows)} checks, {fails} failing, {warns} warnings")
        print(f"HIGHEST EARNED AUTONOMY LEVEL: {ceiling}   (configured: {config.AUTONOMY})")
        if config.AUTONOMY > ceiling:
            print()
            print(
                f"REFUSED: the dial is at {config.AUTONOMY} and the evidence supports "
                f"{ceiling}. Fix the failing checks above, or lower FACTORY_AUTONOMY. "
                f"A dial that outruns its evidence is the failure this whole system "
                f"exists to prevent."
            )
            return 1
        if want is not None and want > ceiling:
            print()
            print(f"REFUSED: level {want} needs the failing checks above to pass first.")
            return 1
        return 0


def has(path: Path) -> bool:
    return path.exists() and (path.is_dir() or path.stat().st_size > 0)


def contains_scaffold(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return SCAFFOLD_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(config.ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    return p.returncode, p.stdout.strip()


def _include_deny_supported() -> tuple[str, str, str, int]:
    """Does THIS engine honour `denied_tools` on an `include:`?

    THE FAILURE THIS EXISTS TO CATCH IS SILENT AND IT COSTS THE WHOLE ARGUMENT.
    `factory-implement` includes Archon's `sdlc` review pack, whose nodes all grant
    Read and none of which know this factory has a holdout. The include carries the
    holdout deny and the engine unions it onto every expanded node -- on an engine
    that supports it. On an older one the field is dropped, the workflow still loads,
    every check still passes, and the reviewer can read the assertions the builder is
    blocked from reading. Nothing goes red. A version string would be the easy check
    and the wrong one, because it tests what the binary CLAIMS.

    So this asks the engine what it did. Loading a workflow whose include carries an
    inert field makes Archon log `include_node_ai_fields_ignored` naming that field.
    If the warning names `denied_tools`, the deny was dropped and the wall is gone.
    Absence of the warning is the pass.

    Unknown is NOT a pass. If the probe cannot run, say so and keep blocking, because
    "we could not check, so we merged" is the same mistake as counting a skipped check
    as a passed one.
    """
    pack = config.ROOT / ".archon" / "workflows" / "factory"
    if not pack.is_dir():
        return (WARN, "include deny", "no factory pack installed yet -- nothing to probe", 99)

    # Only meaningful when this factory actually composes somebody else's block. A pack
    # whose nodes are all local has no include for the engine to strip, and reporting
    # OK there would be a check that cannot fail -- which is the shape this whole file
    # exists to refuse.
    composed = [
        y for y in pack.rglob("*.yaml")
        if y.parent.name != "fixtures"
        if "include:" in y.read_text(encoding="utf-8", errors="replace")
    ]
    if not composed:
        return (WARN, "include deny",
                "this pack includes no other workflow, so there is nothing to sandbox", 99)
    unguarded = [
        y.name for y in composed
        if "denied_tools" not in y.read_text(encoding="utf-8", errors="replace")
    ]
    if unguarded:
        return (FAIL, "include deny",
                f"{', '.join(unguarded)} includes another workflow with NO denied_tools -- "
                "every node it expands can read the holdout", 1)
    try:
        p = subprocess.run(
            [config.ARCHON_BIN, "validate", "workflows"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=str(config.ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return (FAIL, "include deny",
                "could not run `archon validate workflows` -- UNKNOWN is not a pass, and "
                "this is the check that proves the holdout survives composition", 1)

    blob = (p.stdout or "") + (p.stderr or "")
    for line in blob.splitlines():
        if "include_node_ai_fields_ignored" in line and "denied_tools" in line:
            return (FAIL, "include deny",
                    "this Archon DROPS denied_tools on an include -- the review pack would "
                    "run with no holdout deny and every check would still pass. NO RELEASED "
                    "ARCHON HAS THIS YET: it is Archon branch `feat/include-tool-policy`, "
                    "which adds the field to the include directive and unions it onto every "
                    "expanded node. Build Archon from that branch, or drop the four "
                    "`include:` nodes in .archon/workflows/factory/ and write local prompts "
                    "that carry their own denied_tools", 1)
    if "factory-implement" not in blob:
        return (FAIL, "include deny",
                "`archon validate workflows` never mentioned factory-implement, so the probe "
                "proved nothing -- UNKNOWN is not a pass", 1)
    return (OK, "include deny", "the engine keeps denied_tools on an include (holdout survives)")


# A backticked token in the rules that names a FILE rather than a symbol. `is_on` and
# `guard.py` appear in that prose as identifiers, not as paths, so the test is
# deliberately narrow: a separator, a glob, or a name that actually exists at the root.
def _looks_like_path(token: str, root: Path) -> bool:
    if not token or " " in token or "\n" in token:
        return False
    if "/" in token or "*" in token or "?" in token:
        return True
    return (root / token).exists()


def _sample_path(pattern: str) -> str:
    """A concrete path the pattern would match, so a PATTERN can be tested against
    `guard.matches`, which takes paths. `deploy/**` becomes `deploy/a/b`, which is
    matched by guard's own `deploy/**` and by nothing that does not cover it."""
    p = pattern.replace("**", "\x00").replace("*", "a").replace("\x00", "a/b")
    return p[:-1] + "a" if p.endswith("/") else p


def _rules_match_guard() -> tuple[str, str, str, int]:
    """FACTORY_RULES section 5 and the guard's list are ONE FACT WRITTEN TWICE.

    Section 5 is prose: it is what the planner reads before it plans and what the
    judge reads before it judges. `guard.PROTECTED` is the only one of the two that
    can stop a commit. When they disagree, the gate prints PROTECTED_OK on a change
    the rulebook forbids -- and the disagreement is invisible from either side.

    NOT HYPOTHETICAL, and it was not caught by a check. On flagpole, section 5 named
    `app/rollout.py` as a security invariant while the guard's project list was still
    entirely commented out. `archon-plan` refused an issue over it. A planner reading
    the prose is the wrong last line of defence, so this makes it mechanical.

    Only tokens the rules give as real paths are checked. Placeholder spans -- the
    shipped `<Dockerfiles, deploy/, ...>` an operator has not filled in yet -- are
    dropped, because an unfilled template is an incomplete setup and not a
    contradiction, and reporting it as one trains people to ignore the check.

    A PATH IS CLAIMED ON ITS CATEGORY'S ENTRY LINE -- the `**Label:** ...` block, up to
    the first blank line. Everything after that blank line is commentary and is NOT
    read as a claim. That rule is not parser convenience: section 5 is also where you
    explain what is deliberately NOT protected and why, and the first version of this
    check read flagpole's "`app/server.py` is deliberately NOT protected" paragraph as
    a demand that server.py be protected. A check that punishes writing down the
    reasoning is a check that deletes the reasoning.
    """
    rules = config.ROOT / "FACTORY_RULES.md"
    if not rules.is_file():
        return (WARN, "rules vs guard", "no FACTORY_RULES.md to cross-check", 99)
    text = rules.read_text(encoding="utf-8", errors="replace")
    section = re.search(r"^##\s*5\.[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    if not section:
        return (WARN, "rules vs guard",
                "FACTORY_RULES.md has no section 5 to cross-check", 99)
    body = re.sub(r"<[^<>]*>", " ", section.group(1), flags=re.S)
    claims = " ".join(re.findall(r"^\*\*[^*\n]+\*\*.*?(?=\n\s*\n|\Z)", body, re.S | re.M))

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import guard  # noqa: PLC0415

    uncovered = []
    for token in re.findall(r"`([^`\n]+)`", claims):
        token = token.strip().strip(",")
        if not _looks_like_path(token, config.ROOT):
            continue
        if not guard.matches(_sample_path(token), guard.PROTECTED):
            uncovered.append(token)
    if uncovered:
        return (FAIL, "rules vs guard",
                f"FACTORY_RULES section 5 protects {', '.join(sorted(set(uncovered)))} "
                "and the guard does not -- add them to config.PROTECTED_EXTRA, or stop "
                "claiming them in the rules. Prose the gate cannot enforce is not a gate",
                3)
    return (OK, "rules vs guard", "every path section 5 protects is on the guard's list")


def _fixtures_pass() -> tuple[str, str, str, int]:
    """Run the pack's dry-run fixtures. No provider call, no GitHub call, ~2 seconds.

    THIS IS ARCHON'S OWN TEST HARNESS AND THE FACTORY SHIPPED WITHOUT USING IT. The
    sdlc pack carries 48 `fixtures/*.stubs.yaml` files; this pack carried none, so
    every wiring failure in it was found the expensive way -- by dispatching a lap,
    paying for a premium plan node, and reading a log.

    What a fixture executes is the REAL DAG with the AI nodes stubbed: `when:`
    conditions, `trigger_rule`s, `if_skipped` defaults, cancel nodes, and the
    namespaced nodes an `include:` expands into. That is precisely the layer where
    composing somebody else's workflow goes wrong, and none of it is visible by
    reading the YAML.

    A fixture also asserts what must NOT happen. A node absent from the stubs must be
    skipped, because an unstubbed node that RAN fails the fixture -- so "the refusal
    stopped the lap" and "the docs lens stayed off" are expressible as omissions.

    Blocks level 1, the level at which workflows start dispatching unattended.
    """
    pack = config.ROOT / ".archon" / "workflows" / "factory"
    if not pack.is_dir():
        return (WARN, "workflow fixtures", "no factory pack installed yet", 99)
    workflows = sorted(y.parent for y in pack.rglob("*.yaml") if y.parent.name != "fixtures")
    bare = [w.name for w in workflows if not any((w / "fixtures").glob("*.stubs.yaml"))]
    if bare:
        return (FAIL, "workflow fixtures",
                f"{', '.join(sorted(bare))} has no fixtures/*.stubs.yaml -- its wiring is "
                "only ever exercised by a real dispatch, which is the expensive way to "
                "find a binding that was never going to resolve", 1)
    try:
        p = subprocess.run(
            [config.ARCHON_BIN, "workflow", "test", "factory"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, cwd=str(config.ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return (FAIL, "workflow fixtures",
                "could not run `archon workflow test factory` -- UNKNOWN is not a pass", 1)
    blob = (p.stdout or "") + (p.stderr or "")
    tally = re.search(r"(\d+) passed, (\d+) failed", blob)
    if p.returncode != 0 or not tally or tally.group(2) != "0":
        detail = tally.group(0) if tally else "the runner reported no tally"
        return (FAIL, "workflow fixtures",
                f"{detail} -- run `{config.ARCHON_BIN} workflow test factory` to see which", 1)
    return (OK, "workflow fixtures", f"{tally.group(1)} pass, no provider or GitHub calls")


def main(argv: list[str]) -> int:
    want = None
    if "--level" in argv:
        want = int(argv[argv.index("--level") + 1])

    r = Report()
    root = config.ROOT

    # --- the engine ----------------------------------------------------------
    if shutil.which(config.ARCHON_BIN):
        rc, out = 0, ""
        try:
            p = subprocess.run(
                [config.ARCHON_BIN, "version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            out = (p.stdout or "").splitlines()[0] if p.stdout else ""
        except (OSError, subprocess.SubprocessError, IndexError):
            out = ""
        r.add(OK, "archon", out or "installed")
        r.add(*_include_deny_supported())
        r.add(*_fixtures_pass())
    else:
        r.add(FAIL, "archon", f"{config.ARCHON_BIN} not on PATH -- run `factory init`", 1)

    if shutil.which("gh"):
        rc, _ = 0, ""
        p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=60)
        r.add(OK if p.returncode == 0 else FAIL, "gh authenticated",
              "" if p.returncode == 0 else "run `gh auth login`", 1)
    else:
        r.add(FAIL, "gh", "not on PATH -- the state machine, the gate and the merge all use it", 1)

    rc, remote = git("remote", "get-url", "origin")
    r.add(OK if rc == 0 else FAIL, "origin remote", remote if rc == 0 else "none -- labels are the state machine", 1)

    # --- the guidance layer --------------------------------------------------
    for name in ("MISSION.md", "FACTORY_RULES.md"):
        p = root / name
        if not has(p):
            r.add(FAIL, name, "missing", 1)
        elif "<" in p.read_text(encoding="utf-8", errors="replace") and re.search(
            r"<[A-Z][A-Za-z -]{3,}>", p.read_text(encoding="utf-8", errors="replace")
        ):
            r.add(FAIL, name, "still contains <PLACEHOLDER> text", 1)
        else:
            r.add(OK, name, f"{len(p.read_text(encoding='utf-8', errors='replace').splitlines())} lines")

    conventions = next((root / n for n in ("CLAUDE.md", "AGENTS.md") if has(root / n)), None)
    r.add(OK if conventions else WARN, "conventions file",
          conventions.name if conventions else "no CLAUDE.md / AGENTS.md")

    mission = (root / "MISSION.md").read_text(encoding="utf-8", errors="replace") if has(root / "MISSION.md") else ""
    # NUMBERED LISTS COUNT. This matched only `-` and `*` bullets, and both real
    # MISSION files written against this scaffold use a NUMBERED list -- which is the
    # natural choice, because the rest of the document refers to "out-of-scope item 7".
    # So a mission with seven carefully argued exclusions was reported as "0 entries --
    # fewer than five is too thin", which is the opposite of true and trains the reader
    # to ignore the warning.
    # ANCHOR ON THE HEADING, not on the phrase.
    #
    # This split on every occurrence of "Out of scope" and took the LAST one. The phrase
    # is ordinary English and appears in prose: a real MISSION said "Out of scope for
    # this slice, but the feedback set names a sound event" under Open questions, so the
    # segment read was the tail of THAT section and the count came back 2 for a list of
    # nine. Same shape as the assumption counter that counted lines: the check was
    # correct about the wrong text.
    #
    # A heading is the only unambiguous marker of where the list starts, and the next
    # heading of any level is where it ends.
    _m = re.search(r"^#{1,6}\s+.*out of scope.*$", mission, re.M | re.I)
    _oos = re.split(r"^#{1,6}\s", mission[_m.end():], maxsplit=1, flags=re.M)[0] if _m else ""
    out_of_scope = len(re.findall(r"^\s*(?:[-*]|\d+[.)])\s+\S", _oos, re.M))
    if out_of_scope >= 5:
        r.add(OK, "out-of-scope list", f"{out_of_scope} entries")
    else:
        r.add(
            WARN, "out-of-scope list",
            f"{out_of_scope} entries -- fewer than five is too thin. Without it every "
            f"plausible feature request is arguably in scope, and the factory builds all of them",
        )

    # --- the harness ---------------------------------------------------------
    e2e = config.E2E_FILE
    e2eName = e2e.name if not e2e.is_relative_to(root) else str(e2e.relative_to(root)).replace("\\", "/")
    if not has(e2e):
        r.add(FAIL, e2eName, "missing -- there is no end-to-end path", 2)
    elif contains_scaffold(e2e):
        r.add(FAIL, e2eName, "still the scaffold's example journey", 2)
    else:
        r.add(OK, e2eName, "yours")

    holdout = config.HOLDOUT_FILE
    holdName = holdout.name if not holdout.is_relative_to(root) else str(holdout.relative_to(root)).replace("\\", "/")
    if not has(holdout):
        r.add(
            FAIL, "holdout", f"no {holdName} -- NOTHING sits above the "
            "independence line, so every check is one the builder could read and "
            "iterate against", 3,
        )
    elif contains_scaffold(holdout):
        r.add(FAIL, "holdout", "still the scaffold's example scenarios", 3)
    else:
        r.add(OK, "holdout", f"yours ({holdName})")

    # WHO DRIVES THE JOURNEYS. Both END-TO-END.md and HOLDOUT.md are markdown read
    # by a coding agent, so an unset command means those two rungs cannot run at
    # all. It is reported as a FAILURE rather than a warning for the same reason
    # agentcheck.py raises instead of skipping: a gate silently missing its
    # end-to-end rung reports green having never touched the app.
    hcfg = root / "harness" / "harness.config.json"
    agent_cmd = ""
    if hcfg.exists():
        try:
            agent_cmd = (
                json.loads(hcfg.read_text(encoding="utf-8")).get("agent", {}).get("cmd") or ""
            ).strip()
        except (OSError, ValueError):
            agent_cmd = ""
    if agent_cmd:
        exe = shlex.split(agent_cmd, posix=os.name != "nt")[0] if agent_cmd else ""
        if shutil.which(exe):
            r.add(OK, "journey agent", agent_cmd)
        else:
            # ON PATH IS THE CLAIM THAT MATTERS. A configured command that does not
            # exist fails at gate time, in an unattended run, as a harness error --
            # and it is free to catch here instead.
            r.add(FAIL, "journey agent", f"`{exe}` is configured but not on PATH", 2)
    else:
        r.add(
            FAIL, "journey agent",
            "no agent.cmd in harness/harness.config.json -- the end-to-end and holdout "
            "rungs are driven by a coding agent and cannot run without one", 2,
        )

    defects = root / "harness" / "mutations" / "defects.json"
    if not has(defects):
        r.add(FAIL, "mutation set", "no defects.json -- this gate has never been shown to fail", 3)
    else:
        try:
            spec = json.loads(defects.read_text(encoding="utf-8"))
            n = len(spec.get("defects", []))
        except (OSError, ValueError):
            n = 0
        # TWO DIFFERENT PROBLEMS, and they had one message between them. A repo with
        # eight real defects that had merely kept the scaffold's `_scaffold` marker was
        # told "8 defects -- a gate that has never failed is a gate nobody has tested",
        # which contradicts itself in one sentence and names neither cause.
        if n == 0:
            r.add(FAIL, "mutation set",
                  "0 defects -- a gate that has never failed is a gate nobody has tested", 3)
        elif contains_scaffold(defects):
            r.add(FAIL, "mutation set",
                  f"{n} defects, but the `_scaffold` marker line is still in defects.json. "
                  f"Delete it once these are YOUR defects -- it is there so a set nobody "
                  f"has replaced cannot be mistaken for one somebody wrote", 3)
        elif n < 5:
            r.add(WARN, "mutation set", f"{n} defects -- six or seven is a real set")
        else:
            r.add(OK, "mutation set", f"{n} defects")

    floor = config.FLOOR_FILE
    if not has(floor):
        r.add(WARN, "ratchet floor", "no .factory/locks/floor.json -- counts are reported but not enforced")
    else:
        try:
            keys = {k: v for k, v in json.loads(floor.read_text(encoding="utf-8")).items()
                    if isinstance(v, int) and not k.startswith("_")}
        except (OSError, ValueError):
            keys = {}
        live = {k: v for k, v in keys.items() if v > 0}
        if not live:
            r.add(FAIL, "ratchet floor", "every floor is 0 -- coverage can silently fall to nothing", 3)
        else:
            r.add(OK, "ratchet floor", " ".join(f"{k}={v}" for k, v in live.items()))

    # --- gates that must be code ---------------------------------------------
    for name, path, level in (
        ("gate is code", root / "factory" / "gate.py", 3),
        ("merge is code", root / "factory" / "merge.py", 3),
        ("guard is code", root / "factory" / "guard.py", 1),
        ("tripwire", root / "factory" / "tripwire.py", 3),
    ):
        r.add(OK if has(path) else FAIL, name, "" if has(path) else "missing", level)

    r.add(*_rules_match_guard())

    if config.MARKER_APP_RAN in config.REQUIRED_MARKERS and config.MARKER_E2E in config.REQUIRED_MARKERS:
        r.add(OK, "required markers", " ".join(config.REQUIRED_MARKERS))
    else:
        r.add(
            FAIL, "required markers",
            f"{config.MARKER_APP_RAN} and {config.MARKER_E2E} must both be required -- something "
            f"has to prove the application RAN and something has to prove an end-to-end journey "
            f"was asserted. Rename them with FACTORY_MARKER_APP_RAN / FACTORY_MARKER_E2E if your "
            f"harness calls them something else; do not drop them", 2,
        )

    # --- secrets -------------------------------------------------------------
    unignored = []
    for name in config.SECRET_FILES:
        rc, _ = git("check-ignore", "-q", name)
        if rc != 0:
            unignored.append(name)
    if unignored:
        r.add(
            FAIL, "secrets ignored",
            "NOT gitignored: " + " ".join(unignored) + " -- a commit step would publish these", 1,
        )
    else:
        r.add(OK, "secrets ignored", f"{len(config.SECRET_FILES)} patterns")

    rc, _ = git("check-ignore", "-q", ".factory/runs")
    r.add(OK if rc == 0 else WARN, ".factory/runs ignored",
          "" if rc == 0 else "builder artifacts would be committed")

    # --- the workflow pack ---------------------------------------------------
    pack = root / ".archon" / "workflows" / "factory"
    # NOT the fixtures/ beside them: a dry-run fixture is YAML about a workflow, not
    # a workflow, and counting them reported nine in a five-workflow pack.
    found = sorted(p.stem for p in pack.rglob("*.yaml")
                   if p.parent.name != "fixtures") if pack.exists() else []
    expected = {
        "factory-triage", "factory-implement", "factory-validate",
        "factory-fix", "factory-regress",
    }
    missing_wf = sorted(expected - set(found))
    r.add(OK if not missing_wf else FAIL, "workflow pack",
          f"{len(found)} workflows" if not missing_wf else "missing: " + " ".join(missing_wf), 1)

    # --- the factory's own machinery -----------------------------------------
    # Everything else in this audit checks what the factory was given. This checks
    # what the factory IS. A dispatcher that mis-decides which laps are alive
    # produces a repository that looks exactly like a quiet one.
    st = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "_selftest.py"), "--quiet"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    marker = (st.stdout or "").strip().splitlines()[-1] if (st.stdout or "").strip() else ""
    if marker.startswith("SELFTEST_PASSED"):
        r.add(OK, "machinery self-test", marker.split("checks=")[-1] + " invariants hold")
    else:
        r.add(
            FAIL, "machinery self-test",
            (marker or "did not run") + " -- run `python factory/_selftest.py` for the list. "
            "The parts that decide what is alive, what passed, and what may move are "
            "not behaving as written",
            1,
        )

    # --- the escalation channel ----------------------------------------------
    if config.NOTIFY_CMD:
        r.add(OK, "escalation channel", config.NOTIFY_CMD[:60])
    else:
        r.add(
            FAIL, "escalation channel",
            "FACTORY_NOTIFY_CMD unset -- needs-human would wait in a file nobody opens. "
            "Unattended then quietly means unmonitored", 3,
        )

    # --- deployment, and whether its gate can fail ----------------------------
    if not config.DEPLOY_CMD:
        r.add(WARN, "deployment",
              "FACTORY_DEPLOY_CMD unset -- merging is where this stops, so this is a "
              "PR generator with very good gates")
    elif not config.HEALTH_CMD:
        r.add(FAIL, "deployment", "a deploy command with no health command: "
              "nothing stands between a merge and a user", 3)
    elif not config.HEALTH_MARKERS:
        r.add(FAIL, "deployment",
              "FACTORY_HEALTH_MARKERS is empty, so the health check asserts only an "
              "exit code -- and a process that starts, does nothing and returns zero "
              "passes that", 3)
    else:
        r.add(OK, "deployment", f"health asserts {len(config.HEALTH_MARKERS)} marker(s)")

    # --- is this checkout behind what the factory already merged? -------------
    # THE FACTORY COMMITS TO THE SAME REPOSITORY YOU WORK IN, and it does so while you
    # are not looking -- that is the entire product. So your working tree goes stale in
    # a way it never does on a normal project, and the ordinary habit that pairs with
    # that is `git add -A`.
    #
    # Measured here, on this factory: a human commit made 74 seconds after an unattended
    # merge staged the pre-merge files back over it and wiped the feature and all 106
    # lines of its tests. The push succeeded. Nothing failed.
    #
    # The gates never see it, because the gates gate PULL REQUESTS. The ratchet would
    # have caught it -- 50 observed against a floor of 56 -- but not until the next
    # validation or the weekly regression, which is days of a green-looking repository
    # with a feature silently removed.
    rc_f, fetch_out = git("fetch", "--quiet", "origin", config.BASE_BRANCH)
    rc_b, behind = git("rev-list", "--count", f"HEAD..origin/{config.BASE_BRANCH}")
    if rc_f != 0 or rc_b != 0 or not behind.strip().isdigit():
        # UNANSWERED IS NOT CURRENT. The first version fell through to the OK branch
        # whenever the fetch failed -- so being offline, or having no remote, printed
        # "level with origin/main" about a tree it had not compared to anything. That
        # is the same empty-is-not-pass failure this file exists to check for, in the
        # check itself, written the same afternoon.
        r.add(WARN, "checkout freshness",
              "could not compare with origin (" + (fetch_out.strip().splitlines()[-1]
              if fetch_out.strip() else "no output") + ") -- unknown, not current")
    elif int(behind.strip()) > 0:
        r.add(
            FAIL, "checkout is stale",
            f"{behind.strip()} commit(s) behind origin/{config.BASE_BRANCH} -- the factory "
            f"has merged work this tree does not have. `git add -A` here commits the "
            f"old files back over it, and nothing will fail",
            1,
        )
    else:
        r.add(OK, "checkout is current", f"level with origin/{config.BASE_BRANCH}")
        
        # SLACK IS NO LONGER A BRAKE, so it has to be a REPORT. merge.py closes the gap
        # on every merge; a gap that survives means a raise did not land (most often a
        # push that failed), and nothing else would say so now that the gate does not
        # hold on it. Silent slack is exactly what the ratchet was built to prevent.
        try:
            _floor = json.loads((config.SHARED / '.factory/locks/floor.json')
                               .read_text(encoding='utf-8'))
            _stale = [k for k, v in _floor.items()
                      if isinstance(v, int) and not k.startswith('_') and not k.endswith('_MAX')]
            r.add(OK, 'ratchet floors', f'{len(_stale)} tracked, closed automatically on merge')
            _ceiling = _floor.get('UNCALIBRATED_MAX')
            if _ceiling is None:
                r.add(WARN, 'uncalibrated ceiling',
                      'UNCALIBRATED_MAX absent, so a change that introduces a threshold '
                      'nobody set would merge unremarked', 1)
            else:
                r.add(OK, 'uncalibrated ceiling',
                      f'{_ceiling} margins may go uncalibrated; a change that adds one holds')
        except Exception as _e:  # noqa: BLE001
            r.add(WARN, 'ratchet floors', f'could not be read: {_e}')

    # --- holds waiting on a person -------------------------------------------
    # A hold is neither a failure nor an escalation, so nothing else in this audit
    # would ever mention it -- which makes it the one outcome that can sit unnoticed
    # while the factory reports itself perfectly healthy.
    try:
        held_prs = [p_["_target"] for p_ in state._list("prs", "held")]
    except Exception:  # noqa: BLE001
        held_prs = []
    if held_prs:
        r.add(WARN, "held for you",
              " ".join(held_prs) + " -- green and waiting for you to agree "
              "(`factory accept <target>`)")

    # --- the stop button ------------------------------------------------------
    r.add(OK, "stop button", f"{config.STOP_FILE.name} + the {config.STOP_LABEL} label")

    # --- the trigger ----------------------------------------------------------
    # A fully built factory with nothing scheduled audits identically to a running
    # one, so this is the check that tells them apart.
    armed = config.TRIGGER_FILE.exists()
    if config.AUTONOMY >= 1 and not armed:
        r.add(WARN, "trigger armed", "nothing scheduled -- the factory only runs when you run it")
    else:
        r.add(OK, "trigger armed", "scheduled" if armed else "not needed at level 0")

    return r.render(want)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
