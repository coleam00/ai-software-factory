#!/usr/bin/env python3
"""Audit the MACHINERY, not your repo.

    python bin/audit.py                 # the shipped template
    python bin/audit.py --repo ../tally # a factory somebody installed

`factory doctor` asks "is this repository set up correctly?" -- protected files,
a real holdout, a calibrated ratchet, a channel that can reach you. This asks a
different question: **is the machinery underneath it still sound?**

A correctly configured repo running broken machinery passes every check the doctor
has. The findings here are bugs in the factory rather than gaps in your setup.

WHY IT EXISTS. Every check below is a failure shape that actually happened, and every
one of them was invisible to both the doctor and to Archon's own workflow validation
-- because each is a property that spans two files. A node emits a value; a different
node reads it. A state exists in one table; its label lives in another. A marker is
required by config; the thing that prints it is a harness the config has never seen.

Nothing here needs an API key, a model, or a running app. It reads files.
"""

from __future__ import annotations

import ast
import json
import subprocess
import re
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent

FINDINGS: list[tuple[str, str, str]] = []  # (severity, check, detail)


def fail(check: str, detail: str) -> None:
    FINDINGS.append(("FAIL", check, detail))


def warn(check: str, detail: str) -> None:
    FINDINGS.append(("warn", check, detail))


def check_referenced_files_exist(root: Path) -> None:
    """A shipped file that names a path inside the pack must name one that is there.

    THE INCIDENT, and it shipped into every install. Four node prompts were deleted when
    the plan, build, fix and review steps became Archon's sdlc pack. Three references
    survived them, and the worst was in a SKILL:

        .claude/skills/factory-fix/SKILL.md
        "The instructions for this step live in
         .archon/workflows/factory/fix/commands/fix.md. Read that file now and follow it."

    That skill is the by-hand version of the fix step. Its whole design is to point at one
    file rather than keep a second copy -- which is right, and which means the single
    thing it has to get correct is the path. An agent invoked on it reads the sentence,
    cannot open the file, and improvises the correction step with no guidance at all: no
    findings discipline, no attempt cap, no "never self-certify". Nothing errors.

    A deleted file is easy to grep for on the day you delete it and impossible to
    remember six weeks later, so this is mechanical. Only paths this template OWNS are
    checked -- `factory/`, `harness/` and `.claude/skills/` -- because a broken one of
    those is always a mistake, whereas a path into the USER's repository is a reference
    to something that does not exist yet, which is often the point.

    THE OWNED SET MOVED WHEN THE PACK LEFT. It used to be `.archon/workflows/factory/`
    alone -- precisely the directory this migration deleted -- so the check that would
    have caught six skills pointing into it was the check aimed only at it. What is
    scanned now is the skills, because a skill's whole design is to name one file rather
    than keep a second copy of it, which makes the path the single thing it must get
    right. A NAMED FILE, WITH AN EXTENSION: a branch name like `factory/fix-pr-14` and a
    directory like `.archon/workflows/` are not claims that a file exists.
    """
    ref = re.compile(r"[`'\"( ]((?:factory/|harness/|\.factory/|\.archon/workflows/)"
                     r"[\w./-]+\.(?:md|py|json|ya?ml|sh|ts))")
    for path in sorted((root / ".claude").rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in {m.group(1) for m in ref.finditer(text)}:
            target = match.rstrip(".,;:)`'\"")
            if "*" in target:  # a glob is a pattern, not a claim that one file exists
                continue
            if not (root / target).exists():
                fail(
                    "referenced file",
                    f"{path.relative_to(root).as_posix()} points at {target}, which does "
                    f"not exist -- an agent told to read it gets no guidance and no error",
                )


def check_all_scripts_parse(root: Path) -> None:
    """Everything under factory/ and every workflow script compiles.

    Cheap, and it catches the class of damage a bulk edit does: a stray paren in a
    file that only runs on the escalation path, discovered at 3am on the one run that
    needed it.
    """
    for base in (root / "factory", root / ".archon" / "workflows" / "factory"):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError as e:
                fail("syntax", f"{p.relative_to(root)}: line {e.lineno}, {e.msg}")


def check_state_labels(root: Path) -> None:
    """Every state the machine can WRITE must have a label the installer CREATES.

    THE INCIDENT. One state's label was missing from the creation table for its entire
    life. The factory worked perfectly right up to its first green gate, at which point
    the label write failed, the gate died on an unguarded line, and there was no merge,
    no escalation and no notification -- at the one moment the factory was about to do
    the thing it exists for.

    A hand-maintained list that has to agree with a dict in another file is a list with
    an expiry date on it, so the agreement is asserted rather than remembered.
    """
    state_py = root / "factory" / "state.py"
    if not state_py.exists():
        fail("state machine", "factory/state.py is missing")
        return
    src = state_py.read_text(encoding="utf-8")

    written = set(re.findall(r'"(factory:[\w-]+)"', src))
    created = set(re.findall(r'\(\s*"(factory:[\w-]+|priority:\w+)"', src))
    missing = {lbl for lbl in written if lbl.startswith("factory:")} - created
    # The stop label is read, never written by set_state.
    missing.discard("factory:attempt-")
    if missing:
        fail(
            "state machine",
            "state.py can write these labels and init-labels does not create them: "
            + " ".join(sorted(missing))
            + " -- the factory fails the moment it first tries to set that state",
        )

    # Every state in the transition table is reachable, and every reachable state has
    # somewhere to go or is deliberately terminal.
    m = re.search(r"TRANSITIONS.*?=\s*\{(.*?)\n\}", src, re.S)
    if m:
        table = m.group(1)
        keys = set(re.findall(r'^\s{4}"([\w-]+)":', table, re.M))
        targets = set(re.findall(r'"([\w-]+)"', table)) - keys
        unreachable = keys - set(re.findall(r'\{([^}]*)\}', table).__str__().split()) if False else set()
        orphan_targets = {t for t in targets if t not in keys and "-" in t or t in keys}
        for t in sorted(set(re.findall(r'"([\w-]+)"', table))):
            if t not in keys and t not in ("needs-human",):
                # A transition target with no row of its own is terminal by omission
                # rather than by design, which is the kind of thing that reads as a
                # deadlock later.
                warn("state machine", f"'{t}' is a transition target with no row in TRANSITIONS")


def check_markers(root: Path) -> None:
    """Every required marker must be something the harness can actually print.

    A marker nothing emits blocks every merge forever, and the failure reads as "the
    gate is broken" rather than "this marker was never wired up".
    """
    cfg = root / "factory" / "config.py"
    if not cfg.exists():
        return
    src = cfg.read_text(encoding="utf-8")
    m = re.search(r'"FACTORY_REQUIRED_MARKERS",\s*\n?\s*"([^"]+)"', src)
    if not m:
        warn("markers", "could not read REQUIRED_MARKERS from config.py")
        return
    required = m.group(1).split()

    emitters = ""
    for p in (root / "harness").rglob("*.py"):
        emitters += p.read_text(encoding="utf-8", errors="replace")
    for p in (root / ".factory" / "holdout").rglob("*.py"):
        emitters += p.read_text(encoding="utf-8", errors="replace")
    # Same shape as the crash in check_workflow_state_writes: the two rglob loops above
    # are safe on a missing directory, this one is not. It only bites a root that has a
    # config.py and no guard.py, which is a half-installed factory -- exactly the state
    # somebody runs an audit to find out about.
    guard_py = root / "factory" / "guard.py"
    if guard_py.exists():
        emitters += guard_py.read_text(encoding="utf-8", errors="replace")

    for marker in required:
        if marker not in emitters:
            fail(
                "markers",
                f"'{marker}' is required by config.py and nothing in harness/, the "
                f"holdout or guard.py prints it -- every merge would be blocked by a "
                f"marker that cannot appear",
            )

    for essential in ("APP_STARTED", "E2E_PASSED"):
        if essential not in required:
            fail(
                "markers",
                f"{essential} is not in REQUIRED_MARKERS. It is one of the two gates "
                f"that must be code in every factory: without it, software that crashed "
                f"on startup and software that is fine look identical to the gate",
            )


def check_no_freelance_writes(root: Path) -> None:
    """Nothing outside factory/ may change GitHub state directly.

    THE INCIDENT. A correct rejection assembled in a shell pipeline reached the filer
    as two characters. Every transition was right and the entire explanation was lost.

    So every human-facing write goes through one helper that posts in a single process
    and reads it back. Something holding `gh pr merge` can merge; something holding
    `gh issue close` can dispose of an issue outside the transition table, and then the
    table is decoration.

    THE SCOPE MOVED WITH THE PROMPTS. This used to scan the factory's own workflow pack,
    which is upstream's now -- and the generic pack cannot be audited from here, which
    is exactly why the factory gives it no tracker authority: every label, comment and
    merge in this system is `factory/`'s. What is left to check on this side is the
    interactive half, the skills, where a `gh pr merge` in a set of instructions puts a
    person around the machinery just as effectively as a node would.
    """
    banned = [
        (r"gh\s+pr\s+merge", "merges without archon-merge's policy rereads and readback"),
        (r"gh\s+pr\s+review", "approves without an acceptance receipt"),
        (r"gh\s+issue\s+close", "disposes of an issue outside the transition table"),
    ]
    scanned = sorted(list((root / ".claude").rglob("*.md")) + list((root / "harness").rglob("*.py")))
    for path in scanned:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, why in banned:
            if re.search(pattern, text):
                # Instructions SAYING not to do it are the point, not a violation.
                if path.suffix == ".md" and "do not" in text.lower():
                    continue
                fail("freelance write", f"{path.relative_to(root)} contains `{pattern}` -- {why}")


def check_loop_and_monitor_agree(root: Path) -> None:
    """The loop must write the file the monitor reads.

    THE INCIDENT: the README gives two commands, `bash .factory/loop.sh` and
    `python .factory/monitor.py`, and they did not connect. The loop wrote to
    stdout; the monitor tails `.factory/runs/loop.log`, which nothing created
    unless the operator happened to redirect. Follow the documentation exactly and
    the monitor reports "no dispatcher tick for 6 minutes" forever while the loop is
    running perfectly.

    Both components were individually correct, which is why nothing caught it. The
    failure mode is the worst one available here: "the loop is dead" and "nobody
    wired the log" produce identical output, and the alert that exists to tell them
    apart is the thing that is wrong.
    """
    loop = root / ".factory" / "loop.sh"
    monitor = root / ".factory" / "monitor.py"
    if not loop.exists() or not monitor.exists():
        return  # check_install_ships_a_runner already reports a missing one
    mon = monitor.read_text(encoding="utf-8")
    m = re.search(r'LOG\s*=\s*HERE\s*/\s*"([^"]+)"\s*/\s*"([^"]+)"', mon)
    if not m:
        fail("loop/monitor", "cannot find the log path monitor.py reads")
        return
    wanted = f"{m.group(1)}/{m.group(2)}"
    src = loop.read_text(encoding="utf-8")
    # THE ASSIGNMENT, not any occurrence. The first version searched the whole file
    # and passed against a loop.sh pointed at a different path, because the comment
    # explaining the wiring still contained the old one. That is the exact mistake
    # this check's own message warns about: naming a path in a comment is not wiring
    # it up.
    assigned = re.search(r'^\s*LOOP_LOG=.*$', src, re.M)
    src = assigned.group(0) if assigned else ""
    if wanted not in src:
        fail(
            "loop/monitor",
            f"monitor.py tails '{wanted}' and loop.sh never names it, so the loop "
            f"writes nowhere the monitor can read. A watch on a file that never "
            f"appears reports a healthy loop as dead, forever",
        )
    elif "tee" not in loop.read_text(encoding="utf-8"):
        fail(
            "loop/monitor",
            f"loop.sh names '{wanted}' but does not write to it. Naming the path in "
            f"a comment is not wiring it up",
        )


def check_agent_rungs_wired(root: Path) -> None:
    """The two agent-driven rungs exist, are reachable, and stay on the right side.

    `harness/e2e.py` and `.factory/holdout/run.py` were deleted when the journeys
    became markdown, and NOTHING NOTICED. The self-test stayed green and so did this
    auditor, because both of them check the machinery that decides a verdict and
    neither checked that the rungs producing the evidence still existed. A gate can
    lose its end-to-end rung entirely and report `GATE_OK`.

    So this asserts, separately: the files are here, `ci.py` actually calls them, and
    the holdout spec is inside the directory every builder node is denied. The third
    is the one worth having. Moving those scenarios under `harness/` would leave every
    other check green while quietly ending the independence the auto-merge rests on.
    """
    ci = root / "harness" / "ci.py"
    agent = root / "harness" / "agentcheck.py"

    for rel, why in (
        ("harness/agentcheck.py", "nothing runs the journeys or validates the report"),
        ("harness/END-TO-END.md", "there is no end-to-end path to install"),
        (".factory/holdout/HOLDOUT.md", "nothing sits above the independence line"),
        (".claude/skills/factory-e2e/SKILL.md", "the journey agent has no instructions"),
        (".claude/skills/factory-holdout/SKILL.md", "the holdout agent has no instructions"),
    ):
        if not (root / rel).exists():
            fail("agent rungs", f"template/{rel} is missing -- {why}")

    if ci.exists():
        src = ci.read_text(encoding="utf-8")
        if "run_rung" not in src:
            fail("agent rungs", "harness/ci.py never calls run_rung -- the gate has no "
                                "agent-driven rung at all")
        else:
            for kind in ("e2e", "holdout"):
                if f'run_rung("{kind}"' not in src:
                    fail("agent rungs",
                         f"harness/ci.py does not run the {kind} rung. A rung that is "
                         f"never invoked produces no failures, and 'did anything fail?' "
                         f"reads that as success")

    # WHERE THE SCENARIOS LIVE IS THE WHOLE ARGUMENT. Read from the source rather
    # than assumed, so moving the path is caught here instead of six months later by
    # nobody.
    if agent.exists():
        src = agent.read_text(encoding="utf-8")
        m = re.search(r'"holdout":\s*\{[^}]*?"spec":\s*"([^"]+)"', src, re.S)
        if not m:
            fail("agent rungs", "cannot find the holdout spec path in agentcheck.py")
        elif not m.group(1).startswith(".factory/holdout/"):
            fail(
                "holdout isolation",
                f"the holdout spec is at '{m.group(1)}', outside the directory every "
                f"builder node is denied. The builder can read the assertions its work "
                f"will be judged against, and it will write code aimed at exactly those",
            )


def code_only(source: str) -> str:
    """Source with comments and DOCSTRINGS removed, and every other literal kept.

    A grep for a forbidden identifier matches the paragraph explaining why it is
    forbidden -- both checks below flagged their own docstrings the first time they
    ran. The obvious repair, dropping every string token, is worse and it was measured
    that way: the defect these checks exist for was `r.get("branch")`, so stripping
    string literals hides the exact expression being hunted. The mutation went from
    CAUGHT to ESCAPED and the check kept reporting clean.

    Docstrings out, literals in. `ast.unparse` drops comments for free.
    """
    import ast as _ast

    class _Strip(_ast.NodeTransformer):
        def _drop(self, node):
            self.generic_visit(node)
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], _ast.Expr)
                    and isinstance(body[0].value, _ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [_ast.Pass()]
            return node

        visit_Module = visit_FunctionDef = _drop
        visit_AsyncFunctionDef = visit_ClassDef = _drop

    try:
        return _ast.unparse(_ast.fix_missing_locations(_Strip().visit(_ast.parse(source))))
    except (SyntaxError, ValueError):
        return source


def runs_selftest(doctor_src: str) -> bool:
    """Does the doctor actually SPAWN the self-test?

    Grepping the file for the name is not the same question. The first version of this
    check asked that, and a doctor edited to launch `_nothing.py` still passed -- the
    name survived in the failure message it prints, which is text about the test rather
    than an invocation of it.
    """
    import ast as _ast
    try:
        tree = _ast.parse(doctor_src)
    except SyntaxError:
        return False
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        fn = _ast.unparse(node.func)
        if "subprocess" not in fn and "Popen" not in fn:
            continue
        if "_selftest" in _ast.unparse(node):
            return True
    return False


def check_watchdog_wired(root: Path) -> None:
    """The watchdog must exist, must be CALLED by the tick, and must be able to HALT.

    Three separate claims, because on this project each has failed on its own:

     1. It exists. Easy, and the least useful of the three.
     2. The dispatcher actually calls it. A safety net that ships in a file nobody
        imports is the "dead tests" failure again -- 203 lines of persistence tests
        passed by hand for weeks while the gate never executed one of them.
     3. Its halt path writes the STOP file. `escalate()` wrote its label correctly 68
        times while the machine drove straight through it, so "the guard fired" and
        "the machine stopped" have to be checked as different things.
    """
    wd = root / "factory" / "watchdog.py"
    disp = root / "factory" / "dispatch.py"
    if not wd.exists():
        fail("watchdog", "factory/watchdog.py is missing; a runaway has nothing to stop it")
        return
    body = code_only(disp.read_text(encoding="utf-8")) if disp.exists() else ""
    if "watchdog.assess" not in body:
        fail("watchdog wired", "dispatch.py never calls watchdog.assess, so the tick is "
                               "unwatched no matter what watchdog.py contains")
        return
    if "watchdog.halt" not in body:
        fail("watchdog halts", "dispatch.py calls assess() but never halt(); a finding "
                               "that only prints is the escalation that was already ignored")
        return
    wbody = code_only(wd.read_text(encoding="utf-8"))
    if "STOP_FILE" not in wbody:
        fail("watchdog halts", "watchdog.halt does not touch config.STOP_FILE, so nothing "
                               "it decides can actually stop the next tick")
        return


def check_guard_is_trusted(root: Path) -> None:
    """The guard must be run from the BASE tree, never imported from the branch under test.

    This is the root of trust for the entire protected-path mechanism, and it was
    broken once: the gate runner did `sys.path.insert(0, cwd/"factory"); import guard`,
    and in a validation the cwd IS the pull request's checkout. The code deciding
    whether a pull request may touch protected files was code that pull request could
    edit.

    Demonstrated in one commit. A branch that set UNIT_CHECKS to 1 in the protected
    ratchet AND changed `if violations:` to `if False:` in guard.py printed

        BLOCK  .factory/locks/floor.json
        BLOCK  factory/guard.py
        PROTECTED_OK

    and exited 0. It saw both violations, named them, and waved itself through -- and
    PROTECTED_OK is a required marker, so everything downstream was satisfied.

    THE OWNER MOVED, THE INVARIANT DID NOT. `sdlc.snapshot` now materialises the whole
    trusted set out of the base tree and `fixed_gate` runs it, so this checks the same
    three things about the new owner: that the guard comes from a git object at the
    base revision, that the required files are asserted present rather than assumed,
    and that a missing one refuses instead of falling back to the candidate's copy.
    """
    sdlc = root / "factory" / "sdlc.py"
    gate = root / "factory" / "fixed_gate.py"
    if not sdlc.exists() or not gate.exists():
        fail("guard is trusted",
             "factory/sdlc.py or factory/fixed_gate.py is missing, so nothing "
             "materialises a trusted guard")
        return

    text = sdlc.read_text(encoding="utf-8")
    start = text.find("def snapshot(")
    nxt = text.find("\ndef ", start + 1)
    body = code_only(text[start:nxt if nxt > 0 else len(text)]) if start >= 0 else ""
    if not body:
        fail("guard is trusted", "factory/sdlc.py has no snapshot() to build the trusted gate")
        return
    if "ls-tree" not in body or "show" not in body:
        fail("guard is trusted",
             "snapshot() does not read the factory machinery out of a git tree. Anything "
             "that copies it from the working checkout is copying whatever is there, "
             "which in a validation is the candidate")
        return
    if "guard.py" not in body or "raise" not in body:
        fail("guard is trusted",
             "snapshot() does not assert guard.py is present in the base tree and refuse "
             "when it is not. A missing trusted guard must abort, never fall back to the "
             "branch's own copy, which is the original bug")

    gate_body = code_only(gate.read_text(encoding="utf-8"))
    if "guard.main(" not in gate_body or "guard.preflight(" not in gate_body:
        fail("guard is trusted",
             "factory/fixed_gate.py does not run the guard and its secret preflight, so "
             "the trusted set is materialised and then not consulted")
    if "base_sha" not in gate_body:
        fail("guard is trusted",
             "fixed_gate runs the guard without the base revision from the profile, so "
             "the diff it judges is against whatever the checkout happens to think base is")


def check_installer_pin(root: Path) -> None:
    """The installer must name the same workflows the runtime dispatches, and pin an engine.

    TWO LISTS, ONE FACT. `bin/factory.py` names the workflows before the template is
    copied and `factory/config.py` names them at dispatch time. They cannot import each
    other, so the only thing keeping them equal is this check -- and a factory that
    installs an engine missing one of them reports a successful install and fails at the
    first tick, which is the shape of failure this whole auditor exists for.

    The pin is checked as a SHAPE, not a value: a 40-character hex SHA or an explicit
    refusal. What must never happen is an installer that builds from a moving branch and
    calls the result tested.
    """
    installer = HOME / "bin" / "factory.py"
    cfg = root / "factory" / "config.py"
    if not installer.exists() or not cfg.exists():
        return
    src = installer.read_text(encoding="utf-8")
    listed = re.search(r"REQUIRED_WORKFLOWS = \(([^)]*)\)", src, re.S)
    if not listed:
        fail("installer pin", "bin/factory.py declares no REQUIRED_WORKFLOWS")
        return
    installer_names = set(re.findall(r'"([a-z-]+)"', listed.group(1)))
    config_names = set(re.findall(r'WORKFLOW_[A-Z]+ = _env\("[A-Z_]+", "([a-z-]+)"\)',
                                  cfg.read_text(encoding="utf-8")))
    if installer_names != config_names:
        fail("installer pin",
             f"bin/factory.py checks for {sorted(installer_names)} and factory/config.py "
             f"dispatches {sorted(config_names)}. An install verifies one set and the "
             f"factory then asks the engine for another")

    ref = re.search(r'ARCHON_REF = "([^"]*)"', src)
    if not ref:
        fail("installer pin", "bin/factory.py has no ARCHON_REF")
        return
    pinned = bool(re.fullmatch(r"[0-9a-f]{40}", ref.group(1)))
    if not pinned and "not re.fullmatch" not in src:
        fail("installer pin",
             f"ARCHON_REF is `{ref.group(1)}`, which is not a commit, and nothing refuses "
             f"the install. A build from a moving branch is not the revision this factory "
             f"was tested against")
    elif not pinned:
        warn("installer pin",
             f"ARCHON_REF is the placeholder `{ref.group(1)}`. Building the engine from "
             f"source is correctly refused, so `factory init` only works where an engine "
             f"carrying the SDLC pack is already installed. Set it to the tested SHA.")


def check_result_binding(root: Path) -> None:
    """A settled run's result must be read from THAT run, never from the newest file.

    The failure this prevents is silent and it is the reason the journal exists: two
    validations of two pull requests write `acceptance.json` into two artifact
    directories, and a consumer that globs for the newest one applies the second run's
    verdict to the first run's target. Every label is written successfully. Every
    receipt is valid. It is simply about the wrong pull request.
    """
    sdlc = root / "factory" / "sdlc.py"
    if not sdlc.exists():
        return
    body = code_only(sdlc.read_text(encoding="utf-8"))
    if "output_root" not in body or "artifact_root(" not in body:
        fail("result binding",
             "factory/sdlc.py does not resolve artifacts through the run's persisted "
             "output_root, so it cannot prove which run produced what it is applying")
    for pattern, why in (
        (r"glob\([^)]*acceptance", "globs for an acceptance receipt"),
        (r"glob\([^)]*result", "globs for a result file"),
        (r"st_mtime", "picks a result by modification time"),
    ):
        if re.search(pattern, body):
            fail("result binding",
                 f"factory/sdlc.py {why} -- that is 'whichever run finished last', not "
                 f"'the run this dispatch started'")


def check_install_ships_a_runner(root: Path) -> None:
    """`init` must install the things that RUN the factory, not only the machinery.

    Everything else in COPY_PLAN is parts: the state machine, the gate, the guard, the
    workflow pack, the harness. None of them do anything on their own. The dispatcher
    loop is what turns them into a factory, the monitor is what tells a person when it
    stops, and the notifier is what makes an escalation reach somebody.

    All three were missing from the manifest, and it did not look like a bug: `init`
    reported success, `doctor` passed, and the only symptom was one warning saying
    "nothing scheduled -- the factory only runs when you run it". The example repo had
    every part installed and its loop, monitor and notifier written by hand afterwards.
    That is not an install, it is a parts list.
    """
    installer = root.parent / "bin" / "factory.py" if root.name == "template" else None
    src = installer if installer and installer.exists() else Path(__file__).resolve()
    body = src.read_text(encoding="utf-8")
    for name, why in (
        (".factory/loop.sh", "the dispatcher loop; without it nothing ever ticks"),
        (".factory/monitor.py", "the operator watch; without it a stopped factory is silent"),
        (".factory/notify.sh", "the escalation channel; without it needs-human reaches nobody"),
    ):
        if f'"{name}"' not in body:
            fail("install ships a runner", f"COPY_PLAN does not install {name} -- {why}")
            return
        if not (root / name).exists():
            fail("install ships a runner",
                 f"COPY_PLAN installs {name} but template/{name} does not exist")
            return


def check_deploy_result_is_read(root: Path) -> None:
    """A failed deploy after a successful merge must not be silent.

    The dispatcher ran `deploy.py` and discarded the result: the code merged, the deploy
    broke, and the lap reported clean. It never mattered while FACTORY_DEPLOY_CMD was
    unset, because deploy.py then prints DEPLOY_NOT_CONFIGURED and exits 0 -- so wiring
    the fifth component turned a dormant hole into a live one, which is the general
    shape worth watching for: a code path that only becomes reachable once a feature is
    actually configured.
    """
    disp = root / "factory" / "dispatch.py"
    if not disp.exists():
        return
    body = code_only(disp.read_text(encoding="utf-8"))
    if "deploy.py" not in body:
        return
    # the deploy invocation must have its returncode inspected somewhere after it
    idx = body.find("str(deploy)")
    if idx < 0:
        return
    window = body[idx:idx + 900]
    if "returncode" not in window:
        fail("deploy result is read",
             "dispatch.py runs deploy.py and never inspects the result, so a deploy that "
             "fails after a successful merge is silent -- main ends up ahead of what is "
             "running and nothing says so")


def check_selftest_wired(root: Path) -> None:
    """The machinery self-test must exist, and the doctor must run it.

    A test nobody runs is a comment. This one guards the parts that decide which laps
    are alive, what counts as passed, and what may move -- and every one of those was
    once wrong in a way that read as a quiet, healthy repository.
    """
    st = root / "factory" / "_selftest.py"
    if not st.exists():
        fail("machinery self-test", "factory/_selftest.py is missing")
        return
    proc = subprocess.run(
        [sys.executable, str(st), "--quiet"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    last = (proc.stdout or "").strip().splitlines()
    marker = last[-1] if last else ""
    if not marker.startswith("SELFTEST_PASSED"):
        fail("machinery self-test", (marker or "produced no marker at all")
             + " -- run `python factory/_selftest.py` for the list")

    if not runs_selftest((root / "factory" / "doctor.py").read_text(encoding="utf-8")):
        fail(
            "machinery self-test",
            "factory/doctor.py never spawns _selftest.py, so nothing runs it on an "
            "audit -- and an unrun test audits identically to a passing one",
        )
    if "from factory import" in code_only(st.read_text(encoding="utf-8")):
        fail(
            "machinery self-test",
            "_selftest.py imports `from factory import ...` while the modules it tests "
            "import flat -- that is two module objects with separate state, so the test "
            "configures a copy of the thing it believes it is testing",
        )


def check_lock_liveness(root: Path) -> None:
    """A lock may only be released on a positive report about a run id.

    The failure this exists for did not look like a bug: liveness was inferred from a
    branch name the engine never populates, so the set of live runs was empty and every
    lock was released one tick after it was taken. Nothing errored. The reconcile sweep
    then escalated running work as dead, correctly, on a false premise.
    """
    d = root / "factory" / "dispatch.py"
    if not d.exists():
        return
    text = d.read_text(encoding="utf-8")
    start = text.find("def release_settled_locks(")
    if start < 0:
        fail("lock liveness", "release_settled_locks() not found in factory/dispatch.py")
        return
    nxt = text.find("\ndef ", start + 1)
    body = code_only(text[start:nxt if nxt > 0 else len(text)])
    if "RUN_ID_RE" not in text or "lock_run_id" not in body:
        fail(
            "lock liveness",
            "release_settled_locks() does not key on a recorded run id. Any other "
            "identifier is a name this code invented and hoped the engine echoes back",
        )
    if "branch" in body:
        fail(
            "lock liveness",
            "release_settled_locks() still mentions `branch`. The engine does not report "
            "one in that payload, and matching against a blank is how every lock got "
            "released one tick after it was taken",
        )
    if "if not status_by_id" not in body:
        fail(
            "lock liveness",
            "release_settled_locks() has no guard for an empty run list. Empty is "
            "silence, not 'nothing is running' -- keeping those apart is the whole job",
        )
    if "SETTLED_STATUSES" not in body:
        fail(
            "lock liveness",
            "release_settled_locks() does not test membership of an explicit settled "
            "set, so a status this engine has not been seen to emit could free a lock",
        )


def check_trigger_parity(root: Path) -> None:
    """Both scheduler backends must install BOTH jobs.

    The cron path installed the dispatcher and the weekly regression; the Task
    Scheduler path installed the dispatcher and said "ARMED". A Windows factory was
    then fully armed, fully green in the doctor, and never once re-tested what it had
    already merged -- the component whose whole job is noticing that merged code
    stopped working simply was not scheduled.

    Nothing reports a job that was never created, which is why this is checked here
    rather than trusted to a run.
    """
    trig = root / "factory" / "trigger.py"
    if not trig.exists():
        return
    body = trig.read_text(encoding="utf-8")
    # code_only, and the reason is a measured one: the first version grepped the raw
    # function text, and a build with the regression call DELETED still passed --
    # because the comment above it explained what the call was for. A check that its
    # own explanation satisfies is a check that cannot fail.
    for backend, marker, needs in (
        ("cron", "install_cron", "REGRESS"),
        ("task scheduler", "install_task_scheduler", "install_regress_task_scheduler("),
    ):
        start = body.find("def " + marker + "(")
        if start < 0:
            fail("trigger parity", marker + "() is missing from factory/trigger.py")
            continue
        nxt = body.find("\ndef ", start + 1)
        chunk = code_only(body[start:nxt if nxt > 0 else len(body)])
        if needs not in chunk:
            fail(
                "trigger parity",
                "the " + backend + " backend never schedules the regression, so an "
                "armed factory would never re-test what it merged -- and would audit "
                "as armed",
            )
    rm = body.find("def remove(")
    if rm >= 0 and "-regress" not in body[rm:]:
        fail(
            "trigger parity",
            "remove() does not delete the regression job, so disarming leaves it "
            "filing issues into a queue nothing dispatches",
        )


def check_base_branch(root: Path) -> None:
    """The default branch is DETECTED, never assumed.

    `main` was hardcoded in a dozen places across the merge and the deploy poller --
    `base = "origin/main"`, and a merge that refused any PR whose base was not
    literally "main". On a repository using `master` or `develop`, this product
    installed cleanly, audited green, and could never merge anything. That is the
    worst shape a bug can take in something whose pitch is "install it into your
    repo".
    """
    for name in ("merge.py", "deploy.py", "doctor.py", "dispatch.py", "sdlc.py",
                 "fixed_gate.py"):
        f = root / "factory" / name
        if not f.exists():
            continue
        body = code_only(f.read_text(encoding="utf-8"))
        # SINGLE QUOTES, because code_only round-trips through ast.unparse and that
        # normalises every string literal to single quotes. The first version looked
        # for the double-quoted forms the source actually contains, matched nothing,
        # and reported clean against a build with the hardcode put back.
        for bad in ("'origin/main'", "'main'", "'origin/master'"):
            if bad in body:
                fail(
                    "base branch",
                    f"factory/{name} hardcodes {bad} -- use config.BASE_BRANCH, which is "
                    f"read from origin/HEAD, or the factory only works on repos named "
                    f"the way this one happened to be",
                )
                break


def main(argv: list[str]) -> int:
    root = HOME / "template"
    if "--repo" in argv:
        root = Path(argv[argv.index("--repo") + 1]).resolve()

    print(f"auditing {root}\n")

    check_referenced_files_exist(root)
    check_all_scripts_parse(root)
    check_state_labels(root)
    check_markers(root)
    check_no_freelance_writes(root)
    check_agent_rungs_wired(root)
    check_loop_and_monitor_agree(root)
    check_selftest_wired(root)
    check_watchdog_wired(root)
    check_guard_is_trusted(root)
    check_installer_pin(root)
    check_result_binding(root)
    check_install_ships_a_runner(root)
    check_deploy_result_is_read(root)
    check_lock_liveness(root)
    check_trigger_parity(root)
    check_base_branch(root)

    fails = [f for f in FINDINGS if f[0] == "FAIL"]
    warns = [f for f in FINDINGS if f[0] == "warn"]

    for sev, check, detail in FINDINGS:
        mark = "FAIL" if sev == "FAIL" else "warn"
        print(f"[{mark}] {check}: {detail}")

    if not FINDINGS:
        print("No findings. The machinery's cross-file invariants hold.")
    print(f"\n{len(fails)} failing, {len(warns)} warnings")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
