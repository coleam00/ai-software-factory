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

import io
import json
import os
import re
import subprocess
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
import ledger as ledger_module  # noqa: E402
import gate  # noqa: E402
import state  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0
NL = chr(10)
CR = chr(13)
CR_LF = CR + NL


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

        # THE ENGINE'S ID SHAPE IS NOT OURS TO ASSUME. A newer Archon writes 32 bare
        # hex characters; a parser that only knew the hyphenated form read every such
        # lock as "names no run" and the pid reaper freed it under a live lap.
        bare = "114b224f5e16b94b312599f0828a3edf"
        lk_bare = config.LOCKS_RUNTIME / "implement-gh-issue-14.lock"
        lk_bare.unlink(missing_ok=True)
        assert dispatch.acquire(lk_bare)
        with lk_bare.open("a", encoding="utf-8") as fh:
            fh.write("run " + bare + "\n")
        check("a bare 32-hex run id is read back off the lock",
              dispatch.lock_run_id(lk_bare) == bare,
              "a lock holding a live run read back as 'names no run' and was reaped "
              "under it; the lap went on to open a pull request")
        check("a bare id does not match inside a 40-character commit sha",
              dispatch.RUN_ID_RE.search(
                  "at 0123456789abcdef0123456789abcdef01234567 on main") is None)
        lk_bare.unlink(missing_ok=True)

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

        # AGE IS NOT EVIDENCE ABOUT A RUN, and the cap that used to act on it is gone.
        # A four-hour lap and one that died in its first minute look identical to a
        # clock, and freeing both meant the sweep escalated live work as dead.
        ancient = config.LOCKS_RUNTIME / "implement-gh-issue-5.lock"
        ancient.write_text(
            "999999 2020-01-01T00:00:00+00:00\nrun " + run_id + "\n", encoding="utf-8"
        )
        very_old = _time.time() - 90 * 24 * 60 * 60
        os.utime(ancient, (very_old, very_old))
        dispatch.reap_locks()
        check("a lock naming a run is never freed by age, however old",
              ancient.exists(),
              "a ninety-day-old lock still names a run, and the only honest way to free "
              "it is an answer about that run")
        ancient.unlink(missing_ok=True)

        # A MANAGED LOCK IS THE SDLC CONSUMER'S, AND NOTHING ELSE MAY FREE IT. Its run
        # settling is not the end of the work: the result still has to be applied, and a
        # lock released on "the engine says it finished" hands the target to the next
        # tick with the verdict not yet recorded.
        managed = config.LOCKS_RUNTIME / "validate-gh-pr-21.lock"
        managed.write_text("0 now\nrecord C:/nowhere/record.json\nrun " + run_id + "\n",
                           encoding="utf-8")
        os.utime(managed, (very_old, very_old))
        dispatch.reap_locks()
        check("a managed lock survives the reaper", managed.exists())
        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "completed"}]})
        check("a managed lock is not freed by the run settling either", managed.exists(),
              "the result has not been applied yet; sdlc.consume frees it, after applying")
        check("and it is recognised as managed", dispatch.managed_lock(managed))

        unmanaged = config.LOCKS_RUNTIME / "validate-gh-pr-22.lock"
        unmanaged.write_text("0 now\nrun " + run_id + "\n", encoding="utf-8")
        check("a lock with no journal is not managed", not dispatch.managed_lock(unmanaged))
        dispatch.release_settled_locks(payload_override={"runs": [
            {"id": run_id, "status": "completed"}]})
        check("and IS freed once the engine says its run settled", not unmanaged.exists(),
              "a lock taken before the journal existed has no other way to be freed")
        check("an unreadable lock counts as managed",
              dispatch.managed_lock(config.LOCKS_RUNTIME / "does-not-exist.lock"),
              "everything that acts on an unmanaged lock FREES it, so a read error must "
              "not be able to turn a live dispatch into an orphan")
        managed.unlink(missing_ok=True)
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
          "exclude |= escalate(" in src,
          "the fix cap parks a target mid-tick as well, and the loop asks again "
          "afterwards")
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
        # AND THE MESSAGE MUST SAY THAT. It used to end "the PR waits for a human to
        # merge it", which is the one thing the table above forbids: there is no
        # held -> passed edge and merge.py refuses anything not passed, so a person
        # following the instruction had to reach for `gh pr merge` and silently skip
        # the ratchet raise, the labels and the issue close. Guidance that contradicts
        # the mechanism is worse than none: it is trusted.
        sdlc_src = (Path(__file__).resolve().parent / "sdlc.py").read_text(encoding="utf-8")
        check("the hold message names `factory accept`, not a raw transition",
              "factory accept" in sdlc_src and "waits for a human to merge it" not in sdlc_src,
              "the comment must name the command that ARCHIVES what was chosen. The first "
              "version of this fix printed `state.py set ... state=open`, which clears the "
              "hold and throws away the record of who agreed and when -- the same mistake "
              "as the original message, one layer down")
        check("the consumer writes held rather than passed when it holds",
              'value = "held"' in sdlc_src,
              "the hold would be a comment and the next tick would merge it")
        # A HOLD NOBODY CAN CLEAR IS A STALL. The assumptions file is re-read on every
        # validation, so without an accept path a held PR holds again on the next one,
        # and the next, forever. The hold shipped before its other half did, and the
        # stall would have looked like a factory with nothing to do.
        check("the hold is taken from a file re-read on every validation",
              "ASSUMPTIONS_DIR" in sdlc_src)

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

        # THE SECOND LINE OF DEFENCE FOR THE HOLD, and for a stale read of any kind. The
        # merge path does not trust that acceptance already decided: it asks the live
        # label before it prepares, and the policy it hands archon-merge asks AGAIN in
        # the instant before the mutation.
        check("merge refuses any state that is not exactly passed",
              sdlc_src.count("""!= "passed\"""") >= 2,
              "one check at dispatch is not enough -- a PR held or parked during the "
              "merge run must not be merged by a policy written before that happened")
        check("the merge policy is revoked before it is rewritten",
              '"authorized": False' in sdlc_src,
              "a crash between the two writes must leave a denial, not the last yes")

        at("open")
        before = len(writes)
        refused = False
        try:
            state.set_state("gh:pr:1", "open")
        except state.IllegalTransition:
            refused = True
        check("re-applying the current state is allowed", not refused and len(writes) > before,
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

    # THE MERGE IS NOT ALWAYS THE DISPATCHER'S. The validate workflow merges inline
    # and prints "NEXT: python factory/deploy.py"; the first dispatcher only deployed
    # after its own merge action, so main advanced and the running service did not.
    # The poll has to run every tick, before the priority order, so a merge from any
    # source reaches a user within one interval.
    disp = (Path(__file__).resolve().parent / "dispatch.py").read_text(encoding="utf-8")
    main_body = disp[disp.find("\ndef main() -> int:"):]
    poll = main_body.find("run_deploy(")
    order = main_body.find("PRIORITY ORDER")
    check("the dispatcher polls the deploy on every tick, before the priority order",
          0 <= poll < order,
          "a merge the validate workflow made was never deployed: three ticks of "
          "'nothing to do' with main ahead of what was running")


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


# The two documents the installed CLI actually prints. Both `workflow run --json` and
# `workflow get --json` go through `writeJsonLine`, which is
# `JSON.stringify(value, null, 2)` -- pretty-printed, many lines tall, and its last line
# is a bare `}`. Reproduced here as data rather than described in a comment, because
# describing it is exactly what a mock does.
CLI_RUN_REPLY = {
    "ok": True, "action": "run", "detached": True,
    "runId": "0f7c4b2e-9a13-4c5d-8b21-6e7f0a1b2c3d",
    "workflow": "archon-accept", "branch": None,
    "conversationId": "6a1d0f3e-2b44-4c19-9f77-1c2d3e4f5a6b",
    "transcriptPath": "/state/runs/0f7c4b2e/transcript.jsonl",
    "logPath": "/state/runs/0f7c4b2e/child.log",
}
CLI_GET_REPLY = {
    "id": "0f7c4b2e-9a13-4c5d-8b21-6e7f0a1b2c3d",
    "workflow_name": "archon-accept",
    "conversation_id": "6a1d0f3e-2b44-4c19-9f77-1c2d3e4f5a6b",
    "parent_conversation_id": None, "codebase_id": "c-1", "status": "completed",
    "outcome": "succeeded", "user_message": "factory validate 3f2a91c0d4e5",
    "metadata": {"total_cost_usd": 1.37},
    "started_at": "2026-02-11T09:14:02.000Z", "completed_at": "2026-02-11T09:41:55.000Z",
    "last_activity_at": "2026-02-11T09:41:55.000Z", "working_path": None,
    "user_id": None, "parent_run_id": None, "adopted_from_run_id": None,
    "output_root": "/state/workspaces/widget",
    "transcript_path": "/state/runs/0f7c4b2e/transcript.jsonl",
}


def as_cli_json(value: object) -> str:
    """What `writeJsonLine` puts on stdout: one pretty-printed document, newline-ended."""
    return json.dumps(value, indent=2) + NL


def run_resolution_checks() -> None:
    """The run id must be the one the ENGINE acknowledged, read out of the WHOLE reply.

    `archon workflow run --detach` used to be read for a "Run id" it printed that
    appeared nowhere in the run record: across four consecutive dispatches the
    timestamps and workflow names matched and the id overlap was ZERO. Keying on it
    meant lock liveness could never be answered, no run ever read back as `completed`,
    and no cost was ever available -- three symptoms, each patched separately, all one
    bug. The cure was a search of the run list for a message the factory itself had
    sent, which is inference: it worked, and it could still match the wrong run.

    `--json` ends that. The launch returns `{ok, runId, ...}` and that id IS the handle,
    written to the journal and to the lock before anything else happens.

    THEN THE REPLY HAS TO BE READ AS THE CLI WRITES IT. The version before this one took
    `lines[-1]`, on the belief that `--json` emits a single line with warnings kept to
    stderr. It does keep warnings to stderr, and it emits a PRETTY-PRINTED document --
    so the last line is `}` and every real dispatch died parsing it. The fixture below
    is the actual shape; the first check is the one that would have caught it.
    """
    import sdlc

    original = sdlc.command
    try:
        check("the CLI's own reply does not fit on one line",
              as_cli_json(CLI_RUN_REPLY).strip().splitlines()[-1] == "}",
              "reading the last line of a --json reply reads a closing brace")

        sdlc.command = lambda argv, **kw: as_cli_json(CLI_RUN_REPLY)
        check("the acknowledgement is read back out of the whole document",
              sdlc.engine(["workflow", "run"])["runId"] == CLI_RUN_REPLY["runId"])

        sdlc.command = lambda argv, **kw: as_cli_json(CLI_GET_REPLY)
        run = sdlc.engine(["workflow", "get", CLI_GET_REPLY["id"]])
        check("and so are the fields consume() settles on",
              (run["id"], run["workflow_name"], run["status"], run["output_root"])
              == (CLI_GET_REPLY["id"], "archon-accept", "completed",
                  CLI_GET_REPLY["output_root"]))
        check("including the cost the ledger records",
              run["metadata"]["total_cost_usd"] == 1.37)
        check("a completed run is terminal and a running one is not",
              run["status"] in sdlc.TERMINAL and "running" not in sdlc.TERMINAL
              and "paused" not in sdlc.TERMINAL,
              "a paused run is waiting for somebody, not finished, and settling it "
              "would read artifacts that are not written yet")

        # EACH OF THESE IS A REPLY THAT CONTAINS SOMETHING PARSEABLE. Cherry-picking
        # the fragment that happens to parse is how a consumer acts on half a document,
        # or on the wrong one of two.
        sdlc.command = lambda argv, **kw: "warning: something" + NL + as_cli_json(CLI_RUN_REPLY)
        refuses("a line printed before the document is a corrupt reply, not noise to skip",
                lambda: sdlc.engine(["workflow", "run"]),
                "--json puts its diagnostics on stderr, so anything else on stdout is "
                "a reply nothing should be read out of")

        sdlc.command = lambda argv, **kw: as_cli_json(CLI_RUN_REPLY) + as_cli_json(CLI_GET_REPLY)
        refuses("two documents in one reply are refused",
                lambda: sdlc.engine(["workflow", "run"]),
                "picking one of them is picking which run this dispatch is about")

        truncated = as_cli_json(CLI_RUN_REPLY)
        sdlc.command = lambda argv, **kw: truncated[:len(truncated) // 2]
        refuses("a truncated document is refused",
                lambda: sdlc.engine(["workflow", "run"]),
                "a short write on a pipe is the failure the CLI's own writer exists to "
                "prevent; reading half of one anyway gives that back")

        sdlc.command = lambda argv, **kw: ""
        refuses("an empty reply is refused, not read as success",
                lambda: sdlc.engine(["workflow", "run"]))

        sdlc.command = lambda argv, **kw: as_cli_json([1, 2, 3])
        refuses("a reply that is not an object is refused",
                lambda: sdlc.engine(["workflow", "run"]))
    finally:
        sdlc.command = original

    src = (Path(__file__).resolve().parent / "sdlc.py").read_text(encoding="utf-8")
    check("the launch refuses a reply with no acknowledged run id",
          'response.get("ok") is not True' in src and "re.fullmatch" in src,
          "a dispatch whose id the factory cannot name is work nothing can settle")
    check("the acknowledged run id matches the one the CLI hands back",
          re.fullmatch(r"[0-9a-fA-F-]{32,36}", CLI_RUN_REPLY["runId"]) is not None,
          "the launch refuses anything else, so the pattern and the CLI have to agree")
    check("the run id is written to the journal before the lock",
          src.index('record.update(run_id=run_id') < src.index('stream.write(f"run {run_id}'),
          "the journal is the record; the lock line is a convenience for the reaper")
    check("the journal exists before the engine is asked for anything",
          src.index("runtime.write(journal, record)") < src.index("response = engine(argv)"),
          "a run started with nothing on disk knowing about it is a run nothing settles")


def ratchet_raise_checks() -> None:
    """The auto-raise may only ever move a floor UP, and only for keys it already has.

    This is the one place the machinery writes the protected floor file, so the property
    that makes it safe has to be asserted rather than argued. A pull request touching
    floor.json is still auto-rejected by the guard; this path runs after the merge, in
    the machinery, and can only tighten. "The floor never falls without a human" IS the
    ratchet, and these checks are what keep that true.

    THE COUNTS ARE AN ARGUMENT NOW. They used to arrive through an environment variable
    on one merge path and through a file on the other, and the file branch shipped with
    a mangled regex that raised at import, was swallowed by the caller, and silently
    never moved a floor on a real merge. One function, two branches, one of them never
    executed by a test. There is one caller and one parameter.
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
        original_git = _merge.git
        _merge.git = lambda *a: (calls.append(a), (0, ""))[1]  # type: ignore[assignment]
        try:
            # observed: one higher, one LOWER, one absent, plus a key not in the floor
            _merge.raise_floor(str(root), {"UNIT_CHECKS": 70, "VITEST_PASSED": 9,
                                           "NEW_KEY": 999, "UNCALIBRATED_MAX": 99})
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

            calls.clear()
            check("no raise means no commit",
                  _merge.raise_floor(str(root), {"UNIT_CHECKS": 70}) == ""
                  and not any("commit" in c for c in calls))

            calls.clear()
            check("a non-integer count changes nothing",
                  _merge.raise_floor(str(root), {"UNIT_CHECKS": "99"}) == ""
                  and _json.loads(floor.read_text(encoding="utf-8"))["UNIT_CHECKS"] == 70,
                  "a string compares greater than every int in some languages and "
                  "raises here in this one; neither is a measurement")

            calls.clear()
            check("no counts at all changes nothing",
                  _merge.raise_floor(str(root), {}) == ""
                  and not any("commit" in c for c in calls),
                  "a gate that produced no measurements must not be able to move a floor")
        finally:
            _merge.git = original_git  # type: ignore[assignment]

    # THE BOOKKEEPING MUST RAISE, NOT RETURN, when it cannot do its job. The caller
    # records it as an effect that has not been applied and retries it next tick;
    # returning quietly marks it done, and the floor silently never moves on a merge
    # that already landed -- the gap the ratchet exists to close, left open with
    # nothing red anywhere.
    original_git = _merge.git
    try:
        listing = (f"worktree C:/repo{NL}branch refs/heads/{config.BASE_BRANCH}{NL}")

        def fake(*a: str) -> tuple:
            if "worktree" in a:
                return (0, listing)
            if "status" in a:
                return (0, " M somefile")
            return (0, "")

        _merge.git = fake  # type: ignore[assignment]
        try:
            _merge.bookkeeping({"head_sha": "0" * 40}, {"counts": {}})
            check("a dirty base checkout stops the bookkeeping loudly", False,
                  "it returned; the caller would record the raise as done")
        except RuntimeError as e:
            check("a dirty base checkout stops the bookkeeping loudly",
                  "uncommitted" in str(e), f"raised {e!r}")

        def diverged(*a: str) -> tuple:
            if "worktree" in a:
                return (0, listing)
            if "rev-parse" in a:
                return (0, "treeA" if "-C" in a else "treeB")
            return (0, "")

        _merge.git = diverged  # type: ignore[assignment]
        try:
            _merge.bookkeeping({"head_sha": "0" * 40}, {"counts": {"UNIT": 9}})
            check("a base that moved past the measured tree stops the raise", False,
                  "the floor would claim coverage measured on a different tree")
        except RuntimeError as e:
            check("a base that moved past the measured tree stops the raise",
                  "advanced past" in str(e), f"raised {e!r}")
    finally:
        _merge.git = original_git  # type: ignore[assignment]


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
    sdlc_src = (Path(__file__).parent / "sdlc.py").read_text(encoding="utf-8")
    check("the hold message counts assumptions via assumption_keys",
          "gate.assumption_keys(assumptions)" in sdlc_src,
          "the consumer is counting something else; line-counting reported 8 as 80")
    check("the hold message does not count raw lines",
          "assumptions.splitlines()" not in sdlc_src,
          "that expression IS the bug: it counts WHY paragraphs as assumptions")
    fixed_src = (Path(__file__).parent / "fixed_gate.py").read_text(encoding="utf-8")
    check("the uncalibrated hold compares against the ceiling",
          "uncalibrated > ceiling" in fixed_src,
          "holding whenever any margin is uncalibrated is a permanent off switch, "
          "because seven of them are uncalibrated on main by design")
    check("and a floor file with no ceiling does not hold at all",
          "isinstance(ceiling, int)" in fixed_src,
          "a missing UNCALIBRATED_MAX must mean 'not measured here', never zero")


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
        try:
            out = state.gh("issue", "list")
        except state.GhError:
            out = None
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
    # THE RULE THAT WAS HERE WAS WRONG, and it cost a full holdout run on a healthy
    # app. It rejected `observed == expected`, reasoning that echoing the expectation
    # is the cheapest way to fake a check. The reasoning is sound and the rule is not:
    # on a PASSING concrete assertion the two are identical BY CONSTRUCTION, because
    # the expectation was written as a value and the app produced that value. It had
    # no discriminating power -- only false positives, on exactly the runs that should
    # pass. On allot it fired on "total 14, held 6, committed 0, available 8", and at
    # level 3 the holdout is a required marker, so nothing could have merged.
    accepted = {"journeys": [{"name": "j", "assertions": [
        {"name": "the item is untouched", "expected": "available 8",
         "observed": "available 8", "ok": True}]}]}
    groups, asserts, failures = agentcheck._validate("e2e", accepted)
    check("a passing concrete measurement equal to its expectation is ACCEPTED",
          (groups, asserts, failures) == (1, 1, []),
          "an honest agent reporting the value it saw must not be called a fabricator")

    # What actually discriminates: the NAME is the question, `observed` is meant to be
    # the answer, and an answer identical to the question answered nothing.
    rejects("observed that restates the assertion's own name is rejected",
            {"journeys": [{"name": "j", "assertions": [
                {"name": "the counter is zero", "expected": "open=0",
                 "observed": "the counter is zero", "ok": True}]}]},
            "restating the question is not answering it", says="restates the assertion")
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


def operator_settings_live_in_config_checks() -> None:
    """A list a human is invited to edit must live in the ONE file the sync will not
    overwrite.

    THE INCIDENT, AND IT WAS SILENT IN BOTH DIRECTIONS. The per-project protected-path
    list was a `PROTECTED +=` block inside `factory/guard.py`. `bin/sync-to.py` copies
    `factory/` wholesale as machinery, so the first sync after an operator added a path
    DELETED it. Nothing went red: the guard still ran, still found no violation, still
    printed `PROTECTED_OK`. Reproduced on flagpole against `app/rollout.py` -- a path a
    planner had refused to build until it was added, removed again hours later by a
    routine `sync-to.py` and observed only because the diff happened to be read.

    Then the fix reproduced the same shape one level up: the new settings were declared
    as `NAME: list[str] = []`, `sync-to.py`'s missing-settings walker matched only
    `ast.Assign`, and it reported nothing missing while the synced guard raised
    AttributeError on the next run.

    Two mechanical invariants, because the class is "an edit that vanishes quietly":

      1. NO SYNCED MODULE INVITES AN EDIT. A comment line made of nothing but quoted
         strings is a commented-out list entry, which is how this template says "add
         yours here". Only `config.py` -- the sync's NEVER list -- may contain one.
      2. THE GUARD SOURCES ITS PROJECT LISTS FROM CONFIG, by attribute and with no
         `getattr` default. A default would substitute an empty list on a stale install
         and print `PROTECTED_OK`, which is the original silent hole restored by the
         fix for it.
    """
    here = Path(__file__).resolve().parent
    invite = re.compile(r'^\s+#\s*"[^"]*"(?:\s*,\s*"[^"]*")*\s*,?\s*$')
    for module in sorted(here.glob("*.py")):
        if module.name == "config.py":
            continue
        offenders = [
            n for n, line in enumerate(module.read_text(encoding="utf-8",
                                                        errors="replace").splitlines(), 1)
            if invite.match(line)
        ]
        check(
            "operator-editable list outside config.py: " + module.name,
            not offenders,
            "lines " + ",".join(str(n) for n in offenders) + " read as commented-out list "
            "entries, i.e. an invitation to edit a file bin/sync-to.py overwrites. Move the "
            "list to config.py, which the sync never touches",
        )

    guard_src = (here / "guard.py").read_text(encoding="utf-8", errors="replace")
    for setting in ("PROTECTED_EXTRA", "BANNED_CATEGORIES", "TEST_PATHS_EXTRA"):
        check(
            "guard reads config." + setting,
            "config." + setting in guard_src,
            "the per-project list must come from config.py or the next sync deletes it",
        )
    # CODE ONLY. The first version scanned the raw text and failed on the comment
    # ABOVE the assignment, which exists to explain why `getattr` is wrong -- a check
    # that punishes writing down its own reasoning, and the second one of those in this
    # sweep. A string-scan invariant has to be told what a comment is.
    guard_code = "\n".join(
        line.split("#", 1)[0] for line in guard_src.splitlines()
    )
    check(
        "guard does not default its project lists away",
        "getattr(config" not in guard_code,
        "a getattr default turns a stale install into a silently unprotected one -- "
        "AttributeError is the correct failure here",
    )

    cfg_src = (here / "config.py").read_text(encoding="utf-8", errors="replace")
    for setting in ("PROTECTED_EXTRA", "BANNED_CATEGORIES", "TEST_PATHS_EXTRA"):
        check(
            "config declares " + setting,
            re.search(r"^" + setting + r"\s*(:|=)", cfg_src, re.M) is not None,
            "guard.py reads it at import, so a missing declaration breaks every gate",
        )


def unreachable_code_checks() -> None:
    """No statement follows a return, raise, break or continue in the same block.

    THE ONE THAT GOT THROUGH. `gate.assumption_keys` ended a branch with two returns:

        return ["(unkeyed " + str(n + 1) + ")" for n in range(len(entries) or 1)]
        return [f"(unkeyed {i + 1})" for i in range(len(paragraphs) or 1)]

    The second is dead, and it references `paragraphs`, which does not exist anywhere in
    the file. Left over from rewriting the expression in place. It reached the branch
    being merged to main, in the module that decides whether a pull request merges.

    Nothing caught it and every check that should have was looking at the wrong thing.
    It PARSES, so `check_all_scripts_parse` passed. It never RUNS, so no test could fail
    on it. `undefined_module_checks` scans for unknown modules and nodeio helpers, not
    local names. And a reviewer's eye slides over a second return the same way it slides
    over a duplicated word.

    Checked structurally rather than by name resolution, because that is the property
    with no false positives: a statement after an unconditional exit in the same block is
    dead however it is spelled. It is also the right shape for the underlying risk --
    dead code in a gate is one careless reorder away from being live code that raises.
    """
    import ast as _ast

    here = Path(__file__).resolve().parent
    workflows = here.parent / ".archon" / "workflows" / "factory"
    files = sorted(here.glob("*.py"))
    if workflows.is_dir():
        files += sorted(workflows.rglob("scripts/*.py"))

    terminal = (_ast.Return, _ast.Raise, _ast.Break, _ast.Continue)
    for path in files:
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue  # check_all_scripts_parse owns that failure
        dead: list[str] = []
        for node in _ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, terminal):
                        dead.append(f"line {block[i + 1].lineno} after line {stmt.lineno}")
        check(
            "no unreachable code in " + path.name,
            not dead,
            "; ".join(dead) + " -- a statement after an unconditional exit never runs, "
            "so nothing can fail on it and it is one reorder away from being live",
        )


def irreversible_scripts_refuse_arguments_checks() -> None:
    """A script whose only action is irreversible must not perform it by accident.

    `regress-trigger.py --help` DISPATCHED A REGRESSION. It took no arguments, ignored
    the ones it got, and went straight to work: a real run against main on the premium
    tier which, at level 4, can file issues into the queue. Anyone typing --help is by
    definition someone who does not yet know what the script does.

    Narrow and mechanical: a factory script that dispatches a workflow, and takes no
    arguments, must read `sys.argv` at all. It cannot prove the handling is right; it
    proves the question is asked, which is exactly what was missing.
    """
    here = Path(__file__).resolve().parent
    for name in ("regress-trigger.py", "trigger.py"):
        path = here / name
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        code = NL.join(line.split("#", 1)[0] for line in src.splitlines())
        if "subprocess" not in code:
            continue
        check(
            name + " looks at its arguments before acting",
            "sys.argv" in code,
            "it dispatches, takes no arguments, and never reads argv -- so any typo, "
            "and --help, starts a real run",
        )


# --- the SDLC consumer -------------------------------------------------------
# The factory dispatches six generic workflows and owns everything they are not
# allowed to touch: the labels, the caps, the dial, the receipts it will act on. That
# boundary is the whole design, and every check below is about a way it can be crossed
# without anything going red -- a receipt for a different pull request, a merge policy
# written before the hold arrived, a repair spent on a cap that had already been
# reached, an apply that half happened and reported success.

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
SHA_A = "a" * 40
SHA_B = "b" * 40
IDENTITY = {"repository": "acme/widget", "pr": 12, "head_sha": SHA_A, "base_sha": SHA_B}


def receipt(**overrides: object) -> dict:
    base = {
        "schema_version": 1,
        "repository": {"owner": "acme", "name": "widget"},
        "pr": 12, "head_sha": SHA_A, "base_sha": SHA_B,
        "verdict": "approve", "summary": "It does what the issue asked for.",
        "findings": [], "clipped": False,
        "timestamp": "2026-01-01T00:00:00Z",
        "work_order_sha256": "w" * 64, "policy_sha256": "p" * 64,
        "evidence_sha256": "e" * 64, "judgment_sha256": "j" * 64,
        "isolation": "fresh_context_only",
        "checks": [{
            "id": "factory-gate", "identity": {**IDENTITY,
                                               "repository": {"owner": "acme", "name": "widget"}},
            "argv": None, "command_sha256": "c" * 64, "source": "trusted_policy",
            "exit_code": 0, "status": "passed",
            "stdout": "accept-private/out.txt", "stderr": "accept-private/err.txt",
            "stdout_sha256": "o" * 64, "stderr_sha256": "r" * 64,
            "timestamp": "2026-01-01T00:00:00Z",
        }],
    }
    base.update(overrides)
    return base


def refuses(what: str, call, detail: str = "") -> None:
    """The check spelling for "this must not be accepted"."""
    try:
        call()
        check(what, False, detail or "it was accepted")
    except Exception:  # noqa: BLE001
        check(what, True)


def receipt_identity_checks() -> None:
    """An acceptance is evidence about ONE candidate, and about nothing else.

    Every field here is what stops a valid receipt from being applied to the wrong
    thing. A receipt for pull request 12 used on 13 approves code nobody judged, and
    every label written afterwards is written successfully.
    """
    import sdlc

    sdlc.validate_receipt(receipt(), IDENTITY)
    check("a complete approval for this exact candidate is accepted", True)

    refuses("a receipt for another pull request is refused",
            lambda: sdlc.validate_receipt(receipt(pr=13), IDENTITY))
    refuses("a receipt for another head is refused",
            lambda: sdlc.validate_receipt(receipt(head_sha="c" * 40), IDENTITY))
    refuses("a receipt for another BASE is refused",
            lambda: sdlc.validate_receipt(receipt(base_sha="c" * 40), IDENTITY),
            "the base is half the identity: the same head merged onto a different base "
            "is a different change")
    refuses("a receipt for another repository is refused",
            lambda: sdlc.validate_receipt(
                receipt(repository={"owner": "acme", "name": "gadget"}), IDENTITY))
    refuses("a repository that is not an owner/name object is refused",
            lambda: sdlc.validate_receipt(receipt(repository="acme/widget"), IDENTITY))
    refuses("an unknown schema version is refused",
            lambda: sdlc.validate_receipt(receipt(schema_version=2), IDENTITY),
            "a version this consumer has not been written against may mean anything")
    refuses("an abbreviated commit id is refused",
            lambda: sdlc.validate_receipt(receipt(head_sha="a" * 12), IDENTITY))
    refuses("an uppercase commit id is refused",
            lambda: sdlc.validate_receipt(receipt(head_sha="A" * 40), IDENTITY),
            "the contract says lowercase, and a comparison that normalises is a "
            "comparison that accepts two spellings of one field")
    refuses("an unknown verdict is refused",
            lambda: sdlc.validate_receipt(receipt(verdict="looks-fine"), IDENTITY))
    refuses("an approval with no execution evidence is refused",
            lambda: sdlc.validate_receipt(receipt(checks=[]), IDENTITY),
            "no checks and no findings is what a judge that never ran produces")
    refuses("an approval carrying findings is refused",
            lambda: sdlc.validate_receipt(
                receipt(findings=[{"code": "x", "summary": "y", "evidence": []}]), IDENTITY))
    refuses("an approval whose evidence was clipped is refused",
            lambda: sdlc.validate_receipt(receipt(clipped=True), IDENTITY),
            "material evidence over the packet budget means the judge did not see it")
    refuses("an approval with a check that did not pass is refused",
            lambda: sdlc.validate_receipt(
                receipt(checks=[{**receipt()["checks"][0], "status": "failed"}]), IDENTITY))
    refuses("an approval with a check that exited non-zero is refused",
            lambda: sdlc.validate_receipt(
                receipt(checks=[{**receipt()["checks"][0], "exit_code": 1}]), IDENTITY),
            "a passed LABEL is not evidence; the exit code is")
    refuses("an approval whose check is about another candidate is refused",
            lambda: sdlc.validate_receipt(
                receipt(checks=[{**receipt()["checks"][0],
                                 "identity": {**receipt()["checks"][0]["identity"], "pr": 13}}]),
                IDENTITY),
            "the evidence would belong to a different pull request than the receipt")
    refuses("an unreadable timestamp is refused",
            lambda: sdlc.validate_receipt(receipt(timestamp="last tuesday"), IDENTITY))
    refuses("a receipt with no summary is refused",
            lambda: sdlc.validate_receipt(receipt(summary=""), IDENTITY))

    # A NON-APPROVAL IS STILL CHECKED FOR IDENTITY, and only for identity. Repair
    # findings have to be about this pull request; requiring passing checks of them
    # would refuse every receipt that exists to say something failed.
    sdlc.validate_receipt(receipt(verdict="request_changes", checks=[],
                                  findings=[{"code": "x", "summary": "y", "evidence": []}]),
                          IDENTITY)
    check("a request_changes receipt does not need passing checks", True)
    refuses("but it still has to be about this candidate",
            lambda: sdlc.validate_receipt(receipt(verdict="request_changes", pr=13), IDENTITY))


class Recorder:
    """A stand-in for everything that talks to GitHub, that remembers what it was told."""

    def __init__(self, state_of: dict, labels: dict | None = None) -> None:
        self.state_of = state_of
        self.labels = labels or {}
        self.transitions: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str]] = []
        self.priorities: list[tuple[str, str]] = []
        self.attempts = 0
        self.notified: list[str] = []

    def fetch(self, target: str) -> dict:
        return {"_state": self.state_of.get(target, "open"),
                "_attempts": self.labels.get(target, 0),
                "_labels": [], "_kind": target.split(":")[1], "_target": target,
                "number": int(target.split(":")[-1]), "state": "OPEN",
                "url": f"https://github.com/acme/widget/issues/{target.split(':')[-1]}",
                "author": {"login": "someone"}, "createdAt": "2026-01-01T00:00:00Z"}

    def set_state(self, target: str, value: str, force: bool = False) -> None:
        self.transitions.append((target, value))
        self.state_of[target] = value

    def set_priority(self, target: str, priority: str) -> None:
        self.priorities.append((target, priority))

    def comment(self, target: str, body: str) -> None:
        self.comments.append((target, body))

    def bump_attempt(self, target: str) -> int:
        self.attempts += 1
        return self.attempts

    def gh(self, *args: str, **kw: object) -> str:
        if args[:2] in (("pr", "view"), ("issue", "view")):
            return json.dumps({"comments": []})
        return ""


def with_consumer(tmp: Path, recorder: "Recorder", body) -> None:
    """Run `body` with the consumer's world replaced by temp files and a recorder.

    NO NETWORK AND NO ENGINE. Everything below exercises the real `sdlc` code; what is
    replaced is the two edges it cannot reach offline -- GitHub and the workflow engine.
    The state machine, the receipt validation, the effect journal and every refusal in
    between are the shipped ones.
    """
    import notify
    import runtime
    import sdlc

    saved = {
        "root": runtime.root, "fetch": state.fetch, "gh": state.gh,
        "set_state": state.set_state, "set_priority": state.set_priority,
        "comment": state.comment, "bump": state.bump_attempt,
        "linked": state.linked_issue, "send": notify.send,
        "identity": sdlc.identity, "repository": sdlc.repository,
        "assumptions": config.ASSUMPTIONS_DIR, "needs_human": config.NEEDS_HUMAN,
        "ledger": ledger_module.LEDGER, "stop": state.stop_requested,
    }
    runtime.root = lambda: tmp
    state.fetch = recorder.fetch
    state.gh = recorder.gh
    state.set_state = recorder.set_state
    state.set_priority = recorder.set_priority
    state.comment = recorder.comment
    state.bump_attempt = recorder.bump_attempt
    state.stop_requested = lambda: (False, "clear")
    notify.send = lambda t, m: recorder.notified.append(m) or "notified"
    sdlc.identity = lambda target, repo: {**IDENTITY, "pr": int(target.split(":")[-1])}
    sdlc.repository = lambda: "acme/widget"
    config.ASSUMPTIONS_DIR = tmp / "assumptions"
    config.NEEDS_HUMAN = tmp / "needs-human.md"
    ledger_module.LEDGER = tmp / "ledger.jsonl"
    try:
        body()
    finally:
        runtime.root = saved["root"]
        state.fetch = saved["fetch"]
        state.gh = saved["gh"]
        state.set_state = saved["set_state"]
        state.set_priority = saved["set_priority"]
        state.comment = saved["comment"]
        state.bump_attempt = saved["bump"]
        state.linked_issue = saved["linked"]
        state.stop_requested = saved["stop"]
        notify.send = saved["send"]
        sdlc.identity = saved["identity"]
        sdlc.repository = saved["repository"]
        config.ASSUMPTIONS_DIR = saved["assumptions"]
        config.NEEDS_HUMAN = saved["needs_human"]
        ledger_module.LEDGER = saved["ledger"]


def journal_for(tmp: Path, **fields: object) -> tuple[Path, dict]:
    import runtime
    record = {"action": "validate", "target": "gh:pr:12", "repository": "acme/widget",
              "manual": False, "status": "running", "applied": [], "run_id": "run-1",
              "identity": dict(IDENTITY), "base_sha": SHA_B,
              "lock": str(tmp / "runs" / "one" / "lock")}
    record.update(fields)
    journal = tmp / "runs" / record.get("run_id", "one") / "record.json"
    runtime.write(journal, record)
    return journal, record


def state_adapter_checks(tmp: Path) -> None:
    """What each workflow returns, turned into this factory's state -- and only that."""
    import runtime
    import sdlc

    # --- admission ----------------------------------------------------------
    rec = Recorder({"gh:issue:5": "untriaged"})

    def admission() -> None:
        journal, record = journal_for(tmp, action="triage", target="gh:issue:5",
                                      run_id="t1", identity=None)
        sdlc.apply(journal, record, {"disposition": "accepted", "priority": "high",
                                     "route": "deliver", "summary": "In scope.",
                                     "assumptions": ["RATE=5 | WHY: measured"],
                                     "rules_cited": ["MISSION 2"]})
        check("an accepted admission labels the issue accepted",
              ("gh:issue:5", "accepted") in rec.transitions)
        check("and writes the priority it decided",
              ("gh:issue:5", "high") in rec.priorities)
        check("and records the assumptions where the merge hold will read them",
              (config.ASSUMPTIONS_DIR / "gh-issue-5.txt").read_text(encoding="utf-8").strip()
              == "RATE=5 | WHY: measured",
              "an assumption the admission made and nothing recorded is a merge that "
              "never gets held")
        check("and comments once, keyed on the run",
              len(rec.comments) == 1 and "factory-run:t1" in rec.comments[0][1])

        journal, record = journal_for(tmp, action="triage", target="gh:issue:6",
                                      run_id="t2", identity=None)
        refuses("a disposition the transition table has never heard of is refused",
                lambda: sdlc.apply(journal, record,
                                   {"disposition": "probably-fine", "priority": "low",
                                    "summary": "x"}),
                "an unknown disposition would be written as a label nothing reads")

    with_consumer(tmp, rec, admission)

    # --- delivery -----------------------------------------------------------
    rec = Recorder({"gh:issue:5": "in-progress"})

    def delivery() -> None:
        pr = {"number": 12, "url": "https://github.com/acme/widget/pull/12",
              "head": "factory/x", "base": config.BASE_BRANCH, "head_sha": SHA_A,
              "base_sha": SHA_B, "repository": "acme/widget", "is_draft": False}
        journal, record = journal_for(tmp, action="implement", target="gh:issue:5",
                                      run_id="i1", identity=None)
        sdlc.apply(journal, record, {"outcome": "delivered", "summary": "Opened.",
                                     "pr": pr, "reports": []})
        check("a delivered pull request arrives awaiting validation",
              ("gh:pr:12", "open") in rec.transitions)
        check("the issue linkage is recorded on disk, not left to prose",
              runtime.read(tmp / "links" / "gh-pr-12.json")["issue"] == "gh:issue:5",
              "`Closes #N` is text an agent wrote, and one run put it inside backticks "
              "so GitHub ignored it entirely")

        journal, record = journal_for(tmp, action="implement", target="gh:issue:7",
                                      run_id="i2", identity=None)
        refuses("a pull request in another repository is refused",
                lambda: sdlc.apply(journal, record,
                                   {"outcome": "delivered", "summary": "x",
                                    "pr": {**pr, "repository": "acme/other"}, "reports": []}))
        journal, record = journal_for(tmp, action="implement", target="gh:issue:8",
                                      run_id="i3", identity=None)
        refuses("a pull request against another base is refused",
                lambda: sdlc.apply(journal, record,
                                   {"outcome": "delivered", "summary": "x",
                                    "pr": {**pr, "base": "some-other-branch"}, "reports": []}),
                "it would be validated against a base nobody chose")
        journal, record = journal_for(tmp, action="implement", target="gh:issue:9",
                                      run_id="i4", identity=None)
        refuses("a candidate that moved between delivery and application is refused",
                lambda: sdlc.apply(journal, record,
                                   {"outcome": "delivered", "summary": "x",
                                    "pr": {**pr, "head_sha": "c" * 40}, "reports": []}),
                "the head we would queue for validation is not the head that was built")

        journal, record = journal_for(tmp, action="fix", target="gh:pr:12", run_id="f1")
        refuses("a repair that opened a REPLACEMENT pull request is refused",
                lambda: sdlc.apply(journal, record,
                                   {"outcome": "delivered", "summary": "x",
                                    "pr": {**pr, "number": 13}, "reports": []}),
                "the attempt cap, the findings and the acceptance are all about the "
                "original; a replacement escapes every one of them")

        rec.transitions.clear()
        journal, record = journal_for(tmp, action="implement", target="gh:issue:10",
                                      run_id="i5", identity=None)
        sdlc.apply(journal, record, {"outcome": "blocked", "summary": "Could not.",
                                     "pr": None, "reports": []})
        check("a delivery that did not deliver escalates the issue",
              ("gh:issue:10", "needs-human") in rec.transitions)
        check("and tells somebody", rec.notified and "Could not." in rec.notified[-1],
              "an escalation nobody is told about is a file nobody opens")

    with_consumer(tmp, rec, delivery)

    # --- acceptance ---------------------------------------------------------
    def acceptance(state_before: str, verdict: str, measured: dict,
                   assumptions: str = "") -> "Recorder":
        rec = Recorder({"gh:pr:12": state_before, "gh:issue:5": "in-progress"})

        def run() -> None:
            state.linked_issue = lambda t: "gh:issue:5"
            journal, record = journal_for(tmp, run_id="v" + verdict[:3] + state_before[:2])
            runtime.write(journal.parent / "measurements.json", measured)
            # NO ASSUMPTIONS UNLESS THIS CASE ASKS FOR THEM. They are read from a
            # directory shared with the admission checks above, and a leaked file turns
            # every "clean approval" case into a hold -- which is the behaviour under
            # test, arriving from the fixture rather than from the code.
            shutil_rm(config.ASSUMPTIONS_DIR)
            if assumptions:
                path = config.ASSUMPTIONS_DIR / "gh-pr-12.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(assumptions, encoding="utf-8")
            sdlc.apply(journal, record, receipt(
                verdict=verdict,
                findings=[] if verdict == "approve" else [{"code": "c", "summary": "s",
                                                           "evidence": []}],
                checks=receipt()["checks"] if verdict == "approve" else []))
        with_consumer(tmp, rec, run)
        return rec

    clean = {"errors": [], "holds": [], "counts": {"UNIT": 9}}
    rec = acceptance("validating", "approve", clean)
    check("a clean approval passes the pull request",
          ("gh:pr:12", "passed") in rec.transitions)

    rec = acceptance("validating", "approve", {"errors": [], "holds": ["ratchet slack"],
                                               "counts": {}})
    check("an approval with a gate hold is HELD, not passed",
          ("gh:pr:12", "held") in rec.transitions,
          "passed is what a mergeable pull request is called; the next tick would merge it")
    check("and the hold says how to clear it",
          any("factory accept" in body for _, body in rec.comments),
          "a hold with no way out is a stall that looks like a factory with nothing to do")

    rec = acceptance("validating", "approve", clean, assumptions="RATE=5 | WHY: measured")
    check("an approval with recorded assumptions is HELD",
          ("gh:pr:12", "held") in rec.transitions,
          "nobody read the diff; an assumption is the one thing a person still has to agree to")
    check("and the hold counts assumptions rather than lines",
          any("1 recorded assumption(s)" in body for _, body in rec.comments),
          "line-counting reported 8 assumptions as 80, which made a reviewable hold look "
          "like an unreviewable wall")

    rec = acceptance("validating", "request_changes", {"errors": ["red"], "holds": [],
                                                       "counts": {}})
    check("request_changes sends the pull request round the repair loop",
          ("gh:pr:12", "failed") in rec.transitions)
    check("and does not wake anybody", not rec.notified,
          "a failing check is what the repair loop is for; escalating it wakes a person "
          "for the ordinary case")

    rec = acceptance("validating", "reject", {"errors": ["red"], "holds": [], "counts": {}})
    check("a rejection parks the pull request AND its issue",
          ("gh:pr:12", "rejected") in rec.transitions
          and ("gh:issue:5", "needs-human") in rec.transitions,
          "an issue left in-progress behind a rejected pull request is an escalation "
          "nothing can see")

    rec = acceptance("validating", "inconclusive", {"errors": [], "holds": [], "counts": {}})
    check("an inconclusive acceptance reaches a person",
          ("gh:pr:12", "needs-human") in rec.transitions and bool(rec.notified),
          "'we could not tell' is not 'it failed' and it is certainly not 'it passed'")

    # AN APPROVAL WITHOUT THE FACTORY'S OWN MEASUREMENTS IS NOT AN APPROVAL. The judge
    # reads streams; the ratchet, the markers and the mutation score are read here, and
    # a receipt that approves while those are red means the fixed gate never ran.
    rec = Recorder({"gh:pr:12": "validating"})

    def missing_measurements() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, run_id="vnomeasure")
        refuses("an approval with no factory measurements is refused",
                lambda: sdlc.apply(journal, record, receipt()),
                "the fixed gate did not run, so nothing checked the markers or the ratchet")
        journal, record = journal_for(tmp, run_id="vredmeasure")
        runtime.write(journal.parent / "measurements.json",
                      {"errors": ["marker absent"], "holds": [], "counts": {}})
        refuses("an approval contradicted by the factory's own gate is refused",
                lambda: sdlc.apply(journal, record, receipt()),
                "when the raw measurements and the judge disagree, the measurements win")
    with_consumer(tmp, rec, missing_measurements)

    # --- merge --------------------------------------------------------------
    def merge_result(**over: object) -> dict:
        base = {"status": "merged", "repository": "acme/widget", "pr": 12,
                "head_sha": SHA_A, "base_sha": SHA_B, "merge_commit": "d" * 40,
                "summary": "Merged."}
        base.update(over)
        return base

    rec = Recorder({"gh:pr:12": "passed", "gh:issue:5": "in-progress"})

    def merged() -> None:
        import merge as merge_module
        state.linked_issue = lambda t: "gh:issue:5"
        calls: list = []
        original = merge_module.bookkeeping
        merge_module.bookkeeping = lambda result, m: calls.append(result)
        try:
            journal, record = journal_for(tmp, action="merge", run_id="m1",
                                          measurements={"counts": {}})
            sdlc.apply(journal, record, merge_result())
            check("a merged pull request is labelled merged and its issue done",
                  ("gh:pr:12", "merged") in rec.transitions
                  and ("gh:issue:5", "done") in rec.transitions)
            check("and the ratchet bookkeeping runs after the remote merge", len(calls) == 1)
            check("the remote merge commit is recorded before any local bookkeeping",
                  runtime.read(journal)["remote_merge"] == "d" * 40,
                  "a ratchet commit that fails afterwards must be a retry, never a lost merge")
            check("a merged pull request is not commented on again",
                  not any(t == "gh:pr:12" for t, _ in rec.comments),
                  "merging it IS the answer")

            journal, record = journal_for(tmp, action="merge", run_id="m2",
                                          measurements={"counts": {}})
            refuses("a merge reported for another candidate is refused",
                    lambda: sdlc.apply(journal, record, merge_result(head_sha="c" * 40)),
                    "that is somebody else's merge, or ours onto something we did not accept")
        finally:
            merge_module.bookkeeping = original
    with_consumer(tmp, rec, merged)

    rec = Recorder({"gh:pr:12": "passed"})

    def held_merge() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, action="merge", run_id="m3",
                                      measurements={"counts": {}})
        sdlc.apply(journal, record, merge_result(status="held", merge_commit="",
                                                 summary="Checks have not reported."))
        check("a held merge changes nothing and says nothing", not rec.transitions
              and not rec.comments,
              "nothing is wrong and nothing happened; the next tick asks again, and a "
              "comment per tick is how a channel gets muted")
    with_consumer(tmp, rec, held_merge)

    # `revalidation_required` IS TWO ANSWERS. The pull request moved, or it did not
    # move and its head still does not contain the base. Revalidating is right for the
    # first and a treadmill for the second, because acceptance judges a head and never
    # updates one -- so the same head is approved and refused again, lap after lap.
    rec = Recorder({"gh:pr:12": "passed"})

    def moved_since_acceptance() -> None:
        state.linked_issue = lambda t: None
        saved = sdlc.identity
        sdlc.identity = lambda target, repo: {**IDENTITY, "head_sha": "c" * 40}
        try:
            journal, record = journal_for(tmp, action="merge", run_id="m4",
                                          measurements={"counts": {}})
            sdlc.apply(journal, record, merge_result(status="revalidation_required",
                                                     merge_commit="",
                                                     summary="The base moved."))
        finally:
            sdlc.identity = saved
        check("a candidate that MOVED is requeued for validation, not escalated",
              ("gh:pr:12", "open") in rec.transitions and not rec.notified,
              "somebody pushed to the branch or its base, which on any repository with "
              "velocity is Tuesday; waking a person for it is how the channel gets muted")
    with_consumer(tmp, rec, moved_since_acceptance)

    rec = Recorder({"gh:pr:12": "passed"})

    def head_behind_base() -> None:
        state.linked_issue = lambda t: None
        for lap, run_id in enumerate(("m4a", "m4b", "m4c")):
            journal, record = journal_for(tmp, action="merge", run_id=run_id,
                                          measurements={"counts": {}})
            sdlc.apply(journal, record, merge_result(status="revalidation_required",
                                                     merge_commit="",
                                                     summary="PR head does not contain "
                                                             "the accepted base."))
            if lap == 0:
                check("a head that does not contain its base is held for a person",
                      ("gh:pr:12", "needs-human") in rec.transitions and bool(rec.notified),
                      "revalidation would approve the same head and be refused again")
        check("and it is never handed back to validation, however many laps run",
              not any(value == "open" for _, value in rec.transitions),
              "one requeue per tick, forever, is the treadmill this exists to stop")
        check("the person is told the command that clears it",
              any("gh pr update-branch 12" in body for _, body in rec.comments)
              and "gh pr update-branch 12" in rec.notified[-1],
              "a hold with no recovery is a stall with good manners")
        check("and it is a fast-forward of the head, never a force-push",
              all("force" not in body or "never a force-push" in body
                  for _, body in rec.comments))
    with_consumer(tmp, rec, head_behind_base)

    rec = Recorder({"gh:pr:12": "passed"})

    def already_merged_differently() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, action="merge", run_id="m5",
                                      measurements={"counts": {}})
        sdlc.apply(journal, record, merge_result(status="revalidation_required",
                                                 merge_commit="e" * 40,
                                                 summary="Merged with other parents."))
        check("a pull request already merged with OTHER parents reaches a person",
              ("gh:pr:12", "needs-human") in rec.transitions,
              "requeueing it for validation would ask the factory to judge something "
              "that has already landed")
    with_consumer(tmp, rec, already_merged_differently)

    rec = Recorder({"gh:pr:12": "passed"})

    def failed_merge() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, action="merge", run_id="m6",
                                      measurements={"counts": {}})
        sdlc.apply(journal, record, merge_result(status="failed", repository="", pr=0,
                                                 head_sha="", base_sha="",
                                                 merge_commit="",
                                                 summary="Unknown remote outcome."))
        check("a failed merge with unknown identity reaches a person",
              ("gh:pr:12", "needs-human") in rec.transitions and bool(rec.notified),
              "after a mutation attempt, a failed readback is an UNKNOWN remote outcome; "
              "retrying it blind is how a pull request merges twice")
    with_consumer(tmp, rec, failed_merge)

    # --- regression ---------------------------------------------------------
    rec = Recorder({})

    def regression() -> None:
        journal, record = journal_for(tmp, action="regress", target="", run_id="r1",
                                      identity=None)
        refuses("a regression that tested a different revision is refused",
                lambda: sdlc.apply(journal, record,
                                   {"status": "clean", "summary": "ok",
                                    "revision": "c" * 40, "base": config.BASE_BRANCH}),
                "a clean report about a commit nobody asked about proves nothing about "
                "the one that merged")
        journal, record = journal_for(tmp, action="regress", target="", run_id="r2",
                                      identity=None)
        sdlc.apply(journal, record, {"status": "inconclusive", "summary": "Could not run.",
                                     "revision": SHA_B, "base": config.BASE_BRANCH})
        check("a regression that is not clean reaches a person", bool(rec.notified))
        check("and there is no target to comment on", not rec.comments)
    with_consumer(tmp, rec, regression)


def apply_recovery_checks(tmp: Path) -> None:
    """A result is applied effect by effect, and a retry does exactly what did not land.

    THE FAILURE THIS EXISTS FOR is a machine that dies between two of the four or five
    writes one verdict produces. Re-applying from the top posts a second comment and
    re-runs a ratchet commit; not re-applying at all loses the half that never happened.
    """
    import runtime
    import sdlc

    rec = Recorder({"gh:pr:12": "validating", "gh:issue:5": "in-progress"})

    def once() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, run_id="rec1")
        runtime.write(journal.parent / "measurements.json",
                      {"errors": [], "holds": [], "counts": {}})
        sdlc.apply(journal, record, receipt())
        first = (len(rec.transitions), len(rec.comments))
        check("applying once writes the state and the comment", first == (1, 1))
        check("and records what it did", set(runtime.read(journal)["applied"]) ==
              {"state:gh:pr:12:passed", "comment"},
              "an effect nothing recorded is an effect a retry performs twice")

        sdlc.apply(journal, runtime.read(journal), receipt())
        check("applying the same result again does nothing",
              (len(rec.transitions), len(rec.comments)) == first,
              "a retry that re-posts is a pull request with the same verdict on it twice")
    with_consumer(tmp, rec, once)

    # A HALF-APPLIED RESULT KEEPS WHAT LANDED. The comment fails; the label already
    # went. A retry must not write the label again and must try the comment again.
    rec = Recorder({"gh:pr:12": "validating"})

    def half() -> None:
        state.linked_issue = lambda t: None
        journal, record = journal_for(tmp, run_id="rec2")
        runtime.write(journal.parent / "measurements.json",
                      {"errors": [], "holds": [], "counts": {}})

        def explode(target: str, body: str) -> None:
            raise RuntimeError("GitHub said no")

        state.comment = explode
        try:
            sdlc.apply(journal, record, receipt())
            check("a failing effect stops the apply", False, "it swallowed the failure")
        except RuntimeError:
            check("a failing effect stops the apply", True)
        recorded = runtime.read(journal)["applied"]
        check("the effect that landed is remembered",
              recorded == ["state:gh:pr:12:passed"],
              "a retry would otherwise re-write the label, and on a state machine that "
              "refuses repeats that turns a recoverable failure into a stuck one")
        state.comment = rec.comment
        sdlc.apply(journal, runtime.read(journal), receipt())
        check("and the retry performs only what did not land",
              len(rec.transitions) == 1 and len(rec.comments) == 1)
    with_consumer(tmp, rec, half)


def dispatch_refusal_checks(tmp: Path) -> None:
    """What must be refused BEFORE anything is spent or any state is moved."""
    import runtime
    import sdlc

    rec = Recorder({"gh:pr:12": "failed", "gh:issue:5": "accepted"})

    def refusals() -> None:
        state.linked_issue = lambda t: "gh:issue:5"
        saved = {"git": sdlc.git, "snapshot": sdlc.snapshot, "live": sdlc.live}
        sdlc.git = lambda *a: SHA_B
        sdlc.snapshot = lambda directory, base: {
            "require_isolation": False, "autonomy": 3, "floor": {},
            "command": "python harness/ci.py", "markers": [], "floor_path": "f.json",
            "slack_caps_autonomy": False, "accept_races": False, "required_checks": []}
        sdlc.live = lambda: {"autonomy": 3, "accept_races": False, "required_checks": []}
        directory = tmp / "prep"
        try:
            # --- the repair path, and the two counters that bound it ---------
            (tmp / "work-orders").mkdir(parents=True, exist_ok=True)
            original_order = 'Build "quoted" behavior.\nKeep A & B and 100% of the requirement.'
            (tmp / "work-orders" / "gh-issue-5.txt").write_text(original_order, encoding="utf-8")
            runtime.write(tmp / "acceptance" / "gh-pr-12.json",
                          {"receipt": str(tmp / "r.json"), "run_id": "v1",
                           "measurements": {}})
            runtime.write(tmp / "r.json",
                          receipt(verdict="request_changes", checks=[],
                                  findings=[{"code": "c", "summary": "s", "evidence": []}]))

            shutil_rm(directory)
            directory.mkdir(parents=True)
            repair = sdlc.prepare("fix", "gh:pr:12", directory, False)
            check("a failed pull request under the cap may be repaired", True)
            check("repair transports the original multiline request as a document",
                  Path(repair["inputs"]["work_order"]).read_text(encoding="utf-8") == original_order)
            check("repair transports structured findings as a document",
                  json.loads(Path(repair["inputs"]["findings"]).read_text(encoding="utf-8"))
                  == [{"code": "c", "summary": "s", "evidence": []}])

            rec.labels["gh:pr:12"] = config.MAX_FIX_ATTEMPTS
            shutil_rm(directory)
            directory.mkdir(parents=True)
            refuses("a pull request at the attempt cap is refused",
                    lambda: sdlc.prepare("fix", "gh:pr:12", directory, False),
                    "FACTORY_RULES 8: the cap is what stops a repair loop from running "
                    "forever on something it cannot fix")
            rec.labels["gh:pr:12"] = 0

            rec.state_of["gh:pr:12"] = "held"
            shutil_rm(directory)
            directory.mkdir(parents=True)
            refuses("a pull request that is not failed has nothing to repair",
                    lambda: sdlc.prepare("fix", "gh:pr:12", directory, False))
            rec.state_of["gh:pr:12"] = "failed"

            runtime.write(tmp / "r.json", receipt())
            shutil_rm(directory)
            directory.mkdir(parents=True)
            refuses("a cold repair with no request_changes findings is refused",
                    lambda: sdlc.prepare("fix", "gh:pr:12", directory, False),
                    "the repair would re-derive the findings from the diff and spend an "
                    "attempt on an objection nobody made")

            # --- strict isolation fails closed --------------------------------
            sdlc.snapshot = lambda directory, base: {
                "require_isolation": True, "autonomy": 3, "floor": {},
                "command": "x", "markers": [], "floor_path": "f.json",
                "slack_caps_autonomy": False, "accept_races": False, "required_checks": []}
            shutil_rm(directory)
            directory.mkdir(parents=True)
            refuses("strict isolation with nothing attesting it refuses to deliver",
                    lambda: sdlc.prepare("implement", "gh:issue:5", directory, False),
                    "no provider this factory dispatches to enforces an execution "
                    "boundary; running anyway would deliver without the thing that was "
                    "asked for and say nothing")
        finally:
            sdlc.git, sdlc.snapshot, sdlc.live = saved["git"], saved["snapshot"], saved["live"]
    with_consumer(tmp, rec, refusals)


def evaluation_profile_checks(tmp: Path) -> None:
    """What acceptance and the regression are actually handed, against their contracts.

    THESE ARE SOMEBODY ELSE'S PARSERS. `archon-accept` fails an operator profile closed
    on an unknown field, on a command with no `public_description` under a declared
    `gate`, or on evidence it can prove is not this evaluation's output.
    `archon-regress` declares `public_probe_scope` as a workflow INPUT, so the same name
    on the profile is an unknown field AND a run that silently never probes. None of
    that is visible in a dispatch that "succeeded": it comes back as an inconclusive
    verdict hours later.
    """
    import hashlib
    import runtime
    import sdlc

    rec = Recorder({"gh:pr:12": "open"})
    base_files = {"MISSION.md": "Ship the thing." + NL,
                  "FACTORY_RULES.md": "1. One issue at a time." + NL}

    def trusted(autonomy: int, probe: str) -> dict:
        return {"require_isolation": False, "autonomy": autonomy, "floor": {},
                "command": "python harness/ci.py", "markers": [], "floor_path": "f.json",
                "slack_caps_autonomy": False, "accept_races": False, "required_checks": [],
                "accept_report": ".factory/acceptance-report.json",
                "public_probe_scope": probe}

    def profiles() -> None:
        saved = {"git": sdlc.git, "snapshot": sdlc.snapshot, "live": sdlc.live,
                 "linked": state.linked_issue, "body": state.body_text}
        sdlc.git = lambda *a: base_files.get(a[-1].split(":", 1)[-1], SHA_B)
        sdlc.snapshot = lambda directory, base: trusted(
            4, "Run the static checks and the unit tests.")
        sdlc.live = lambda: {"autonomy": 4, "accept_races": False, "required_checks": []}
        state.linked_issue = lambda t: "gh:issue:5"
        # A MULTI-LINE REQUEST WITH WINDOWS LINE ENDINGS, which is what a GitHub issue
        # body is on half the machines that file one.
        state.body_text = lambda t: CR_LF.join(["Add the endpoint.", "", "It must page."])
        directory = tmp / "validate"
        try:
            shutil_rm(directory)
            directory.mkdir(parents=True)
            record = sdlc.prepare("validate", "gh:pr:12", directory, False)
            policy = runtime.read(directory / "policy.json")

            check("the acceptance profile declares the gate it is handing over",
                  policy["gate"] == {"complete": True,
                                     "description": sdlc.GATE_COMPLETENESS},
                  "without it the judge cannot tell a whole protected gate from an "
                  "arbitrary command that exited zero, and is right to say so")
            check("and every command carries the public description that declaration needs",
                  all(c.get("public_description") for c in policy["commands"]),
                  "upstream fails a gate declaration without them CLOSED")
            check("the judge is given the governance it cannot fetch for itself",
                  [c["source"] for c in policy["context"]]
                  == ["base:MISSION.md", "base:FACTORY_RULES.md"],
                  "it has no tools; a pointer from one document to another is a pointer "
                  "it cannot follow")
            check("that context is read from the BASE, never from the candidate",
                  all(c["source"].startswith("base:") for c in policy["context"]),
                  "a candidate that supplies its own mission has written its own rubric")
            check("and the gate owes back a report bound to the evaluation",
                  policy["required_evidence"] == [".factory/acceptance-report.json"],
                  "it is the only thing the judge sees of a private gate's result")
            check("the profile carries no field the upstream parser has not declared",
                  set(policy) == {"schema_version", "commands", "gate", "context",
                                  "required_evidence", "protected_paths",
                                  "require_isolation"},
                  "unknown fields and unknown versions fail closed: " + str(sorted(policy)))
            check("its commands are the shape that parser accepts",
                  all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", c["id"]) and c["argv"]
                      and all(1 <= code <= 255 for code in c["environment_exit_codes"])
                      for c in policy["commands"]),
                  "lowercase kebab ids, a nonempty argv, and declared environment exits "
                  "in 1..255")

            # THE DIGESTS ARE OF BYTES SOMEBODY ELSE RE-READS. Acceptance reads both
            # files with Node and hashes exactly what is on disk, so this side has to
            # hash the same thing or every receipt about them is refused.
            order = (directory / "work-order.txt").read_bytes()
            check("the work order is hashed as the bytes acceptance will read",
                  record["work_order_sha256"] == hashlib.sha256(order).hexdigest())
            check("and a multi-line request is written with LF endings on every platform",
                  CR not in order.decode("utf-8") and NL in order.decode("utf-8"),
                  "Path.write_text translates newlines on Windows, so the file the judge "
                  "read and the string this side hashed were two different documents")
            check("the policy is hashed as its bytes too",
                  record["policy_sha256"] == hashlib.sha256(
                      (directory / "policy.json").read_bytes()).hexdigest())
            check("and the work order reaches acceptance as an external file",
                  record["inputs"]["work_order"]
                  == "file:" + str(directory / "work-order.txt"),
                  "acceptance refuses a work-order path inside the candidate checkout")

            directory = tmp / "regress"
            shutil_rm(directory)
            directory.mkdir(parents=True)
            record = sdlc.prepare("regress", "", directory, False)
            check("the public probe is a workflow INPUT, not a field on the profile",
                  record["inputs"]["public_probe_scope"]
                  == "Run the static checks and the unit tests."
                  and "public_probe_scope" not in runtime.read(directory / "policy.json"),
                  "on the profile it is an unknown field, and the run never probes")
            check("the private gate is still the profile the regression runs first",
                  runtime.read(directory / "policy.json")["argv"][-1] == "--regression",
                  "a public probe can never waive a failed full gate; it only runs after "
                  "that gate has come back non-clean")
            check("publication follows the dial and nothing else",
                  record["inputs"]["publish"] is True)

            sdlc.snapshot = lambda directory, base: trusted(sdlc.PUBLISH_LEVEL - 1, "")
            shutil_rm(directory)
            directory.mkdir(parents=True)
            record = sdlc.prepare("regress", "", directory, False)
            check("below the publishing level the regression still runs and reports",
                  record["inputs"]["publish"] is False
                  and record["inputs"]["public_probe_scope"] == "",
                  "the flood control is the dial; turning it down must not stop the "
                  "scheduled run from telling anybody what it found")
        finally:
            sdlc.git, sdlc.snapshot, sdlc.live = saved["git"], saved["snapshot"], saved["live"]
            state.linked_issue, state.body_text = saved["linked"], saved["body"]

    with_consumer(tmp, rec, profiles)


def shutil_rm(path: Path) -> None:
    import shutil as _shutil
    if path.exists():
        _shutil.rmtree(path)


def stop_and_dial_checks(tmp: Path) -> None:
    """The STOP button and the dial, asked again at the moment of spending.

    Preparation reads GitHub and git and can take a minute. A STOP raised during that
    minute has to land, or the button only works while nothing is happening -- which is
    exactly when nobody needs it.
    """
    import sdlc

    saved_stop, saved_live = state.stop_requested, sdlc.live
    try:
        sdlc.live = lambda: {"autonomy": 2, "accept_races": False, "required_checks": []}
        state.stop_requested = lambda: (False, "clear")
        check("the dial gates an automatic dispatch",
              sdlc.authorized("validate", False) and not sdlc.authorized("merge", False),
              "level 2 validates and does not merge; that IS the dial")
        check("a person may run a step the dial has not reached",
              sdlc.authorized("validate", True) and sdlc.authorized("triage", True),
              "`factory run` is somebody asking for one unit of work by hand")
        check("but not a merge",
              not sdlc.authorized("merge", True),
              "a merge's authority comes from an acceptance receipt, never from whoever "
              "typed the command")

        state.stop_requested = lambda: (True, "the STOP file exists")
        check("STOP refuses everything, including by hand",
              not any(sdlc.authorized(a, m) for a in sdlc.LEVELS for m in (True, False)),
              "one brake, one place to look")
    finally:
        state.stop_requested, sdlc.live = saved_stop, saved_live

    import dispatch

    def stop_during_preparation() -> None:
        saved = (sdlc.prepare, sdlc.engine, dispatch.lock_path)
        calls = []

        def prepare(*args):
            state.stop_requested = lambda: (True, "STOP raised during preparation")
            return {"inputs": {}, "base_sha": SHA_B}

        def engine(argv):
            calls.append(argv)
            return {"ok": True, "runId": "a" * 32}

        try:
            tmp.mkdir(parents=True, exist_ok=True)
            dispatch.lock_path = lambda *args: tmp / "launch.lock"
            sdlc.prepare, sdlc.engine = prepare, engine
            refused = False
            try:
                sdlc.launch("regress", "", manual=True)
            except ValueError as error:
                refused = "changed during preparation" in str(error)
            check("STOP raised during preparation refuses dispatch", refused)
            check("STOP raised during preparation never calls the engine", calls == [])
        finally:
            sdlc.prepare, sdlc.engine, dispatch.lock_path = saved

    with_consumer(tmp, Recorder({}), stop_during_preparation)


def merge_policy_checks(tmp: Path) -> None:
    """The merge authorization is rewritten immediately before every read of it."""
    import runtime
    import sdlc

    def policy_for(pr_state: str, dial: int, assumptions: str = "") -> dict:
        rec = Recorder({"gh:pr:12": pr_state})
        out: dict = {}

        def run() -> None:
            state.linked_issue = lambda t: None
            saved = sdlc.live
            sdlc.live = lambda: {"autonomy": dial, "accept_races": False,
                                 "required_checks": ["ci"]}
            try:
                if assumptions:
                    path = config.ASSUMPTIONS_DIR / "gh-pr-12.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(assumptions, encoding="utf-8")
                directory = tmp / ("policy-" + pr_state + str(dial) + str(bool(assumptions)))
                directory.mkdir(parents=True, exist_ok=True)
                out.update(sdlc.merge_policy(
                    {"target": "gh:pr:12", "repository": "acme/widget"}, directory))
                out["_path"] = str(directory / "policy.json")
            finally:
                sdlc.live = saved
        with_consumer(tmp, rec, run)
        return out

    check("a passed pull request at level 3 is authorized",
          policy_for("passed", 3)["authorized"] is True)
    check("the same pull request at level 2 is not",
          policy_for("passed", 2)["authorized"] is False,
          "the dial is the operator's, and archon-merge rereads this file in the "
          "instant before it mutates")
    check("a HELD pull request is not authorized",
          policy_for("held", 3)["authorized"] is False,
          "the hold is the second line of defence, and this is where it holds")
    check("a pull request with recorded assumptions is not authorized",
          policy_for("passed", 3, "RATE=5 | WHY: measured")["authorized"] is False,
          "somebody has to agree to an assumption before code nobody read merges on it")
    policy = policy_for("passed", 3)
    check("the policy names the stop file the merge must reread",
          policy["stop_file"] == str(config.STOP_FILE),
          "a stop button the merge cannot see is a stop button you do not have")
    check("and the checks the operator requires", policy["required_checks"] == ["ci"])
    check("and never asks for the races to be accepted by default",
          policy["accept_races"] is False,
          "accepting them is an explicit statement about branch protection, and turning "
          "the dial up is not that statement")

    # REVOKED FIRST. A crash between the two writes must leave a denial.
    rec = Recorder({"gh:pr:12": "passed"})

    def revocation() -> None:
        state.linked_issue = lambda t: None
        directory = tmp / "revoke"
        directory.mkdir(parents=True, exist_ok=True)
        runtime.write(directory / "policy.json", {"authorized": True})
        saved = sdlc.live
        sdlc.live = lambda: {"autonomy": 3, "accept_races": False, "required_checks": []}
        original = runtime.write
        seen: list = []

        def spy(path: Path, value: object) -> None:
            if path.name == "policy.json":
                seen.append(dict(value))  # type: ignore[arg-type]
            original(path, value)

        runtime.write = spy
        try:
            sdlc.merge_policy({"target": "gh:pr:12", "repository": "acme/widget"}, directory)
        finally:
            runtime.write = original
            sdlc.live = saved
        check("the old authorization is revoked before the new one is written",
              len(seen) == 2 and seen[0]["authorized"] is False,
              "a crash between the two must leave a denial, not the last yes")
    with_consumer(tmp, rec, revocation)


def artifact_binding_checks(tmp: Path) -> None:
    """A result is read out of THE run that produced it, and out of nowhere else.

    Two validations of two pull requests write `acceptance.json` into two artifact
    directories. A consumer that reaches for the newest one applies the second run's
    verdict to the first run's target -- and every label it writes afterwards is
    written successfully.
    """
    import runtime
    import sdlc

    home = tmp / "home"
    root = home / "artifacts" / "runs" / "run-1"
    root.mkdir(parents=True)
    (root / "acceptance.json").write_text("{}", encoding="utf-8")

    resolved = sdlc.artifact_root({"id": "run-1", "output_root": str(home)}, "run-1")
    check("the artifacts of a run are found under its own persisted output root",
          resolved == root.resolve())
    refuses("a reply about a DIFFERENT run is refused",
            lambda: sdlc.artifact_root({"id": "run-2", "output_root": str(home)}, "run-1"),
            "that is somebody else's result, arriving with a valid shape")
    refuses("a run that persisted no output root is refused",
            lambda: sdlc.artifact_root({"id": "run-1", "output_root": None}, "run-1"),
            "guessing where it wrote is how a consumer reads another run's directory")

    check("an artifact inside the run resolves",
          sdlc.artifact(root, "acceptance.json").name == "acceptance.json")
    refuses("an artifact path that climbs out of the run is refused",
            lambda: sdlc.artifact(root, "../../secrets.json"),
            "the receipt names its own stream files, and a receipt is a document the "
            "run under judgement had a hand in producing")

    receipt_with = receipt(evidence_sha256=hashlib_sha256(b"packet"),
                           checks=[{**receipt()["checks"][0],
                                    "stdout": "out.txt", "stderr": "err.txt",
                                    "stdout_sha256": hashlib_sha256(b"hello"),
                                    "stderr_sha256": hashlib_sha256(b"")}])
    (root / "out.txt").write_bytes(b"hello")
    (root / "err.txt").write_bytes(b"")
    (root / "accept-private").mkdir()
    (root / "accept-private" / "evidence.json").write_bytes(b"packet")
    sdlc.verify_evidence(receipt_with, root)
    check("evidence that still matches its digests is accepted", True)

    (root / "out.txt").write_bytes(b"hello, actually it passed")
    refuses("a stream that no longer matches its digest is refused",
            lambda: sdlc.verify_evidence(receipt_with, root),
            "a digest nobody re-computes is decoration on a document produced beside "
            "the code it is about")


def hashlib_sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def flood_cap_checks(tmp: Path) -> None:
    """FACTORY_RULES 1: a DELAY, not a wastebasket, and never a way to lock yourself out.

    The cap is applied before a triage costs anything. What it must get right is who it
    applies to (not the owner), how it counts (issues listed, never the search index,
    which lags by minutes on exactly the day somebody is running a flood), and that
    yesterday's held issues come back.
    """
    import sdlc

    class Flood(Recorder):
        def __init__(self, author: str, today: list[int], held: list[dict]) -> None:
            super().__init__({})
            self.author = author
            self.today = today
            self.held = held
            self.edits: list[tuple] = []

        def fetch(self, target: str) -> dict:
            item = super().fetch(target)
            item["author"] = {"login": self.author}
            item["createdAt"] = TODAY + "T09:00:00Z"
            return item

        def gh(self, *args: str, **kw: object) -> str:
            if args[:2] == ("repo", "view"):
                return json.dumps({"owner": {"login": "owner"}})
            if args[:2] == ("issue", "list") and "factory:rate-limited" in args:
                return json.dumps(self.held)
            if args[:2] == ("issue", "list"):
                return json.dumps([
                    {"number": n, "author": {"login": self.author},
                     "createdAt": TODAY + "T0" + str(i) + ":00:00Z"}
                    for i, n in enumerate(self.today)])
            if args[:2] == ("issue", "edit"):
                self.edits.append(args)
            return ""

    cap = config.ISSUE_CAP_PER_DAY
    under = list(range(1, cap + 1))

    rec = Flood("stranger", under, [])
    with_consumer(tmp, rec, lambda: check(
        "an issue inside the cap is triaged", sdlc.flood_allowed("gh:issue:" + str(under[-1]))))

    over = under + [99]
    rec = Flood("stranger", over, [])
    with_consumer(tmp, rec, lambda: check(
        "the one past the cap is held", not sdlc.flood_allowed("gh:issue:99")))
    check("and it is labelled so a person can see why",
          any("factory:rate-limited" in a for a in rec.edits),
          "an issue silently ignored is indistinguishable from one nobody has got to")

    rec = Flood("owner", over, [])
    with_consumer(tmp, rec, lambda: check(
        "the repository owner is exempt", sdlc.flood_allowed("gh:issue:99"),
        "you should not be able to lock yourself out of your own factory by filing "
        "four issues in a morning"))

    rec = Flood("stranger", over, [{"number": 3, "createdAt": "2020-01-01T00:00:00Z"}])
    with_consumer(tmp, rec, lambda: sdlc.flood_allowed("gh:issue:99"))
    check("yesterday's held issues are unstuck first",
          any("--remove-label" in a and "3" in a for a in rec.edits),
          "without this the cap is permanent for anyone who ever tripped it, which "
          "makes it a wastebasket rather than a delay")

    class Broken(Flood):
        def gh(self, *args: str, **kw: object) -> str:
            if args[:2] == ("issue", "list"):
                raise RuntimeError("GitHub returned 503")
            return super().gh(*args, **kw)

    rec = Broken("stranger", over, [])
    with_consumer(tmp, rec, lambda: check(
        "an unreachable API does not stop every triage in the factory",
        sdlc.flood_allowed("gh:issue:99"),
        "a thirty-second blip in somebody else's service must not read as a flood"))

    src = (Path(__file__).resolve().parent / "sdlc.py").read_text(encoding="utf-8")
    check("the count comes from a listing, never the search index",
          "--search" not in src,
          "the search index lags issue creation by minutes, and a cap computed from a "
          "stale index lets the flood straight through on the day somebody runs one")


def fixed_gate_checks(tmp: Path) -> None:
    """The one command acceptance, publication and the regression all run.

    EVERY CHECK IS A POSITIVE ASSERTION, and the reason is the whole design: a check
    that never ran produces no failures, and "did anything fail?" reads that as success.
    So `measure` is asked what the log PROVES, and the cases below are the ways a log
    can prove nothing while looking fine.
    """
    import fixed_gate
    import runtime

    green = (NL.join([
        "PREFLIGHT_OK secrets_ignored=5", "PROTECTED_OK", "TRIPWIRE_CLEAR",
        "APP_STARTED", "E2E_PASSED journeys=3", "UNIT_PASSED tests=64",
        "MUTATIONS_TOTAL=9", "MUTATIONS_CAUGHT=9", "GATE_OK",
        "BALANCE_CLAIMS=10 FAILED=0 UNCALIBRATED=2",
    ]) + NL)
    floor = {"e2e_journeys": 3, "unit_tests": 64}

    saved = (config.REQUIRED_MARKERS, config.SLACK_CAPS_AUTONOMY)
    config.REQUIRED_MARKERS = ["PROTECTED_OK", "APP_STARTED", "E2E_PASSED", "GATE_OK"]
    config.SLACK_CAPS_AUTONOMY = False
    try:
        clean = fixed_gate.measure(green, 0, floor)
        check("a complete green run has no errors and no holds",
              clean["errors"] == [] and clean["holds"] == [], str(clean))
        check("and its counts are what the ratchet will be raised to",
              clean["counts"].get("unit_tests") == 64 and clean["counts"].get("e2e_journeys") == 3)

        missing = fixed_gate.measure(green.replace("APP_STARTED" + NL, ""), 0, floor)
        check("a required marker that never appeared is an error",
              any("APP_STARTED" in e for e in missing["errors"]),
              "a check that did not report that it ran, and did not report that it "
              "failed, did not run")

        red = fixed_gate.measure(green, 1, floor)
        check("a non-zero exit is an error", any("exited 1" in e for e in red["errors"]))

        unmeasured = fixed_gate.measure(green, 0, {**floor, "holdout_scenarios": 2})
        check("a floor nothing measures is an error, not a pass",
              any("holdout_scenarios" in e for e in unmeasured["errors"]),
              "a floor nobody is held to looks configured the whole time")

        short = fixed_gate.measure(green, 0, {**floor, "unit_tests": 99})
        check("fewer checks than the floor requires is an error",
              any("64 of a required 99" in e for e in short["errors"]),
              "the rest were skipped, and skipped is not passed")

        escaped = fixed_gate.measure(green.replace("MUTATIONS_CAUGHT=9",
                                                   "MUTATIONS_CAUGHT=8"), 0, floor)
        check("a deliberate defect the gate missed is an error",
              any("8 of 9" in e for e in escaped["errors"]),
              "every miss is a class of bug that can currently merge unreviewed")

        none_injected = fixed_gate.measure(green.replace("MUTATIONS_TOTAL=9",
                                                         "MUTATIONS_TOTAL=0"), 0, floor)
        check("zero deliberate defects injected is an error",
              any("never failed" in e for e in none_injected["errors"]),
              "a gate that has never failed is a gate nobody has tested")

        anchor_moved = fixed_gate.measure(green + "MUTATIONS_NOT_INJECTED=1" + NL, 0, floor)
        check("a defect that could not be injected is an error",
              any("could not be injected" in e for e in anchor_moved["errors"]),
              "a mutation set that silently stops injecting reports a perfect score "
              "for doing nothing")

        # HOLDS ARE NOT ERRORS. Green, and waiting for a person to agree with a call
        # the factory made. Reporting one as a failure sends the pull request round the
        # repair loop for something no repair can fix.
        ceiling = fixed_gate.measure(green, 0, {**floor, "UNCALIBRATED_MAX": 1})
        check("uncalibrated margins over the ceiling HOLD rather than fail",
              ceiling["errors"] == [] and any("uncalibrated" in h for h in ceiling["holds"]),
              str(ceiling))
        under = fixed_gate.measure(green, 0, {**floor, "UNCALIBRATED_MAX": 7})
        check("and under the ceiling they do not hold at all", under["holds"] == [],
              "seven margins are uncalibrated on main by design; holding on any of them "
              "is a permanent off switch")
        no_ceiling = fixed_gate.measure(green, 0, floor)
        check("a floor file with no ceiling does not hold either",
              no_ceiling["holds"] == [],
              "absent must mean 'not measured here', never zero")

        config.SLACK_CAPS_AUTONOMY = True
        slack = fixed_gate.measure(green, 0, {**floor, "unit_tests": 60})
        check("ratchet slack holds only when the operator asked it to",
              any("slack" in h for h in slack["holds"]) and slack["errors"] == [])
        config.SLACK_CAPS_AUTONOMY = False
        check("and not otherwise",
              fixed_gate.measure(green, 0, {**floor, "unit_tests": 60})["holds"] == [],
              "every merge closes the slack it opened, so holding on it would deadlock "
              "the pull requests that ADD tests")
    finally:
        config.REQUIRED_MARKERS, config.SLACK_CAPS_AUTONOMY = saved

    # --- the structural half, which must actually run ------------------------
    import guard
    import tripwire

    saved_guard = (guard.preflight, guard.main, tripwire.main)
    calls: list = []
    try:
        guard.preflight = lambda: (print("PREFLIGHT_OK secrets_ignored=5"), 0)[1]
        guard.main = lambda argv: (calls.append(argv), print("PROTECTED_OK"), 0)[2]
        tripwire.main = lambda argv: (print("TRIPWIRE_CLEAR"), 0)[1]
        code, log = fixed_gate.structural({"base_sha": SHA_B}, False)
        check("the structural checks run and pass", code == 0)
        check("the guard is run against the base revision from the profile",
              calls and calls[0] == ["--base", SHA_B, "--head", "HEAD"],
              "a guard run against whatever the checkout thinks base is compares the "
              "wrong two trees, and the diff it judges is not this change")
        check("their markers come from the checks themselves, not from the gate",
              "PROTECTED_OK" in log and "PREFLIGHT_OK" in log and "TRIPWIRE_CLEAR" in log,
              "an earlier version prepended the string PROTECTED_OK because the guard "
              "had returned zero -- a marker the gate wrote about itself")

        guard.main = lambda argv: 1
        check("a guard violation stops the gate as a candidate failure",
              fixed_gate.structural({"base_sha": SHA_B}, False)[0] == 1)
        guard.main = lambda argv: 2
        check("a guard that COULD NOT RUN is an environment failure, not a verdict",
              fixed_gate.structural({"base_sha": SHA_B}, False)[0] == 75,
              "'we could not check' recorded as 'we checked and it failed' sends the "
              "repair loop after a broken machine")

        guard.main = lambda argv: 0
        tripwire.main = lambda argv: 1
        check("a builder artifact in the validator's tree stops the gate",
              fixed_gate.structural({"base_sha": SHA_B}, False)[0] == 1)
        check("but publication does not run the tripwire at all",
              fixed_gate.structural({"base_sha": SHA_B}, True)[0] == 0,
              "the builder's own tree is full of the builder's own artifacts; that is "
              "only a leak when it reaches an independent judge")

        guard.preflight = lambda: 1
        check("a secret that is not gitignored stops everything, first",
              fixed_gate.structural({"base_sha": SHA_B}, False)[0] == 1,
              "a broad `git add` inside a publish step is publication, not a mistake "
              "you can take back")
    finally:
        guard.preflight, guard.main, tripwire.main = saved_guard

    # --- the report acceptance reads back -----------------------------------
    #
    # A fixed profile keeps this gate's argv and its streams private, so the judge is
    # shown this file and nothing else of it. What has to be true is that it exists on
    # every acceptance exit, that it is bound to the evaluation that asked for it, and
    # that what it carries is aggregate rather than raw.
    saved_guard = (guard.preflight, guard.main, tripwire.main)
    saved_root, saved_report = config.ROOT, config.ACCEPT_REPORT
    saved_env = {key: os.environ.get(key) for key in fixed_gate.ACCEPT_BINDINGS}
    candidate = tmp / "candidate"
    candidate.mkdir(parents=True, exist_ok=True)
    identity = {"repository": {"owner": "acme", "name": "widget"}, "pr": 12,
                "head_sha": SHA_A, "base_sha": SHA_B}
    report_path = candidate / ".factory" / "acceptance-report.json"
    try:
        guard.preflight = lambda: 0
        guard.main = lambda argv: 0
        tripwire.main = lambda argv: 0
        os.environ["ACCEPT_EVALUATION_ID"] = "eval-9"
        os.environ["ACCEPT_IDENTITY"] = json.dumps(identity)
        config.ACCEPT_REPORT = ".factory/acceptance-report.json"
        profile = {"base_sha": SHA_B, "markers": [], "slack_caps_autonomy": False,
                   "floor": {}, "result": str(tmp / "measurements.json"),
                   "command": "this-command-does-not-exist --please"}
        runtime.write(tmp / "gate.json", profile)

        cwd = os.getcwd()
        os.chdir(candidate)
        try:
            code = fixed_gate.main([str(tmp / "gate.json")])
        finally:
            os.chdir(cwd)
        check("a gate command that cannot start reports an ENVIRONMENT exit", code == 75,
              "declared to acceptance as environment, because a gate that could not run "
              "must never be recorded as a gate that ran")
        written = runtime.read(report_path)
        check("and it STILL writes the report acceptance requires",
              written["gate_status"] == "environment" and written["schema_version"] == 1,
              "a missing required-evidence file reads as evidence that could not be "
              "verified, which turns every gate failure into an inconclusive one")
        check("the report is bound to the evaluation that asked for it",
              written["evaluation_id"] == "eval-9" and written["identity"] == identity,
              "a report bound to another evaluation or another head cannot certify this "
              "candidate, and upstream refuses it")
        check("and its evidence is a nonempty string", isinstance(written["evidence"], str)
              and written["evidence"].strip() != "",
              "upstream requires one; an empty field fails the whole evaluation closed")

        report_path.unlink()
        # The gate command itself is the project's, so it is stood in for here: what is
        # under test is that a run which REACHED it reports what it measured.
        profile.update(markers=["PROTECTED_OK", "GATE_OK"], floor={"unit_tests": 64},
                       command="the-project-gate")
        runtime.write(tmp / "gate.json", profile)
        ran = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=NL.join(["PROTECTED_OK", "GATE_OK", "UNIT_PASSED tests=64"]) + NL,
            stderr="")
        saved_run = fixed_gate.subprocess.run
        fixed_gate.subprocess.run = lambda *a, **kw: ran
        os.chdir(candidate)
        try:
            code = fixed_gate.main([str(tmp / "gate.json")])
        finally:
            os.chdir(cwd)
            fixed_gate.subprocess.run = saved_run
        written = runtime.read(report_path)
        check("a green gate reports passed with the markers that reported",
              code == 0 and written["gate_status"] == "passed"
              and written["required_markers"] == {"PROTECTED_OK": True, "GATE_OK": True},
              str(written))
        check("and it carries counts and tallies rather than raw failures",
              written["blocking_errors"] == 0 and written["merge_holds"] == 0
              and written["measured_counts"] == {"unit_tests": 64},
              "the judge is a builder-facing context; raw evaluator output in one is a "
              "holdout that has stopped being a holdout")
        check("nothing in the report quotes the gate's command or its streams",
              "the-project-gate" not in json.dumps(written)
              and "UNIT_PASSED" not in written["evidence"],
              "the argv and the log are exactly what a fixed profile keeps private")

        report_path.unlink()
        os.chdir(candidate)
        try:
            code = fixed_gate.main([str(tmp / "gate.json"), "--publication"])
        finally:
            os.chdir(cwd)
        check("publication writes NO report into the tree it is checking",
              code == 0 and not report_path.exists(),
              "nothing is judging a receipt at publication time, and a file dropped into "
              "a delivery worktree is a file that can be committed")

        del os.environ["ACCEPT_EVALUATION_ID"]
        refuses("acceptance with no evaluation binding refuses before it spends anything",
                lambda: fixed_gate.main([str(tmp / "gate.json")]),
                "a report the judge cannot bind is one it must refuse, and discovering "
                "that after the gate has run has burned the whole run")
    finally:
        guard.preflight, guard.main, tripwire.main = saved_guard
        config.ROOT, config.ACCEPT_REPORT = saved_root, saved_report
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def trusted_snapshot_checks(tmp: Path) -> None:
    """The judge is rebuilt from the BASE tree, and refuses when it cannot be.

    This is the root of trust for every protected path. A gate imported from the branch
    under test is a gate that branch can edit: one commit that set a floor to 1 AND
    changed `if violations:` to `if False:` printed both violations, printed
    PROTECTED_OK, and exited 0.
    """
    import sdlc

    files = {
        "factory/config.py": "AUTONOMY = 3",
        "factory/guard.py": "PROTECTED = []",
        "factory/gate.py": "",
        "factory/tripwire.py": "",
        "factory/fixed_gate.py": "",
        "factory/runtime.py": "",
        ".factory/locks/floor.json": '{"unit_tests": 3}',
    }

    def fake_git(*args: str) -> str:
        if args[0] == "ls-tree":
            return NL.join(sorted(files))
        if args[0] == "show":
            return files[args[1].split(":", 1)[1]]
        return SHA_B

    saved = (sdlc.git, sdlc.settings)
    try:
        sdlc.git = fake_git
        sdlc.settings = lambda directory: {
            "autonomy": 3, "command": "python harness/ci.py", "markers": ["GATE_OK"],
            "slack_caps_autonomy": False, "floor_path": ".factory/locks/floor.json",
            "require_isolation": False, "accept_races": False, "required_checks": []}

        directory = tmp / "ok"
        directory.mkdir(parents=True)
        profile = sdlc.snapshot(directory, SHA_B)
        check("the trusted set is written out of the base tree",
              (directory / "trusted" / "guard.py").read_text(encoding="utf-8").strip()
              == "PROTECTED = []",
              "a copy taken from the working checkout is a copy of whatever is there, "
              "which in a validation is the candidate")
        check("and the profile carries the base revision the guard will diff against",
              profile["base_sha"] == SHA_B)
        check("and the ratchet floor from that same revision",
              profile["floor"] == {"unit_tests": 3},
              "a floor read from the candidate is a floor the candidate chose")

        removed = files.pop("factory/guard.py")
        directory = tmp / "no-guard"
        directory.mkdir(parents=True)
        refuses("a base tree with no guard refuses rather than falling back",
                lambda: sdlc.snapshot(directory, SHA_B),
                "falling back to the branch's own copy IS the original bug")
        files["factory/guard.py"] = removed

        floor_entry = files.pop(".factory/locks/floor.json")
        directory = tmp / "no-floor"
        directory.mkdir(parents=True)
        check("a base tree with no ratchet floor is a day-one factory, not an error",
              sdlc.snapshot(directory, SHA_B)["floor"] == {},
              "a repository that has not set a floor yet must still be able to validate")
        files[".factory/locks/floor.json"] = floor_entry
    finally:
        sdlc.git, sdlc.settings = saved


def sdlc_checks(tmp: Path) -> None:
    """Everything above, with the consumer's own reporting kept out of the results.

    These paths print: a notification, a rate-limit reason, a ratchet line. That output
    is a feature in production and noise here, and noise around a list of failures is
    how somebody reads past the one line that mattered.
    """
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        receipt_identity_checks()
        state_adapter_checks(tmp / "adapters")
        apply_recovery_checks(tmp / "recovery")
        dispatch_refusal_checks(tmp / "refusals")
        stop_and_dial_checks(tmp / "dial")
        merge_policy_checks(tmp / "policy")
        artifact_binding_checks(tmp / "artifacts")
        flood_cap_checks(tmp / "flood")
        evaluation_profile_checks(tmp / "profiles")
        fixed_gate_checks(tmp / "gate")
        trusted_snapshot_checks(tmp / "snapshot")


def main() -> int:
    quiet = "--quiet" in sys.argv
    # POINT THE LEDGER SOMEWHERE HARMLESS FOR THE WHOLE RUN, before any check fires.
    # `lock_checks` records settles as a side effect of proving the lock logic.
    import ledger as _led
    _led.LEDGER = Path(tempfile.gettempdir()) / "factory-selftest-ledger.jsonl"
    _led.LEDGER.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        lock_checks(Path(td))
        sdlc_checks(Path(td) / "sdlc")
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
    operator_settings_live_in_config_checks()
    unreachable_code_checks()
    irreversible_scripts_refuse_arguments_checks()

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
