#!/usr/bin/env python3
"""Would the self-test know?

`factory/_selftest.py` pins the factory's own machinery. This asks the question that
matters about any set of checks: **can they fail?** It injects each defect below into a
throwaway copy of the template and requires the self-test to go red.

Everything in the template answers "is this build good?". The self-test answers "would
the thing deciding that know if it were not?". This answers the same question one level
up, about the self-test.

    python bin/selfcheck-mutations.py
    python bin/selfcheck-mutations.py --repo /path/to/an/installed/repo

A NOT_APPLICABLE entry is a defect that provably changes no behaviour, named here
rather than deleted, because a set that quietly drops the ones it cannot catch is a set
whose score means nothing.
"""

from __future__ import annotations

import shutil
import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
NEWLINE = chr(10)

# (name, file, anchor, replacement, applicable)
DEFECTS = [
    ("the transition check moves back out of set_state", "factory/state.py",
     "    if not force and old != new and new not in TRANSITIONS.get(old, set()):",
     "    if False:", True),
    ("parking loses its exemption", "factory/state.py",
     "    if not force and old != new", "    if old != new", True),
    ("re-applying the current state is refused", "factory/state.py",
     "    if not force and old != new and", "    if not force and", True),
    ("needs-human stops being terminal", "factory/state.py",
     '    "needs-human": set(),', '    "needs-human": {"accepted"},', True),
    ("escalate stops forcing the park", "factory/dispatch.py",
     'state.set_state(target, "needs-human", force=True)',
     'state.set_state(target, "needs-human")', True),
    ("the sweep stops excluding what it just escalated", "factory/dispatch.py",
     "    exclude: set[str] = set(escalated_here)", "    exclude: set[str] = set()", True),
    ("an unrecognised run status frees a lock", "factory/dispatch.py",
     '                    "error", "errored", "timeout", "timed_out", "stopped"}',
     '                    "error", "errored", "timeout", "timed_out", "stopped", "running"}',
     True),
    ("the level-3 markers stop following the dial", "factory/config.py",
     "if AUTONOMY >= 3:", "if False:", True),
    ("the deploy gate stops requiring markers", "factory/deploy.py",
     "    if not config.HEALTH_MARKERS:", "    if False:", True),
    ("the reaper acts on a lock that names a run or has a journal", "factory/dispatch.py",
     "        if lock_run_id(lock) or managed_lock(lock):", "        if False:", True),
    ("held becomes mergeable", "factory/state.py",
     '    "held": {"open", "needs-human", "rejected"},',
     '    "held": {"open", "needs-human", "rejected", "merged"},', True),
    ("done stops closing the issue", "factory/state.py",
     '        elif new == "done" and current.get("state") == "OPEN":',
     '        elif False:', True),
    ("an issue with a live PR becomes work again", "factory/state.py",
     '    issues = [i for i in issues if i["_target"] not in answered]',
     '    issues = list(issues)', True),
    ("merge moves a checked-out ref again", "factory/merge.py",
     "    holder = worktree_holding(config.BASE_BRANCH)", "    holder = ''", True),
    ("an unreadable worktree list is treated as safe", "factory/merge.py",
     "        return str(config.SHARED)", "        return ''", True),
    # NOT APPLICABLE, and stated rather than dropped. Removing the empty-list guard
    # changes nothing observable: an empty `status_by_id` means every lookup returns
    # None, and the per-lock "not in the reported window, so keep it" rule already
    # holds the lock. The guard is defence in depth and a statement of intent, not a
    # second mechanism, so no test can distinguish a build without it.
    ("the empty run list guard is removed", "factory/dispatch.py",
     "    if not status_by_id:", "    if False:", False),
    # THE AGENT-DRIVEN RUNGS. `_validate` is the only thing between "a model said it
    # passed" and "the gate passed", so every one of these removes a rejection and
    # requires the self-test to notice.
    ("an assertion may report nothing observed", "harness/agentcheck.py",
     "            if not observed:", "            if False:", True),
    # FULL LINE, because a partial anchor here left an unbalanced paren, and a
    # mutation that breaks the SYNTAX is caught by the interpreter rather than by
    # the check it was aimed at. That reads as CAUGHT and measures nothing.
    ("observed may simply restate the assertion", "harness/agentcheck.py",
     "            if observed.lower() == name.lower():",
     "            if False:", True),
    # NOT APPLICABLE, and named rather than deleted. Every group is already
    # required to carry at least one assertion, so by the time this line is
    # reached `assertions` cannot be 0. It is kept as a backstop for whoever
    # loosens the guard above it, and a set that quietly drops the defects it
    # cannot catch is a set whose score means nothing.
    ("zero assertions counts as a pass", "harness/agentcheck.py",
     "    if assertions == 0:", "    if False:", False),
    ("an empty report counts as a pass", "harness/agentcheck.py",
     "    if not groups:", "    if False:", True),
    ("no configured agent becomes a skip", "harness/agentcheck.py",
     "    if not cmd.strip():", "    if False:", True),
    # THE RATCHET'S SOURCES. A floor whose marker nobody prints is a floor nobody is
    # held to, and it looks configured the whole time.
    ("a floor key names a marker the harness never prints", "factory/gate.py",
     '"e2e_journeys": counted(log, "E2E_PASSED journeys"),',
     '"e2e_journeys": counted(log, "E2E_PASSED nothing"),', True),
    # THE QUOTES. Only argv[0] used to be unquoted, and the library driver's import
    # check could not fail as a result.
    ("only argv[0] gets its quotes stripped again", "harness/appproc.py",
     "    parts = [unquote(t) for t in shlex.split(cmd, posix=False)]",
     "    parts = shlex.split(cmd, posix=False)", True),
    ("a transient failure stops being retried", "factory/state.py",
     "        if attempt == attempts or not _is_transient(p.stderr or \"\"):",
     "        if True:", True),
    ("a real answer starts being retried", "factory/state.py",
     "    return any(sig in low for sig in _TRANSIENT)", "    return True", True),
    ("teardown stops freeing the port", "harness/appproc.py",
     "        self._free_the_port()", "        pass", True),
    ("the gate ladder stops unquoting its arguments", "harness/ci.py",
     "    argv = [unquote(t) for t in argv]", "    argv = list(argv)", True),
    ("the shipped floor counts agent-chosen assertions again",
     ".factory/locks/floor.json",
     '"e2e_journeys": 0,', '"e2e_steps_asserted": 0,', True),
    # THE CONSUMER'S REFUSALS. Each of these is a place where a well-formed answer
    # from a generic workflow would be applied to something it is not about, and none
    # of them announces itself: the labels still get written, the receipt is still
    # valid, the run still succeeds.
    ("an acceptance for another candidate is accepted", "factory/sdlc.py",
     "    if actual != expected:", "    if False:", True),
    ("an approval no longer needs complete evidence", "factory/sdlc.py",
     '    if not receipt["checks"] or receipt["findings"] or receipt.get("clipped") is not False:',
     "    if False:", True),
    ("a failed check stops blocking an approval", "factory/sdlc.py",
     '        if check.get("status") != "passed" or check.get("exit_code") != 0:',
     "        if False:", True),
    ("evidence about another candidate stops blocking an approval", "factory/sdlc.py",
     '        if check.get("identity") != {**expected, "repository": repo}:',
     "        if False:", True),
    ("an approval stops needing the factory's own measurements", "factory/sdlc.py",
     '    if verdict == "approve" and (measured is None or measured["errors"]):',
     "    if False:", True),
    ("the hold becomes a sentence again", "factory/sdlc.py",
     '            value = "held"', '            value = "passed"', True),
    ("the attempt cap stops bounding the repair loop", "factory/sdlc.py",
     '            if current["_attempts"] >= config.MAX_FIX_ATTEMPTS:',
     "            if False:", True),
    ("a cold repair runs without current findings", "factory/sdlc.py",
     '            if receipt["verdict"] != "request_changes":', "            if False:", True),
    ("strict isolation stops failing closed", "factory/sdlc.py",
     '        if profile["require_isolation"]:', "        if False:", True),
    ("a merge reported for another candidate is accepted", "factory/sdlc.py",
     "        if not same:", "        if False:", True),
    ("a failed merge policy refresh leaves authorization active", "factory/sdlc.py",
     '        runtime.write(path, {**runtime.read(path), "authorized": False})',
     "        pass", True),
    ("the merge policy stops reading the pull request's own state", "factory/sdlc.py",
     '               and state.fetch(target)["_state"] == "passed"', "               and True",
     True),
    ("the merge policy stops seeing recorded assumptions", "factory/sdlc.py",
     "               and not assumption_text(target).strip())", "               and True)",
     True),
    ("an effect stops recording that it happened", "factory/sdlc.py",
     '    record["applied"].append(name)', "    pass", True),
    ("a recorded effect is performed again on every retry", "factory/sdlc.py",
     '    if name in record["applied"]:', "    if False:", True),
    ("a replacement pull request is adopted as the repair", "factory/sdlc.py",
     "    if action == \"fix\" and pr_target != target:", "    if False:", True),
    ("a candidate that moved after delivery is queued anyway", "factory/sdlc.py",
     '    if current["head_sha"] != pr["head_sha"] or current["base_sha"] != pr["base_sha"]:',
     "    if False:", True),
    ("a result is read from a run nobody asked about", "factory/sdlc.py",
     "    if run.get(\"id\") != expected_id:", "    if False:", True),
    ("an artifact path may climb out of its run", "factory/sdlc.py",
     "    if not path.is_relative_to(root.resolve()):", "    if False:", True),
    ("a stream digest stops being re-computed", "factory/sdlc.py",
     '            if digest != check[f"{channel}_sha256"]:', "            if False:", True),
    ("the flood cap stops counting", "factory/sdlc.py",
     "    if position is None or position < config.ISSUE_CAP_PER_DAY:",
     "    if True:", True),
    ("the repository owner stops being exempt from the flood cap", "factory/sdlc.py",
     "        if not author or author == owner:", "        if False:", True),
    ("yesterday's rate-limited issues are never unstuck", "factory/sdlc.py",
     '            if row["createdAt"][:10] < today:', "            if False:", True),
    ("the STOP button is only checked before preparation", "factory/sdlc.py",
     '            raise ValueError("The STOP button or the dial changed during preparation")',
     "            pass", True),
    ("the ratchet raise stops being monotonic", "factory/merge.py",
     "        if not isinstance(got, int) or got <= value:",
     "        if not isinstance(got, int):", True),
    ("the ratchet raises a key the floor does not have", "factory/merge.py",
     "    for key, value in data.items():", "    for key, value in list(data.items()) + [('NEW_KEY', 0)]:",
     True),
    ("bookkeeping reports success on a dirty base checkout", "factory/merge.py",
     "    if rc or dirty:", "    if False:", True),
    ("bookkeeping raises a floor from a tree nobody measured", "factory/merge.py",
     "    if actual != accepted:", "    if False:", True),
    # THE TRUSTED GATE. It is reconstructed from the base tree precisely so a
    # candidate cannot supply the judge that judges it; these remove the parts that
    # make the reconstruction mean anything.
    ("a missing trusted file stops refusing", "factory/sdlc.py",
     "        if not (trusted / name).is_file():", "        if False:", True),
    ("a required marker stops being required", "factory/fixed_gate.py",
     "               for marker, seen in markers.items() if not seen]",
     "               for marker, seen in markers.items() if False]", True),
    ("a floor with no observed count stops being an error", "factory/fixed_gate.py",
     "            errors.append(" + NEWLINE +
     '                f"the ratchet has a floor for \'{key}\' ({minimum}) and the run log reports "' + NEWLINE +
     '                f"no count for it. A floor nothing measures is a floor nobody is held to.")',
     "            pass", True),
    ("an escaped deliberate defect stops blocking", "factory/fixed_gate.py",
     "        if caught != total:", "        if False:", True),
    ("the gate stops running the guard", "factory/fixed_gate.py",
     '            code = guard.main(["--base", profile["base_sha"], "--head", "HEAD"])',
     "            code = 0", True),
    ("a gate that could not run reports a candidate failure", "factory/fixed_gate.py",
     "        return 75" + NEWLINE + '    print(out, end="")',
     "        return 1" + NEWLINE + '    print(out, end="")', True),
]


def main(argv: list[str]) -> int:
    root = HOME / "template"
    if "--repo" in argv:
        root = Path(argv[argv.index("--repo") + 1]).resolve()

    print("injecting into a copy of " + str(root) + "\n")
    caught = applicable = 0
    problems: list[str] = []

    for name, rel, anchor, replacement, live in DEFECTS:
        tmp = Path(tempfile.mkdtemp()) / "probe"
        shutil.copytree(root, tmp, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        target = tmp / rel
        try:
            source = target.read_text(encoding="utf-8")
            hits = source.count(anchor)
            if hits != 1:
                # AN AMBIGUOUS ANCHOR IS NOT A PASS. Injecting into the wrong one of
                # two identical lines measures a defect nobody wrote.
                print("  NOT_INJECTED   " + name + "  (anchor appears "
                      + str(hits) + "x in " + rel + ")")
                problems.append(name)
                continue
            mutated = source.replace(anchor, replacement, 1)
            try:
                ast.parse(mutated)
            except SyntaxError:
                print("  NOT_INJECTED   " + name + "  (mutation creates invalid Python)", flush=True)
                problems.append(name)
                continue
            target.write_text(mutated, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(tmp / "factory" / "_selftest.py"), "--quiet"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=300,
            )
            # A crashed interpreter or missing dependency did not exercise an invariant.
            went_red = proc.returncode == 1 and bool(re.search(
                r"(?m)^SELFTEST_FAILED checks=[1-9][0-9]* failed=[1-9][0-9]*$", proc.stdout))
            if not live:
                print("  NOT_APPLICABLE " + name + "  (changes no behaviour; see the note)")
                continue
            applicable += 1
            if went_red:
                caught += 1
                print("  CAUGHT         " + name, flush=True)
            else:
                label = "ESCAPED" if proc.returncode == 0 else "INCONCLUSIVE"
                print("  " + label.ljust(15) + name, flush=True)
                problems.append(name)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    print()
    print("SELFCHECK_MUTATIONS_CAUGHT=" + str(caught) + " applicable=" + str(applicable))
    if problems:
        print("\nThe self-test cannot see: " + "; ".join(problems))
        return 1
    print("Every applicable defect turns the self-test red.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
