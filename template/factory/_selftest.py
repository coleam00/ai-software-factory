"""_selftest.py -- the harness for the factory's own machinery.

`harness/` asks "is the product working?". THIS asks "is the thing that decides
that working?", and the two are not the same question. Every check below exists
because the behaviour it pins was once wrong in a way that read as normal
operation: a lock released a tick after it was taken, a gate that passed on an
empty log, a state machine that let a node walk an item back out of needs-human.

Fast, offline, no network, no GitHub. The doctor runs it on every audit, so a
regression here is reported before the dial is trusted rather than after.

    python factory/_selftest.py            # run them
    python factory/_selftest.py --quiet    # markers only
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
import tempfile
from pathlib import Path

# Import the way every other module here imports -- flat, from this directory.
# `import config` and `from factory import config` produce TWO module objects with
# separate state, so a test that reaches for the second one is configuring a copy
# of the thing it believes it is testing. That mistake is silent: the calls all
# succeed and every assertion about the effect comes back false.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import dispatch  # noqa: E402
import gate  # noqa: E402
import state  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0
NL = chr(10)


def check(what: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(what + ((" -- " + detail) if detail else ""))


# --- the lock, and the difference between "finished" and "unanswered" ---------
# THE INCIDENT: the release path built a set of active branch names, the engine
# populated none of them, the blanks were filtered away, and every lock was then
# compared against an empty set. `any()` over nothing is False, so it released
# every lock one tick after it was taken and the reconcile sweep escalated a
# running lap as dead. An empty answer was read as a negative answer.

def lock_checks(tmp: Path) -> None:
    original = config.LOCKS_RUNTIME
    original_log = dispatch.log
    # A test must not write to the operator's log. A LOCK_RELEASED line about a run id
    # that never existed is worse than noise: it is evidence, in the place someone goes
    # to reconstruct what the factory did, about something that never happened.
    dispatch.log = lambda *a, **k: None
    config.LOCKS_RUNTIME = tmp / "locks"
    config.LOCKS_RUNTIME.mkdir(parents=True, exist_ok=True)
    try:
        run_id = "11111111-2222-3333-4444-555555555555"

        def fresh() -> Path:
            lk = config.LOCKS_RUNTIME / "implement-gh-issue-9.lock"
            lk.unlink(missing_ok=True)
            assert dispatch.acquire(lk)
            with lk.open("a", encoding="utf-8") as fh:
                fh.write("run " + run_id + "\n")
            return lk

        lk = fresh()
        check("acquire is exclusive", not dispatch.acquire(lk),
              "a second dispatcher took a lock that was already held")
        check("the run id is read back off the lock",
              dispatch.lock_run_id(lk) == run_id)

        dispatch.release_settled_locks(payload_override={"runs": []})
        check("an EMPTY run list keeps the lock", lk.exists(),
              "empty was treated as an answer; this is the original incident")

        dispatch.release_settled_locks(payload_override={"runs": [{"id": "", "status": ""}]})
        check("a run list with no usable ids keeps the lock", lk.exists())

        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": "99999999-0000-0000-0000-000000000000", "status": "completed"}]})
        check("a list that does not mention this run keeps the lock", lk.exists(),
              "absence from a windowed list is not evidence the run ended")

        # THE PER-RUN FALLBACK. The bulk list reports a WINDOW of 20 runs, so on a busy
        # day a live lock's run ages out of it within the hour. Asking about that id
        # directly is the only honest way to tell "the list did not mention it" from
        # "the engine says it is gone", and conflating them ran the factory at zero
        # capacity for three hours over a run that had already finished.
        missing = {"runs": [{"id": "99999999-0000-0000-0000-000000000000", "status": "completed"}]}

        dispatch.release_settled_locks(payload_override=missing, status_probe=lambda _r: None)
        check("an UNREACHABLE engine keeps the lock", lk.exists(),
              "silence must still be silence; this is the half that must not regress")

        dispatch.release_settled_locks(payload_override=missing, status_probe=lambda _r: "running")
        check("a probe reporting RUNNING keeps the lock", lk.exists())

        dispatch.release_settled_locks(payload_override=missing, status_probe=lambda _r: "wat")
        check("a probe reporting an UNRECOGNISED status keeps the lock", lk.exists(),
              "unknown must mean still running, never settled")

        # A YOUNG lock is not probed at all. `not_found` seconds after a dispatch is
        # the engine not having persisted the row yet, and acting on it released the
        # lock out from under a LIVE validation, which the reconcile sweep then
        # escalated to needs-human. That happened for real, minutes after the probe
        # was added: the cure for a three-hour wedge became a three-second one that
        # killed work which was going fine.
        dispatch.release_settled_locks(payload_override=missing,
                                       status_probe=lambda _r: "not_found")
        check("a YOUNG lock is not released on not_found", lk.exists(),
              "a just-dispatched run is not yet queryable; this is a race, not a verdict")

        import time as _t
        old_enough = _t.time() - (dispatch.PROBE_GRACE_MINUTES + 1) * 60
        import os as _os
        _os.utime(lk, (old_enough, old_enough))
        dispatch.release_settled_locks(payload_override=missing,
                                       status_probe=lambda _r: "not_found")
        check("an AGED lock reporting NOT_FOUND is released", not lk.exists(),
              "being told the run does not exist is an answer, not silence")
        # Re-take it for the checks that follow. `os` is imported further down in
        # this function, so the pid is written as 0: a lock naming a run is freed
        # by evidence about that run, never by its pid.
        lk.write_text(f"0 now\nrun {run_id}\n", encoding="utf-8")

        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "running"}]})
        check("a RUNNING run keeps the lock", lk.exists())

        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "some-state-this-engine-invented"}]})
        check("an unrecognised status keeps the lock", lk.exists(),
              "unknown must mean still running, never settled")

        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "completed"}]})
        check("a COMPLETED run releases the lock", not lk.exists(),
              "nothing would ever be released, so every target stalls until reaped")

        lk = fresh()
        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "failed"}]})
        check("a FAILED run releases the lock", not lk.exists())

        lk = config.LOCKS_RUNTIME / "implement-gh-issue-8.lock"
        lk.unlink(missing_ok=True)
        assert dispatch.acquire(lk)          # no run id recorded
        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "completed"}]})
        check("a lock carrying no run id is left to the age reaper", lk.exists())
        lk.unlink(missing_ok=True)

        # --- the reaper, and whose pid is on the lock -------------------------
        # DISPATCH IS DETACHED, so the recorded pid dies in seconds while the run
        # has twenty minutes left. The first reaper tested "pid gone AND older than
        # GRACE" and its docstring said a live lap is never touched because its pid
        # is alive -- false for every dispatch this system makes. An implement lap
        # ran nine minutes, its lock was reaped at five, and the sweep escalated it
        # as dead while it went on to open a pull request.
        import os
        import time as _time

        aged = _time.time() - (config.LOCK_GRACE_MINUTES + 5) * 60

        held = config.LOCKS_RUNTIME / "implement-gh-issue-7.lock"
        held.unlink(missing_ok=True)
        assert dispatch.acquire(held)
        with held.open("a", encoding="utf-8") as fh:
            fh.write("run " + run_id + "\n")
        held.write_text(
            "999999 2020-01-01T00:00:00+00:00\nrun " + run_id + "\n", encoding="utf-8"
        )
        os.utime(held, (aged, aged))
        dispatch.reap_locks()
        check("an aged lock naming a run survives a dead dispatching pid", held.exists(),
              "every lap longer than the grace period would be reaped and escalated")

        orphan = config.LOCKS_RUNTIME / "implement-gh-issue-6.lock"
        orphan.write_text("999999 2020-01-01T00:00:00+00:00\n", encoding="utf-8")
        os.utime(orphan, (aged, aged))
        dispatch.reap_locks()
        check("an aged lock naming NO run is still reaped", not orphan.exists(),
              "a dispatch that died before recording a run id would wedge capacity")

        ancient = config.LOCKS_RUNTIME / "implement-gh-issue-5.lock"
        ancient.write_text(
            "999999 2020-01-01T00:00:00+00:00\nrun " + run_id + "\n", encoding="utf-8"
        )
        very_old = _time.time() - (config.LOCK_STALE_MINUTES + 5) * 60
        os.utime(ancient, (very_old, very_old))
        dispatch.reap_locks()
        check("the stale cap still frees a lock the engine can no longer be asked about",
              not ancient.exists())
        held.unlink(missing_ok=True)
    finally:
        config.LOCKS_RUNTIME = original
        dispatch.log = original_log


# --- the gate, and "empty is not pass" ---------------------------------------

def gate_checks() -> None:
    check("an empty log yields no counts",
          all(v is None for v in gate.observed_counts("").values()),
          "a missing marker must read as unknown, never as zero-and-fine")
    check("a marker with no count reads as unknown",
          gate.counted("E2E_PASSED", "E2E_PASSED steps") is None)
    check("the last occurrence of a marker wins",
          gate.counted("E2E_PASSED steps=3\nE2E_PASSED steps=17", "E2E_PASSED steps") == 17,
          "a re-run inside one log must not be scored on its first attempt")
    for key in gate.FLOOR_SOURCES:
        check("floor key " + key + " has a source marker",
              key in gate.observed_counts("E2E_PASSED steps=1"))


# --- the state machine, and the escalation guarantee --------------------------

def state_checks() -> None:
    check("needs-human is terminal for every node",
          state.TRANSITIONS["needs-human"] == set(),
          "a node could walk an item back out of the one state that means STOP")
    check("merged is terminal", state.TRANSITIONS["merged"] == set())
    check("passed does not lead back to validating",
          "validating" not in state.TRANSITIONS["passed"],
          "two validations would claim one PR")
    check("every state can reach needs-human",
          all("needs-human" in v for k, v in state.TRANSITIONS.items()
              if v and k not in ("merged",)),
          "a state with no escape hatch is a state that strands work")
    for src, dsts in state.TRANSITIONS.items():
        for d in dsts:
            check("transition target " + d + " is a declared state",
                  d in state.TRANSITIONS, "reachable from " + src)
    # `open` is a PR with no disposition label and `closed-unlabelled` is an issue
    # GitHub closed on merge, so both are the ABSENCE of a label by construction.
    # Naming them here is what stops that exemption growing quietly: any other
    # label-less state is a state that cannot be written, so it cannot be read back.
    labelless = {"open", "closed-unlabelled"}
    check("every state that is not defined by absence has a label",
          all(s in state.LABEL_FOR_STATE for s in state.TRANSITIONS if s not in labelless),
          "missing: " + " ".join(sorted(
              s for s in state.TRANSITIONS
              if s not in labelless and s not in state.LABEL_FOR_STATE)))


    # THE READ MUST AGREE WITH THE WRITE. Every check above this one interrogates
    # TRANSITIONS, which is the table a person reads when they want to know what the
    # states are. None of them ever asked whether a state written as a label can be
    # READ BACK as itself, and that gap cost 68 dispatches of one rejected pull
    # request: `factory:needs-human` on a PR was skipped by the kind filter in
    # `_state_from_labels` and fell through to `open`, so the dispatcher put the one
    # state that means STOP back at the front of its queue, indefinitely.
    #
    # THE FIRST VERSION OF THIS CHECK WENT VACUOUS RATHER THAN RED. It asked which
    # kinds a state was declared for and round-tripped those, so when needs-human was
    # missing from PR_STATES the pr case was simply not generated: 118 checks passing
    # instead of 119 failing. A check that disappears in exactly the situation it
    # exists to catch is worse than no check, and it is the same "empty is not pass"
    # failure the gate has a ratchet for. So the invariant is now STATED, not derived
    # from the list it is auditing.
    check("needs-human is declared for BOTH kinds",
          "needs-human" in state.ISSUE_STATES and "needs-human" in state.PR_STATES,
          "escalate() parks either kind here, so a kind that does not declare it "
          "cannot read its own escalation back")

    # Anything a PR-only state can transition to is, by definition, a PR state.
    # `rejected` is deliberately excluded as a SOURCE: it is shared with issues, and
    # walking out of it drags the issue half of the table in.
    pr_sources = {"open", "validating", "passed", "failed", "merged", "held"}
    for src in pr_sources:
        for dst in state.TRANSITIONS.get(src, set()):
            check("PR state " + dst + " is declared in PR_STATES",
                  dst in state.PR_STATES or dst in {"open"},
                  "reachable from " + src + " but a PR carrying its label reads back as "
                  + state._state_from_labels("pr", [state.LABEL_FOR_STATE.get(dst, "")], False))

    for st, label in state.LABEL_FOR_STATE.items():
        if not label:
            continue
        for kind, declared in (("issue", state.ISSUE_STATES), ("pr", state.PR_STATES)):
            if st not in declared:
                continue
            check("state " + st + " round-trips for a " + kind,
                  state._state_from_labels(kind, [label], False) == st,
                  "written as " + label + " but reads back as "
                  + state._state_from_labels(kind, [label], False))


# --- the dial and the checks it makes load-bearing ----------------------------

def marker_checks() -> None:
    """At level 3 nobody reads the diff, so the two checks that justify that must be
    required to have RUN. A holdout that quietly stops running -- renamed, crashed on
    import, skipped by a bad path -- otherwise leaves a green gate, which is exactly
    the failure the marker list exists to prevent, aimed at the one check the whole
    arrangement rests on.

    Asked in a subprocess because the answer depends on the environment config was
    imported under, and this process already imported it once.
    """
    import os
    import subprocess

    here = str(Path(__file__).resolve().parent)
    probe = ("import sys; sys.path.insert(0, r'" + here + "'); "
             "import config; print(' '.join(config.REQUIRED_MARKERS))")

    def markers_at(level: int) -> set:
        env = {**os.environ, "FACTORY_AUTONOMY": str(level)}
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             env=env, timeout=60)
        return set((out.stdout or "").split())

    low, high = markers_at(2), markers_at(3)
    for m in ("PROTECTED_OK", config.MARKER_APP_RAN, config.MARKER_E2E, "GATE_OK"):
        check("marker " + m + " is required at every level", m in low)
    for m in config.MARKERS_LEVEL3:
        check("marker " + m + " becomes required at level 3", m in high,
              "an unreviewed merge could pass without it having run")
    check("the level-3 set is a superset of the level-2 set", low <= high,
          "raising the dial must never remove a requirement")


# --- what a tick escalated, it must not then dispatch ------------------------

def escalation_checks() -> None:
    """An escalation writes a label to GitHub and the queue is read back from GitHub
    seconds later. GitHub does not promise you read your own write, and it did not: a
    validation was parked at needs-human and re-dispatched eight seconds later, back
    into the state a human had just been told to look at.

    `escalate()` must therefore report every target it parked -- the linked issue
    included -- so the caller can exclude them for the rest of the tick. Checked
    against the source, because calling it would mutate a real repository.
    """
    src = (Path(__file__).resolve().parent / "dispatch.py").read_text(encoding="utf-8")
    check("escalate() reports what it parked",
          "def escalate(target: str, why: str) -> set[str]:" in src,
          "a caller cannot exclude targets it is never told about")
    check("the reconcile sweep collects them",
          "escalated_here |= escalate(" in src)
    check("and seeds the dispatch exclusions with them",
          "exclude: set[str] = set(escalated_here)" in src,
          "the sweep would park a target and the loop would dispatch it anyway")
    check("in-loop escalations exclude too",
          src.count("exclude |= escalate(") >= 2,
          "the fix cap and the merge refusal park a target mid-tick as well")
    check("escalate parks with force",
          src.count('"needs-human", force=True') >= 2,
          "an escalation that the transition table can refuse is not an escalation -- "
          "and a merged PR or an already-parked item reaches needs-human from nowhere")


# --- the table must be enforced where the writes are ------------------------

def enforcement_checks() -> None:
    """A transition table that only the CLI consults governs the CLI.

    Eleven callers import `set_state` and call it directly -- the gate, the merge,
    the dispatcher. While the check lived in a wrapper around the function, every one
    of them was ungoverned, and the guarantee read as absolute in the docs.

    Exercised for real: a fake `fetch` puts an item in a state, and the move is
    attempted. No network, no GitHub.
    """
    original_fetch = state.fetch
    original_gh = state.gh
    writes: list = []
    state.gh = lambda *a, **k: writes.append(a) or ""
    try:
        def at(current_state: str):
            state.fetch = lambda t: {
                "_state": current_state, "_labels": [], "_kind": "pr",
                "_target": t, "state": "OPEN",
            }

        at("needs-human")
        try:
            state.set_state("gh:pr:1", "validating")
            check("a node cannot claim an item parked at needs-human", False,
                  "the write went through; the escalation guarantee is decorative")
        except state.IllegalTransition:
            check("a node cannot claim an item parked at needs-human", True)

        # Park from `merged`, which reaches NOTHING in the table -- so this only
        # passes if `force` is genuinely exempt. Parking from needs-human proves
        # nothing: old == new short-circuits the check before force is consulted,
        # and a build with force removed entirely sailed through that version.
        at("merged")
        before = len(writes)
        try:
            state.set_state("gh:pr:1", "needs-human", force=True)
            check("parking is always allowed, from a state that reaches nothing",
                  len(writes) > before)
        except state.IllegalTransition:
            check("parking is always allowed, from a state that reaches nothing", False,
                  "an escalation a table lookup can block is not an escalation")

        at("validating")
        before = len(writes)
        state.set_state("gh:pr:1", "passed")
        check("a legal move still goes through", len(writes) > before)

        at("passed")
        try:
            state.set_state("gh:pr:1", "validating")
            check("passed cannot be re-claimed for validation", False,
                  "two validations would hold one PR")
        except state.IllegalTransition:
            check("passed cannot be re-claimed for validation", True)

        # THE HOLD MUST BE A STATE, NOT A SENTENCE. The gate used to print
        # "merge HELD", set the PR to `passed`, and the dispatcher merged it
        # forty-five seconds later -- because `passed` is what a mergeable PR is
        # called and the dispatcher reads states, not prose.
        check("held exists as a state", "held" in state.TRANSITIONS)
        check("held has its own label", state.LABEL_FOR_STATE.get("held") is not None,
              "a hold nobody can see on the PR is not a hold")
        check("held is not mergeable", "merged" not in state.TRANSITIONS.get("held", set()),
              "the dispatcher would merge the thing the gate held")
        check("held resumes only through open",
              state.TRANSITIONS.get("held", set()) == {"open", "needs-human", "rejected"},
              "a human raises the floor or accepts the assumptions, then it revalidates")
        gate_src = (Path(__file__).resolve().parent / "gate.py").read_text(encoding="utf-8")
        check("the gate writes held rather than passed when it holds",
              'state.set_state(target, "held")' in gate_src,
              "the hold would be a comment and the next tick would merge it")
        # A HOLD NOBODY CAN CLEAR IS A STALL. The gate re-reads the assumptions file
        # every run, so without an accept path a held PR holds again on the next
        # validation, and the next, forever. The hold shipped before its other half
        # did, and the stall would have looked like a factory with nothing to do.
        gate_reads = "ASSUMPTIONS_DIR" in gate_src
        cli = (Path(__file__).resolve().parent.parent / "factory" / "doctor.py")
        check("the gate holds on a file it re-reads every run", gate_reads)

        # AN ISSUE MARKED done MUST ACTUALLY CLOSE. Relying on `Fixes #N` in the PR
        # body is relying on GitHub's prose parsing of text an agent wrote -- and one
        # PR put the keyword inside backticks, so GitHub ignored it and the issue sat
        # OPEN under a `factory:done` label, reading as finished on every board.
        at("in-progress")
        writes.clear()
        state.fetch = lambda t_: {
            "_state": "in-progress", "_labels": [], "_kind": "issue",
            "_target": t_, "state": "OPEN",
        }
        state.set_state("gh:issue:1", "done")
        closed = any("close" in " ".join(str(x) for x in call) for call in writes)
        check("marking an issue done closes it", closed,
              "the label would say finished while the issue stayed open")

        merge_src = (Path(__file__).resolve().parent / "merge.py").read_text(encoding="utf-8")
        check("merge refuses any state that is not exactly passed",
              """if pr["_state"] != "passed":""" in merge_src,
              "the second line of defence for the hold, and for a stale read of any "
              "kind -- merge.py does not trust that the gate already decided")

        at("open")
        before = len(writes)
        state.set_state("gh:pr:1", "open")
        check("re-applying the current state is allowed", len(writes) > before,
              "the labels ARE the state, so a correct state with no label is unreadable")
    finally:
        state.fetch = original_fetch
        state.gh = original_gh

    src = (Path(__file__).resolve().parent / "state.py").read_text(encoding="utf-8")
    check("the check is inside set_state, not in a wrapper",
          "raise IllegalTransition(" in src.split("def set_state")[1].split("def ")[0],
          "a wrapper governs only the callers that use the wrapper")


# --- every state write must be able to fail safely ---------------------------

def write_safety_checks() -> None:
    """`set_state` talks to GitHub and can now also refuse an illegal move, so every
    call site must either be inside a `try` or be a forced park.

    One was not. The approve-but-hold branch of the gate wrote the state bare while
    the branch fifteen lines below it -- the same write, for the same reason -- was
    guarded. Unguarded, a label edit that fails ends the gate in a traceback and
    leaves the PR at `validating` with nothing holding it: the exact shape the
    reconcile sweep has to clean up, arriving as a crash instead of a verdict.
    """
    import ast as _ast

    here = Path(__file__).resolve().parent
    for mod in sorted(here.glob("*.py")):
        if mod.name.startswith("_"):
            continue
        try:
            tree = _ast.parse(mod.read_text(encoding="utf-8"))
        except SyntaxError:
            check("factory/" + mod.name + " parses", False)
            continue

        guarded: set = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Try):
                for inner in _ast.walk(node):
                    guarded.add(id(inner))

        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            if not _ast.unparse(node.func).endswith("set_state"):
                continue
            forced = any(k.arg == "force" for k in node.keywords)
            where = "factory/" + mod.name + ":" + str(node.lineno)
            check("the state write at " + where + " can fail safely",
                  forced or id(node) in guarded,
                  "a failed label edit ends the node in a traceback instead of a verdict")


# --- the last gate before a real user ----------------------------------------

def deploy_checks() -> None:
    """A health command with no markers to look for asserts an exit code and nothing
    else. It printed `HEALTH_CHECK_OK markers=0` and moved the pointer -- the
    empty-is-not-pass failure this system is built around, in the one gate standing
    between a merge and a real user. The refusal for a MISSING health command was
    there; the refusal for an unusable one was not.
    """
    src = (Path(__file__).resolve().parent / "deploy.py").read_text(encoding="utf-8")
    check("deploy refuses a health check with nothing to check",
          "if not config.HEALTH_MARKERS:" in src,
          "markers=0 would pass and the pointer would move")
    check("and refuses a missing health command",
          "if not config.HEALTH_CMD:" in src)
    check("the marker loop still runs after both guards",
          "for marker in config.HEALTH_MARKERS:" in src)


# --- one issue, one lap ------------------------------------------------------

def duplication_checks() -> None:
    """An issue a live pull request already answers must not be implemented again.

    `next_action` selects on the issue's label alone, and `accepted` is reachable
    while a PR for that issue is open. It happened: PR #13 was held on ratchet slack
    and the very next tick answered `implement gh:issue:12` -- the issue that PR was
    for. A second lap started, on a second branch, for work that was already built and
    waiting on a human. Nothing stopped it except a lock that happened to still be
    held by the validation, which is luck rather than a mechanism.

    Driven with fakes, because the real thing needs GitHub.
    """
    real_list, real_linked = state._list, state.linked_issue
    try:
        state._list = lambda kind, st=None: (
            [{"_target": "gh:pr:13", "_state": "held", "_labels": [], "_kind": "pr"}]
            if kind == "prs" else
            [{"_target": "gh:issue:12", "_state": "accepted", "_priority": "medium",
              "_labels": [], "_kind": "issue"}]
        )
        state.linked_issue = lambda tgt: "gh:issue:12"
        action, target, _ = state.next_action()
        check("an issue whose PR is still open is not re-implemented",
              action != "implement",
              f"chose {action} {target}: a second branch for work already built")

        # ...and the same issue IS work again once its PR is out of the picture.
        state._list = lambda kind, st=None: (
            [{"_target": "gh:pr:13", "_state": "rejected", "_labels": [], "_kind": "pr"}]
            if kind == "prs" else
            [{"_target": "gh:issue:12", "_state": "accepted", "_priority": "medium",
              "_labels": [], "_kind": "issue"}]
        )
        action, target, _ = state.next_action()
        check("but it is work again once that PR is rejected",
              action == "implement" and target == "gh:issue:12",
              f"chose {action} {target}: the filter is too broad and strands the issue")
    finally:
        state._list, state.linked_issue = real_list, real_linked


# --- never move a ref out from under a checkout ------------------------------

def refmove_checks() -> None:
    """`update-ref` on a checked-out branch arms a revert in that checkout.

    It moves the pointer and touches neither index nor working tree, so HEAD jumps to
    the merge while the files stay on the commit before it. `git status` there then
    reports the merged work as STAGED DELETIONS, and the next `git commit` -- by
    anyone, for any reason -- commits a revert of the merge that just landed.

    It happened, and it cost a feature and 106 lines of tests. The merge runs from a
    validation worktree, where the current branch is the validation branch, so the
    unsafe path was taken on every single merge.
    """
    import merge as merge_mod  # noqa: PLC0415

    src = (Path(__file__).resolve().parent / "merge.py").read_text(encoding="utf-8")
    # THE ASSIGNMENT, not the name. The first version looked for "worktree_holding("
    # anywhere in the file -- which the function's own `def` line satisfies. A build
    # with `holder = ""` hardcoded, taking the unsafe path every time, passed it.
    check("merge asks who has the base branch checked out",
          "holder = worktree_holding(config.BASE_BRANCH)" in src,
          "nothing would stop it moving a ref under a live working tree")
    check("and fast-forwards that checkout instead of moving its ref",
          '"-C", holder, "merge", "--ff-only"' in src.replace("'", '"'),
          "ref, index and files must move together or they come apart")

    marker = "def worktree_holding("
    body = src[src.find(marker):]
    nxt = body.find("\ndef ", 1)
    if nxt > 0:
        body = body[:nxt]
    check("an unreadable worktree list is treated as 'checked out somewhere'",
          "return str(config.SHARED)" in body,
          "unknown must take the safe path; the other way silently arms a revert")

    here = merge_mod.worktree_holding("definitely-not-a-branch-here")
    check("a branch nothing has checked out reports nowhere", here == "",
          "reported " + repr(here) + ", so update-ref would never run and refs go stale")


def watchdog_checks() -> None:
    """Run the watchdog's own detector proofs as machinery invariants.

    They live in `_test_watchdog.py` because they need synthetic histories and a
    frozen clock, and they are re-run FROM HERE so `doctor` cannot report healthy
    machinery while the one component that stops a runaway is broken. The proofs are
    not duplicated: `check` is swapped for this module's, so each one counts as an
    invariant here rather than being collapsed into a single pass/fail.
    """
    try:
        import _test_watchdog as wt
    except Exception as e:  # noqa: BLE001
        check("watchdog proofs are importable", False, str(e))
        return
    original = wt.check
    wt.check = check  # type: ignore[assignment]
    try:
        wt.detector_proofs()
    finally:
        wt.check = original  # type: ignore[assignment]


def ledger_isolation_checks() -> None:
    """The self-test must never write the REAL ledger, and this pins it.

    `lock_checks` drives `release_settled_locks()` with a synthetic Archon payload, and
    that function records a settle. Bound to the production path, every `doctor` run
    appended FABRICATED settles (run 11111111-2222-3333-4444-555555555555, alternating
    completed/failed) to the evidence the watchdog judges. Well-formed, plausible, and
    entirely invented -- which is worse than a corrupt line, because nothing looks
    wrong until a detector halts a healthy factory on it.

    Fake evidence in a safety system is the one failure mode that turns the safety
    system into the hazard, so it gets an invariant rather than a fix and a hope.
    """
    import ledger as _led
    real = config.SHARED / ".factory/ledger.jsonl"
    before = real.read_text(encoding="utf-8") if real.exists() else None
    check("the self-test redirects the ledger away from the real one",
          _led.LEDGER != real,
          f"still pointing at {_led.LEDGER}; a doctor run would fabricate history")
    _led.record(_led.SETTLE, run="selftest-probe", status="completed")
    after = real.read_text(encoding="utf-8") if real.exists() else None
    check("a recorded event did NOT reach the real ledger", before == after,
          "the production ledger grew during a test run")
    check("the redirected ledger DID receive it",
          any(e.get("run") == "selftest-probe" for e in _led.read()),
          "the write went nowhere, so this check proves nothing")


def size_cap_checks() -> None:
    """The size cap must count production code and exempt tests, with a total backstop.

    Both halves are load-bearing and each fails differently. Without the test exemption
    the cap punishes the behaviour the whole system exists to encourage -- PR #14 was
    rejected at 515 lines of which 404 were tests. Without the total backstop the
    exemption becomes a loophole: move anything into `tests/` and the cap is gone.
    """
    import guard
    check("a tests/ path is recognised as a test", guard.matches("tests/weapons.test.ts",
                                                                 guard.TEST_PATHS))
    check("a co-located .test.ts is recognised", guard.matches("src/sim/world.test.ts",
                                                               guard.TEST_PATHS))
    check("production source is NOT treated as a test",
          not guard.matches("src/sim/world.ts", guard.TEST_PATHS),
          "the exemption would swallow the code the cap exists to bound")
    check("the harness is NOT treated as a test",
          not guard.matches("harness/ci.ts", guard.TEST_PATHS),
          "harness/ is protected and must never become exempt scope")
    check("a total backstop exists and exceeds the production cap",
          bool(config.TOTAL_CAP) and config.TOTAL_CAP > config.SIZE_CAP,
          "without it, moving code under tests/ removes the cap entirely")


def run_resolution_checks() -> None:
    """The run id must come from the ENGINE, matched on a key the factory controls.

    `archon workflow run --detach` prints a "Run id" that appears nowhere in the run
    record: across four consecutive dispatches the timestamps and workflow names
    matched and the id overlap was zero. Keying on it meant lock liveness could never
    be answered, no run ever read back as `completed`, and no cost was ever available
    -- three symptoms, each patched separately, all one bug.
    """
    now = 1_756_000_000.0
    iso = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    older = datetime.fromtimestamp(now - 4000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = {"runs": [
        {"id": "right", "user_message": "validate gh:pr:15", "started_at": iso},
        {"id": "stale", "user_message": "validate gh:pr:15", "started_at": older},
        {"id": "other", "user_message": "validate gh:pr:14", "started_at": iso},
    ]}
    check("the run is found by the message the factory sent",
          dispatch.resolve_run_id("validate", "gh:pr:15", now, payload_override=payload) == "right")
    check("a PREVIOUS run of the same target is not matched",
          dispatch.resolve_run_id("validate", "gh:pr:15", now, payload_override=payload) != "stale",
          "a re-dispatch would otherwise adopt the run it just replaced")
    check("a different target is not matched",
          dispatch.resolve_run_id("validate", "gh:pr:99", now, payload_override=payload) == "")
    check("an empty engine answer resolves to nothing, not to a guess",
          dispatch.resolve_run_id("validate", "gh:pr:15", now,
                                  payload_override={"runs": []}) == "")


def ratchet_raise_checks() -> None:
    """The auto-raise may only ever move a floor UP, and only for keys it already has.

    This is the one place the machinery writes the protected floor file, so the
    property that makes it safe has to be asserted rather than argued. A pull request
    touching floor.json is still auto-rejected by the guard; this path runs after the
    merge, in the machinery, and can only tighten. "The floor never falls without a
    human" IS the ratchet, and these checks are what keep that true.
    """
    import json as _json
    import merge as _merge
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".factory" / "locks").mkdir(parents=True)
        floor = root / ".factory" / "locks" / "floor.json"
        before = {"_note": "prose", "UNIT_CHECKS": 64, "VITEST_PASSED": 21,
                  "MUTATIONS_CAUGHT": 14, "UNCALIBRATED_MAX": 7}
        floor.write_text(_json.dumps(before), encoding="utf-8")

        calls: list[tuple] = []
        original_git, original_env = _merge.git, os.environ.get("FACTORY_OBSERVED_COUNTS")
        _merge.git = lambda *a: (calls.append(a), (0, ""))[1]  # type: ignore[assignment]
        try:
            # observed: one higher, one LOWER, one absent, plus a key not in the floor
            os.environ["FACTORY_OBSERVED_COUNTS"] = _json.dumps(
                {"UNIT_CHECKS": 70, "VITEST_PASSED": 9, "NEW_KEY": 999,
                 "UNCALIBRATED_MAX": 99})
            _merge.raise_floor(str(root))
            after = _json.loads(floor.read_text(encoding="utf-8"))

            check("a higher observed count RAISES the floor", after["UNIT_CHECKS"] == 70)
            check("a LOWER observed count leaves the floor alone",
                  after["VITEST_PASSED"] == 21,
                  "the floor fell without a human, which is the ratchet gone")
            check("a key the run did not report is untouched",
                  after["MUTATIONS_CAUGHT"] == 14)
            check("a key not already in the floor is NOT added",
                  "NEW_KEY" not in after,
                  "the factory would be choosing what it is measured on")
            check("prose keys survive", after.get("_note") == "prose")
            check("a _MAX CEILING is never raised", after.get("UNCALIBRATED_MAX") == 7,
                  "raising a ceiling loosens the check; this path may only tighten")
            check("the raise is committed and pushed",
                  any("commit" in c for c in calls) and any("push" in c for c in calls))

            # nothing to raise -> nothing written, nothing committed
            calls.clear()
            os.environ["FACTORY_OBSERVED_COUNTS"] = _json.dumps({"UNIT_CHECKS": 70})
            check("no raise means no commit", _merge.raise_floor(str(root)) == ""
                  and not any("commit" in c for c in calls))

            # THE FILE FALLBACK, which is the branch that shipped broken.
            #
            # Everything above exercises the env-var hand-off. The DISPATCHER merge path
            # has no env var and reads a counts file instead, and that branch contained a
            # regex whose character class had been mangled by tooling -- so it raised
            # `unterminated character set` the moment it ran, was swallowed by the
            # caller's except, and the floor silently never moved on a real merge. One
            # function, two branches, and only one of them was ever executed by a test.
            # A SEPARATE SANDBOX, so raising a floor here cannot disturb the
            # assertions above and below that are about THIS root's floor.
            calls.clear()
            os.environ.pop("FACTORY_OBSERVED_COUNTS", None)
            os.environ["FACTORY_MERGE_TARGET"] = "gh:pr:7"
            original_findings = config.FINDINGS_DIR
            with tempfile.TemporaryDirectory() as td2:
                root2 = Path(td2)
                (root2 / ".factory" / "locks").mkdir(parents=True)
                (root2 / ".factory" / "locks" / "floor.json").write_text(
                    _json.dumps({"UNIT_CHECKS": 64}), encoding="utf-8")
                try:
                    config.FINDINGS_DIR = root2 / ".factory" / "findings"
                    config.FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
                    (config.FINDINGS_DIR / "gh-pr-7.counts.json").write_text(
                        _json.dumps({"UNIT_CHECKS": 88}), encoding="utf-8")
                    raised = _merge.raise_floor(str(root2))
                    check("the counts FILE is read when no env hand-off exists",
                          "UNIT_CHECKS" in raised and "88" in raised,
                          f"returned {raised!r}; without this the dispatcher merge path "
                          f"raises nothing at all")
                    check("and the file-fallback path committed", any("commit" in c for c in calls))
                finally:
                    config.FINDINGS_DIR = original_findings
                    os.environ.pop("FACTORY_MERGE_TARGET", None)

            # a missing/garbled hand-off must be a no-op, never a rewrite
            calls.clear()
            os.environ["FACTORY_OBSERVED_COUNTS"] = "not json"
            check("an unreadable hand-off changes nothing",
                  _merge.raise_floor(str(root)) == ""
                  and _json.loads(floor.read_text(encoding="utf-8"))["UNIT_CHECKS"] == 70)
        finally:
            _merge.git = original_git  # type: ignore[assignment]
            if original_env is None:
                os.environ.pop("FACTORY_OBSERVED_COUNTS", None)
            else:
                os.environ["FACTORY_OBSERVED_COUNTS"] = original_env


def uncalibrated_ceiling_checks() -> None:
    """The uncalibrated hold must fire on a RISE, and stay silent at the ceiling.

    It fired on neither before: the gate looked for `NAME_UNCALIBRATED=<n>` while the
    harness prints `FAILED=0 UNCALIBRATED=5`, so the regex matched nothing on every run
    since it was written. Simply repairing the regex would have been worse than leaving
    it dead -- seven margins are uncalibrated on main BY DESIGN, so "any exist" would
    have refused every auto-merge forever.
    """
    import gate as _gate
    real_log = ("BALANCE_CLAIMS=10 FAILED=0 UNCALIBRATED=5\n"
                "LEGIBILITY_CHECKS=20 FAILED=0 UNCALIBRATED=2\n")
    total = _gate.uncalibrated_total(real_log)
    check("the marker the harness ACTUALLY prints is counted", total == 7,
          f"counted {total}; the old pattern required an underscore and matched nothing")
    check("a log with no uncalibrated margins counts zero",
          _gate.uncalibrated_total("BALANCE_CLAIMS=10 FAILED=0" + chr(10)) == 0)
    # THE CEILING'S VALUE IS REPO STATE, NOT A MACHINERY INVARIANT, so it is checked by
    # `doctor` instead. Reading an installed factory's floor.json from here made this
    # file unrunnable in the template, where no such file exists -- and a self-test that
    # cannot run in the product it ships with is a self-test nobody runs.


def assumption_count_checks() -> None:
    """An assumption is a KEY, not a line, and the hold message must say which."""
    import gate as _gate
    sample = NL.join([
        "# ASSUMPTIONS - issue 3",  # a comment, not an assumption
        "",
        "EFFECTIVE=1.9  | WHY: derived with RESISTED below, not chosen on its own.",
        "                 The mean multiplier a hero sees over a wave is unchanged,",
        "                 which is the whole reason the pair moves together.",
        "                 CHANGE IF: the balance run shows the margin inside noise.",
        "",
        "RESISTED=0.35  | WHY: the other half of the pair above.",
        "                 CHANGE IF: an off-element loadout feels unplayable.",
    ])
    keys = _gate.assumption_keys(sample)
    check("two assumptions are counted as two, not as nine lines",
          keys == ["EFFECTIVE", "RESISTED"],
          f"got {keys}; counting lines reported 7-8 assumptions as 69-80 on every PR,"
          f" which made a reviewable hold look like an unreviewable wall")
    check("an indented continuation is not an assumption",
          "CHANGE" not in keys and "The" not in keys)
    check("a comment line is not an assumption", not any(k.startswith("#") for k in keys))
    check("an empty file yields no assumptions", _gate.assumption_keys("") == [])

    # THE CALL SITE, NOT JUST THE FUNCTION. The checks above prove assumption_keys is
    # correct; they say nothing about whether the gate USES it. A mutation that put
    # line-counting back into the hold message passed all of them, because the hold
    # message is built inline in main() where a unit check cannot reach it. Source
    # inspection is how the rest of this machinery pins its call sites too.
    gate_src = (Path(__file__).parent / "gate.py").read_text(encoding="utf-8")
    check("the hold message counts assumptions via assumption_keys",
          "keys = assumption_keys(assumptions)" in gate_src,
          "the gate is counting something else; line-counting reported 8 as 80")
    check("the hold message does not count raw lines",
          "assumptions.splitlines() if" not in gate_src,
          "that expression IS the bug: it counts WHY paragraphs as assumptions")
    check("the uncalibrated hold compares against the ceiling",
          "uncal_max is not None and uncal_total > uncal_max" in gate_src,
          "holding whenever any margin is uncalibrated is a permanent off switch, "
          "because seven of them are uncalibrated on main by design")


def floor_reader_agreement_checks() -> None:
    """Both readers of floor.json must exclude the same things.

    `floor.json` is read TWICE by different languages in different directories:
    `factory/gate.py` decides whether to hold a merge, and `harness/ci.ts` decides
    whether the gate goes red. Adding `UNCALIBRATED_MAX` -- a CEILING rather than a
    floor -- meant teaching both to skip `_MAX`, and only one got taught. The gate then
    demanded a count for a key no rung emits and escalated a green PR with "a floor
    nothing measures is a floor nobody is held to", which is a correct sentence aimed
    at something that is not a floor.

    A change to what a shared file MEANS has to land in every reader of it, and the
    other reader here was in another language in another directory, which is precisely
    why nothing pointed at it.
    """
    import gate as _gate
    floor = _gate.read_floor()
    check("the gate excludes ceilings from the floors it enforces",
          not any(k.endswith("_MAX") for k in floor),
          f"gate.read_floor returned {sorted(k for k in floor if k.endswith('_MAX'))}")
    check("the gate excludes prose keys",
          not any(k.startswith("_") for k in floor))
    ci = config.SHARED / "harness" / "ci.ts"
    if ci.exists():
        src = ci.read_text(encoding="utf-8", errors="replace")
        check("the harness reader excludes ceilings too",
              'endsWith("_MAX")' in src,
              "harness/ci.ts would treat a ceiling as a floor and demand a marker for "
              "it, which is the same bug on the other side of the language boundary")
        check("the harness reader excludes prose keys too",
              'startsWith("_")' in src)


# --- the agent-driven rungs, and what stops them being a sentence -------------
# THE RISK THIS ANSWERS: end-to-end and holdout are now markdown read by a model,
# and a model reporting on its own work is the exact shape of defect this project
# keeps finding -- something announcing success without checking anything. What
# makes the rung a measurement rather than an opinion is that `_validate` rejects
# a report which is not evidence, BEFORE anything is counted. So these checks are
# aimed at the rejections, not at the happy path: a validator that accepts
# everything passes a happy-path test perfectly.

def gh_retry_checks() -> None:
    """A blip is retried. An answer is not.

    THE INCIDENT: one HTTP 503 on `gh issue list` took down a tick, wrote a
    needs-human entry and sent a notification. The next tick, sixty seconds later,
    succeeded. A thirty-second wobble in somebody else's service produced a page and
    a permanent record for a human to clear.

    BOTH DIRECTIONS ARE CHECKED. A retry that never fires is decoration; a retry
    that fires on a 404 asks the same question three times and reports the same
    thing four seconds later. A merge refusal is the case that matters most: it is
    an answer, and retrying it would re-attempt a merge the base branch already
    rejected.
    """
    calls = {"n": 0}
    real_run, real_sleep = state.subprocess.run, state.time.sleep
    state.time.sleep = lambda _s: None
    # The retry says so out loud, which is right in production and noise here --
    # `doctor` runs this file, and a GH_RETRY line in a health report reads as a
    # real upstream problem rather than a test exercising one.
    import contextlib as _ctx
    import io as _io
    _quiet = _ctx.redirect_stderr(_io.StringIO())
    _quiet.__enter__()

    class P:
        def __init__(self, rc, err):
            self.returncode, self.stdout, self.stderr = rc, ("OK" if rc == 0 else ""), err

    def script(seq):
        def fake(*a, **kw):
            i = calls["n"]; calls["n"] += 1
            rc, err = seq[min(i, len(seq) - 1)]
            return P(rc, err)
        return fake

    try:
        calls["n"] = 0
        state.subprocess.run = script([(1, "HTTP 503: Service Unavailable"), (0, "")])
        out = state.gh("issue", "list")
        check("a transient 503 is retried and recovers", out == "OK" and calls["n"] == 2,
              "returned " + repr(out) + " after " + str(calls["n"]) + " attempts")

        calls["n"] = 0
        state.subprocess.run = script([(1, "HTTP 503: Service Unavailable")])
        raised = False
        try:
            state.gh("issue", "list")
        except state.GhError:
            raised = True
        check("a 503 that never clears still fails, after every attempt",
              raised and calls["n"] == 3, "raised=" + str(raised) + " attempts=" + str(calls["n"]))

        for label, err in (("a 404", "HTTP 404: Not Found"),
                           ("a merge refusal", "Pull request is not mergeable")):
            calls["n"] = 0
            state.subprocess.run = script([(1, err)])
            raised = False
            try:
                state.gh("pr", "view", "1")
            except state.GhError:
                raised = True
            check(label + " is an answer and is NOT retried",
                  raised and calls["n"] == 1,
                  "attempts=" + str(calls["n"]) + " -- retrying an answer asks the same "
                  "question three times and reports the same thing, slower")
    finally:
        _quiet.__exit__(None, None, None)
        state.subprocess.run, state.time.sleep = real_run, real_sleep


def teardown_frees_the_port_checks() -> None:
    """Teardown must free the PORT, not merely the process it happens to track.

    THE LEAK: a journey may restart the app, and that replacement is untracked.
    The original dies, the replacement keeps the port, `__exit__` terminates a
    corpse, and the next lap gets "address already in use" from a factory that
    believes it tore everything down. Measured after one morning of gate runs: four
    orphaned interpreters holding four ports.

    Checked at the source, because proving it properly needs a real process on a
    real port and this file is deliberately offline and fast. The behaviour itself
    was verified once by hand, against a deliberate impostor.
    """
    src_path = Path(__file__).resolve().parent.parent / "harness" / "appproc.py"
    if not src_path.exists():
        check("harness/appproc.py exists", False, "there is no process driver")
        return
    src = src_path.read_text(encoding="utf-8")
    body = src.split("def __exit__", 1)[-1].split("def _free_the_port", 1)[0]
    check("HttpApp teardown frees the port, not just its own process",
          "_free_the_port" in body,
          "__exit__ kills self.proc only, so a replacement started by a journey "
          "keeps the port and the next lap cannot bind it")
    check("the port sweep exists", "def _free_the_port" in src)
    check("the port sweep does not kill the process it already terminated",
          "self.proc.pid" in src.split("def _free_the_port", 1)[-1],
          "without the exclusion it re-kills its own pid, which is harmless but "
          "means the guard was never really aimed at the impostor")


def argv_quoting_checks() -> None:
    """A quoted argument must reach the program unquoted.

    THE INCIDENT: commands are split with posix=False so Windows paths keep their
    backslashes, and the quotes were stripped from argv[0] only. So
    `python -c "import app"` reached Python as the three tokens
    python / -c / "import app", and Python evaluated the STRING LITERAL and exited 0.
    Measured: `python -c "import definitely_not_a_module"` also exited 0. The library
    driver's import check could not fail, which means `APP_STARTED driver=library` was
    unconditional -- proof-the-app-ran that proved nothing.
    """
    hpath = str(Path(__file__).resolve().parent.parent / "harness")
    if hpath not in sys.path:
        sys.path.insert(0, hpath)
    try:
        import appproc  # noqa: PLC0415
        import ci  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        check("harness/appproc.py and ci.py import", False, str(e))
        return

    got = appproc._argv('python -c "import definitely_not_a_module"')
    check("the driver hands -c an unquoted argument",
          got[-1] == "import definitely_not_a_module",
          "got " + repr(got[-1]) + " -- quoted, so the interpreter evaluates a string "
          "literal and exits 0 whatever is inside it")
    check("the driver keeps the argument as ONE token", len(got) == 3,
          "got " + repr(got))

    got = ci.resolve(["python", "-c", '"import definitely_not_a_module"'])
    check("the gate ladder hands -c an unquoted argument too",
          got[-1] == "import definitely_not_a_module",
          "got " + repr(got[-1]) + " -- the same hole on the other side of the harness")

    # Backslashes are the reason posix=False is used at all, so they must survive.
    win = '"C:' + chr(92) + 'Program Files' + chr(92) + 'node.exe" -e x'
    got = appproc._argv(win)
    check("a quoted Windows path keeps its backslashes",
          "Program Files" in got[0] and chr(92) in got[0],
          "got " + repr(got[0]))


def ratchet_source_checks() -> None:
    """The floor keys read the markers the harness actually prints.

    THE FAILURE THIS PINS: the agent-driven rungs' floors used to count ASSERTIONS,
    and an agent decides how many assertions a journey needs. Measured on the SAME
    unchanged code: 12, then 13.
    `merge.raise_floor` raises each floor to what the gate just observed, so an
    assertion floor climbs to the luckiest run and then fails every ordinary one --
    a helpful extra check turning into a broken factory two laps later.

    Journeys and scenarios are stable: they are headings in a protected file.
    """
    log = ("HARNESS_START mode=full" + NL + "STATIC_OK" + NL + "UNIT_PASSED tests=30" + NL
           + "APP_STARTED port=1" + NL + "E2E_PASSED journeys=2 steps=12" + NL
           + "HOLDOUT_PASSED scenarios=3 assertions=14" + NL + "MUTATIONS_CAUGHT=8" + NL
           + "GATE_OK mode=full" + NL)
    keys = ["e2e_journeys", "holdout_scenarios", "unit_tests", "mutations_caught"]
    obs = gate.observed_counts(log, keys)
    for key, want in zip(keys, (2, 3, 30, 8)):
        check("the ratchet reads " + key + " from the run log", obs.get(key) == want,
              "got " + repr(obs.get(key)) + ", wanted " + str(want)
              + " -- a floor nothing measures is a floor nobody is held to")

    # Every floor key the template ships must have a source, or the gate reports
    # "a floor nothing measures" on a fresh install and the ratchet is off from day
    # one while looking configured.
    import json as _json
    floor_file = Path(__file__).resolve().parent.parent / ".factory" / "locks" / "floor.json"
    if floor_file.exists():
        raw = _json.loads(floor_file.read_text(encoding="utf-8"))
        shipped = [k for k, v in raw.items()
                   if isinstance(v, int) and not k.startswith("_") and not k.endswith("_MAX")]
        for key in shipped:
            check("the shipped floor key " + key + " has a marker to read",
                  key in gate.FLOOR_SOURCES or obs.get(key) is not None
                  or key in gate.observed_counts(log, [key]),
                  "no source, so the gate cannot enforce it")
        check("the shipped floors do not count agent-chosen assertions",
              "e2e_steps_asserted" not in shipped and "holdout_assertions" not in shipped,
              "an assertion floor plus an auto-raise ratchet climbs to the luckiest run")


def agentcheck_checks() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
    try:
        import agentcheck  # noqa: PLC0415
    except ImportError as e:
        check("harness/agentcheck.py imports", False, str(e))
        return

    def rejects(what: str, payload: object, because: str, kind: str = "e2e",
                says: str = "") -> None:
        """Reject, AND for the stated reason.

        `says` is not decoration. Two guards here reject an empty report
        independently, so a check that asks only "did it raise" stays green while
        either one is deleted -- and a guard nothing measures is a guard that
        leaves whenever somebody is tidying up.
        """
        try:
            agentcheck._validate(kind, payload)
        except agentcheck.AgentCheckFailed as e:
            if says and says not in str(e):
                check(what, False, f"rejected, but for the wrong reason: {e}")
                return
            check(what, True)
            return
        check(what, False, because)

    good = {"journeys": [{"name": "j", "assertions": [
        {"name": "a", "expected": "open=0", "observed": "open=0 from GET /tasks", "ok": True},
    ]}]}
    groups, asserts, failures = agentcheck._validate("e2e", good)
    check("a well-formed result counts its groups and assertions",
          (groups, asserts, failures) == (1, 1, []), f"got {(groups, asserts, failures)}")

    # THE COUNT IS THE POINT. It feeds the ratchet, and a rung that reports zero
    # while exiting 0 is indistinguishable from one that passed.
    rejects("zero journeys is rejected", {"journeys": []},
            "an empty run would have been read as a pass", says="zero journeys")
    rejects("a journey with no assertions is rejected",
            {"journeys": [{"name": "j", "assertions": []}]},
            "a journey that checked nothing would have counted as a journey that passed",
            says="has no assertions")
    rejects("a result with no journeys key is rejected", {"nope": []},
            "an unrecognised shape must not be read as an empty pass")

    # `observed` IS THE EVIDENCE. Everything else in the report is the agent
    # restating what it was asked to do.
    rejects("a missing observed value is rejected",
            {"journeys": [{"name": "j", "assertions": [
                {"name": "a", "expected": "open=0", "ok": True}]}]},
            "an assertion with nothing observed did not run")
    rejects("an empty observed value is rejected",
            {"journeys": [{"name": "j", "assertions": [
                {"name": "a", "expected": "open=0", "observed": "   ", "ok": True}]}]},
            "whitespace is not an observation")
    rejects("observed that merely restates expected is rejected",
            {"journeys": [{"name": "j", "assertions": [
                {"name": "a", "expected": "open=0", "observed": "open=0", "ok": True}]}]},
            "echoing the expectation is the cheapest way to report a check that "
            "never happened")
    rejects("observed that says nothing is rejected",
            {"journeys": [{"name": "j", "assertions": [
                {"name": "a", "expected": "open=0", "observed": "as expected", "ok": True}]}]},
            "'as expected' is a claim, not a measurement")

    # A FAILING ASSERTION MUST SURVIVE VALIDATION, not raise. The two outcomes are
    # different: `ok: false` is the product being broken and belongs in the log as a
    # named failure, while a malformed report is the HARNESS being broken. Collapsing
    # them sends whoever reads the log at 3am to the wrong file.
    bad = {"journeys": [{"name": "j", "assertions": [
        {"name": "the count moves", "expected": "open=0", "observed": "open=1", "ok": False},
        {"name": "b", "expected": "x", "observed": "x observed live", "ok": True},
    ]}]}
    groups, asserts, failures = agentcheck._validate("e2e", bad)
    check("a failing assertion is reported, not raised",
          groups == 1 and asserts == 2 and len(failures) == 1,
          f"got {(groups, asserts, len(failures))}")
    check("the failure text carries both values",
          failures and "open=0" in failures[0] and "open=1" in failures[0],
          "a failure nobody can read is a failure somebody re-runs instead of fixing")

    # The holdout uses `scenarios`, and the two must not be interchangeable: a
    # holdout result shaped like an e2e result would count as an empty holdout.
    rejects("an e2e-shaped result is not accepted where scenarios are required",
            {"journeys": [{"name": "s", "assertions": [
                {"name": "a", "expected": "x", "observed": "y", "ok": True}]}]},
            "wrong key must fail loudly rather than count as zero", kind="holdout")
    sgroups, sasserts, sfail = agentcheck._validate("holdout", {"scenarios": [
        {"name": "s", "assertions": [
            {"name": "a", "expected": "3 tasks", "observed": "3 returned", "ok": True}]}]})
    check("a holdout result validates on the scenarios key",
          (sgroups, sasserts, sfail) == (1, 1, []), f"got {(sgroups, sasserts, sfail)}")

    # NOT CONFIGURED IS A FAILURE, NOT A SKIP. This is the whole reason the rung
    # cannot quietly disappear on a machine where nobody set an agent command.
    try:
        agentcheck.agent_command({"agent": {"cmd": "   "}})
        check("an unset agent command fails rather than skips", False,
              "a gate that drops its end-to-end rung reports green having never "
              "touched the app")
    except agentcheck.AgentCheckFailed:
        check("an unset agent command fails rather than skips", True)


def undefined_module_checks() -> None:
    """Every stdlib module a factory file USES, that file also IMPORTS.

    THE INCIDENT: `gate.py` referenced `os.environ` exactly once, on the line that
    hands the observed counts to the merge, and never imported `os`. That line runs
    only when the markers, the ratchet and the verdict are ALL green -- so no failing
    lap ever reached it, and the FIRST fully green validation this repo ever produced
    died with NameError one statement before the merge it had just earned.

    WHY NOTHING CAUGHT IT. The static rung for a Python project is
    `python -m compileall`, which proves a file parses, not that its names resolve.
    A NameError is a runtime event, and the runtime in question is the rarest path in
    the system: the one where everything else passed.

    This is deliberately narrow -- bare `NAME.attr` where NAME is a stdlib module this
    factory actually uses. It is not a type checker and it is not trying to be; it
    exists to make the specific silence above impossible to repeat.
    """
    import ast

    watched = {
        "os", "sys", "json", "re", "subprocess", "time", "shutil", "tempfile",
        "hashlib", "sqlite3", "socket", "signal", "textwrap", "difflib", "fnmatch",
    }

    # THE HELPERS TOO, not only modules. The module version of this check shipped and
    # then failed to catch `note(...)` in a script that never imported it -- a NameError
    # on the success path of the fix loop, found by running it rather than by reading it.
    # These two are the factory's own output channel: every node either imports them
    # from nodeio or prints directly, and calling one that is not there is the same
    # silence as the missing import, one word narrower.
    helpers = {"note", "emit"}
    here = Path(__file__).resolve().parent
    # BOTH ROOTS. The first version of this scanned only factory/ and therefore never
    # looked at the workflow scripts -- which is exactly where the NameError it was
    # written for actually happened. A check aimed at the wrong directory passes for
    # the same reason a check aimed at nothing passes.
    roots = [here]
    pack = here.parent / ".archon" / "workflows" / "factory"
    if pack.is_dir():
        roots.append(pack)
    for path in sorted(q for root in roots for q in root.rglob("*.py")):
        if path.name.startswith("_test"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            check("selftest/undefined-module " + path.name, False, "does not parse")
            continue

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.asname or alias.name)

        # Names bound anywhere in the file are not module references; a local called
        # `time` shadows the module and is none of this check's business.
        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)
                for arg in node.args.args + node.args.kwonlyargs:
                    bound.add(arg.arg)

        used: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in watched
            ):
                used.add(node.value.id)

        called: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in helpers
            ):
                called.add(node.func.id)

        missing = sorted((used | called) - imported - bound)
        check(
            "selftest/undefined-name " + path.name,
            not missing,
            "uses " + ", ".join(missing) + " without importing or defining it" if missing else "",
        )


def clean_tree_is_not_empty_work_checks() -> None:
    """A script that fails on a clean working tree must also ask whether the BRANCH moved.

    THE INCIDENT, TWICE IN ONE DAY. The build step became Archon's `archon-implement`,
    which commits as it goes. Two scripts asserted "did anything change" by reading
    `git status --porcelain` and treating a clean tree as "the node did nothing":

      commit.py   would have failed the lap for succeeding.
      land-fix.py DID -- it threw away a ten-minute opus fix as "changed nothing",
                  after the fix had been correctly written and committed.

    The question both were asking stopped being the right one the moment the builder
    started committing its own work. What has to be true is that the branch carries
    something, not that the tree is dirty.

    This check is narrow and mechanical: a factory script that reads `--porcelain` and
    can `die`/`exit(1)` on an empty result must also consult `rev-list`. It cannot prove
    the logic is right; it proves the second question is being asked at all, which is
    exactly what was missing both times.
    """
    here = Path(__file__).resolve().parent
    roots = [here]
    pack = here.parent / ".archon" / "workflows" / "factory"
    if pack.is_dir():
        roots.append(pack)

    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.name.startswith("_test") or path.name.startswith("_self"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "--porcelain" not in text:
                continue
            # Only scripts that can STOP on the answer are in scope; a script that merely
            # reports cleanliness is not making a decision this can be wrong about.
            stops = "die(" in text or "sys.exit(1)" in text
            if not stops:
                continue
            check(
                "selftest/clean-tree " + path.name,
                "rev-list" in text,
                "decides on `git status --porcelain` and can stop, but never consults "
                "rev-list -- a builder that commits its own work reads as having done "
                "nothing",
            )


def main() -> int:
    quiet = "--quiet" in sys.argv
    # POINT THE LEDGER SOMEWHERE HARMLESS FOR THE WHOLE RUN, before any check fires.
    # `lock_checks` records settles as a side effect of proving the lock logic.
    import ledger as _led
    _led.LEDGER = Path(tempfile.gettempdir()) / "factory-selftest-ledger.jsonl"
    _led.LEDGER.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        lock_checks(Path(td))
    gate_checks()
    state_checks()
    marker_checks()
    escalation_checks()
    enforcement_checks()
    write_safety_checks()
    deploy_checks()
    duplication_checks()
    refmove_checks()
    watchdog_checks()
    ledger_isolation_checks()
    size_cap_checks()
    run_resolution_checks()
    ratchet_raise_checks()
    uncalibrated_ceiling_checks()
    assumption_count_checks()
    floor_reader_agreement_checks()
    agentcheck_checks()
    ratchet_source_checks()
    argv_quoting_checks()
    teardown_frees_the_port_checks()
    gh_retry_checks()
    undefined_module_checks()
    clean_tree_is_not_empty_work_checks()

    if FAILURES:
        if not quiet:
            print("The factory's own machinery is broken:", file=sys.stderr)
            for f in FAILURES:
                print("  FAIL  " + f, file=sys.stderr)
        print("SELFTEST_FAILED checks=" + str(CHECKS) + " failed=" + str(len(FAILURES)))
        return 1
    if not quiet:
        print("Every machinery invariant holds.")
    print("SELFTEST_PASSED checks=" + str(CHECKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
