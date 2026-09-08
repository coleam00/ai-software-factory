"""The dispatcher. Component 2, built last on purpose.

    python factory/dispatch.py              dispatch at most MAX_PARALLEL things, exit
    python factory/dispatch.py --dry-run    say what it would do, do nothing

It answers exactly one question -- "what, if anything, should run right now?" --
from a fixed priority order and the labels on GitHub. NO MODEL IS CONSULTED.

That is not a stylistic preference. A model asked "what work is pending?" will
invent dispatches for issues that were never filed and PRs that do not exist. It is
a plausible-sounding answer with nothing behind it, and the factory then acts on it.
The dumbest component in the system is the one where a wrong answer is worse than no
answer.

NOTHING PUSHES. Filing an issue does not trigger a run. There is no webhook and
there is not meant to be one: a scheduler wakes on a timer, reads the state, and
dispatches. An issue filed at 09:01 waits for the next tick. A push trigger that
breaks fails SILENTLY and looks exactly like a factory with nothing to do; a poll
that breaks is a poll you can see not running.

From cron, once the dial is above 0. Slower than feels right:
    */30 * * * * cd /path/to/repo && python factory/dispatch.py >> factory.log 2>&1
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
# Bound to `eventlog` rather than `ledger`: this module already defines a function
# called `ledger()` (the needs-human.md writer), and shadowing it would silently turn
# every escalation record into a call on the wrong object.
import ledger as eventlog  # noqa: E402
import notify  # noqa: E402
import state  # noqa: E402
import watchdog  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

DRY_RUN = "--dry-run" in sys.argv

# Every action, and the dial level it requires. The dial is enforced HERE, in code,
# rather than documented in a file -- raising it is then a deliberate act rather
# than a note nobody read.
REQUIRES_LEVEL = {
    "implement": 1,
    "fix": 1,
    "validate": 2,
    "merge": 3,
    "triage": 4,
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def ledger(target: str, why: str) -> None:
    config.NEEDS_HUMAN.parent.mkdir(parents=True, exist_ok=True)
    with config.NEEDS_HUMAN.open("a", encoding="utf-8") as fh:
        fh.write(
            f"- {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  {target}  "
            f"(dispatcher)  {why}\n"
        )


def escalate(target: str, why: str) -> set[str]:
    """Park it, record it, tell someone. All three, or it is not an escalation.

    Returns EVERY target it parked, the linked issue included, so a caller can keep
    the same tick from dispatching one of them out of a stale read.
    """
    log(f"ESCALATE {target}: {why}")
    parked = {target}
    if DRY_RUN:
        return parked
    try:
        state.set_state(target, "needs-human", force=True)
    except Exception as e:  # noqa: BLE001
        log(f"  (could not label {target}: {e})")
    ledger(target, why)
    # AND THE ITEM BEHIND IT. A PR parked at needs-human whose issue still reads
    # `in-progress` is an escalation nothing can see: `next` moves on to unrelated
    # work while the escalated issue sits in a state that means "being worked on"
    # with nothing working on it.
    try:
        if target.startswith("gh:pr:"):
            issue = state.linked_issue(target)
            if issue:
                state.set_state(issue, "needs-human", force=True)
                ledger(issue, f"its PR {target} escalated: {why}")
                parked.add(issue)
    except Exception:  # noqa: BLE001
        pass
    for t in parked:
        eventlog.record(eventlog.ESCALATE, target=t, reason=why[:300])
    log(notify.send(target, why))
    return parked


# BOTH SHAPES. One Archon build writes hyphenated UUIDs, a newer one writes 32 bare
# hex characters, and the factory has to read the id it was given back off its own
# lock either way. The first version matched only the hyphenated form; on the bare
# form every lock read back as "names no run", the five-minute pid reaper freed it
# under a live lap, and the reconcile sweep escalated a lap that went on to open a
# pull request. The word boundaries keep a bare id from matching the first 32
# characters of a 40-character commit sha in the same log.
RUN_ID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{32})\b"
)
# A run in any of these is over. Anything else -- INCLUDING a status this version of
# the engine has never been seen to emit -- counts as still running, because the
# cost of guessing wrong in that direction is only a delay.
# `not_found` is here deliberately: see run_status(). The engine saying it has no
# such run is a settled outcome, not an unknown one.
SETTLED_STATUSES = {"not_found", "completed", "failed", "cancelled", "canceled", "abandoned",
                    "error", "errored", "timeout", "timed_out", "stopped"}


# --- locks --------------------------------------------------------------------
# Labels are good shared state and a BAD LOCK. There is no compare-and-swap: two
# dispatchers reading `factory:accepted` both claim the issue, because read-then-
# write is not atomic and nothing in the API makes it so. So the mutex lives here,
# on disk, per (workflow, target) pair.


def lock_path(action: str, target: str) -> Path:
    key = f"{action}-{target}".replace("/", "-").replace(".", "-").replace(":", "-")
    return config.LOCKS_RUNTIME / f"{key}.lock"


def acquire(path: Path) -> bool:
    """Atomically, or not at all.

    `if not exists: write` is a time-of-check-to-time-of-use race, and it is not
    theoretical: two dispatchers started in the same second both pass the test and
    both dispatch on the same PR, so two runs edit one worktree and the second
    judges a tree the first is still writing. Reachable whenever a tick outlives the
    cron interval, or a human runs the dispatcher while cron fires.

    O_EXCL makes exactly one of the racers the winner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
    return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def reap_locks() -> None:
    """Reap dead locks BEFORE counting capacity.

    THE WEDGE THIS REMOVES, and it is the most likely one on a real machine. The lock
    is released when the run settles. It is NOT released when the process is KILLED:
    a reboot, the machine sleeping, a power cut, someone closing the terminal, the
    OOM killer. The lock file then survives its owner, counts toward capacity
    forever, and every subsequent tick logs "at capacity, nothing dispatched" and
    exits 0 -- indistinguishable from a factory with nothing to do.

    THE PID ON THE LOCK IS NOT THE RUN'S PID, and this is the whole subtlety.
    Dispatch is detached: `archon workflow run` hands the work to a child and returns
    in seconds, so the recorded pid is dead almost immediately while the run has
    another twenty minutes to go. The first version of this reaped on "pid gone AND
    older than GRACE", and its docstring said a live lap is never touched because its
    pid is alive. That sentence was false for every dispatch this system makes: an
    implement lap ran nine minutes, its lock was reaped at five, and the reconcile
    sweep escalated it as dead while it went on to finish and open a pull request.

    So the pid test applies to exactly one case: a lock with NO run id, meaning the
    dispatch died before it could record one. There the pid IS the only owner there
    ever was.

    A LOCK THAT NAMES A RUN IS NEVER FREED BY AGE, and the cap that used to do it is
    gone. Age is not evidence about a run: a four-hour lap and one that died in its
    first minute look identical to a clock, and freeing both meant the sweep escalated
    live work as dead. Such a lock is freed by `sdlc.consume` once the run has settled
    and its result has been applied, or -- for a lock taken before this factory kept
    that record -- by release_settled_locks() asking the engine about that run.
    """
    config.LOCKS_RUNTIME.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for lock in config.LOCKS_RUNTIME.glob("*.lock"):
        try:
            age_min = (now - lock.stat().st_mtime) / 60
            first = lock.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue

        if lock_run_id(lock) or managed_lock(lock):
            continue

        if age_min > config.LOCK_GRACE_MINUTES:
            head = first.split(" ")[0]
            if head.isdigit() and not pid_alive(int(head)):
                log(
                    f"LOCK_REAPED {lock.name} - it names no run, its dispatching process "
                    f"({head}) is gone, and it is over {config.LOCK_GRACE_MINUTES}m old"
                )
                lock.unlink(missing_ok=True)


def in_flight() -> list[Path]:
    config.LOCKS_RUNTIME.mkdir(parents=True, exist_ok=True)
    return sorted(config.LOCKS_RUNTIME.glob("*.lock"))


# --- dispatch -----------------------------------------------------------------

def dispatch(action: str, target: str) -> bool:
    """Hand ONE unit of work to the SDLC consumer, detached, and return.

    The dispatcher never waits: a tick that blocks for twenty minutes is a tick that
    overlaps the next one. Which workflow, which inputs, which trusted profile and
    which state moves are `sdlc.launch`'s -- this module's whole remaining job is
    deciding that this is the thing to do now.
    """
    if DRY_RUN:
        log(f"DRY-RUN would dispatch {action} {target}")
        return not lock_path(action, target).exists()
    import sdlc
    return sdlc.launch(action, target)


def _grace_minutes() -> float:
    try:
        return float(os.environ.get("FACTORY_PROBE_GRACE_MINUTES", "") or 5.0)
    except ValueError:
        return 5.0


PROBE_GRACE_MINUTES = _grace_minutes()


def lock_age_minutes(lock: Path) -> float:
    try:
        return (time.time() - lock.stat().st_mtime) / 60
    except OSError:
        return 0.0


def run_status(run_id: str) -> str | None:
    """Ask the engine about ONE run. None when it genuinely cannot be answered.

    `archon workflow runs --json` reports a WINDOW (20 runs). A lock whose run has
    aged out of it is not "unknown" in any deep sense -- the engine still knows, it
    just was not asked. Before this, such a lock sat until the 180-minute stale cap
    and the factory ran at zero capacity the whole time, reporting "at capacity,
    nothing dispatched" once a minute. On a busy day that window fills in under an
    hour, so the wedge is the normal case rather than an edge one.

    Only ever called as a FALLBACK, for ids the bulk list did not mention, so it costs
    one extra query per stuck lock rather than one per lock per tick.
    """
    if not run_id:
        return None
    try:
        out = subprocess.run(
            [config.ARCHON_BIN, "workflow", "get", run_id, "--json"],
            cwd=str(config.ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        # NOT gated on returncode: `not_found` exits 1 while still printing a
        # perfectly good JSON answer, and returning early on the exit code threw that
        # answer away and re-created the wedge this function exists to remove.
        raw = out.stdout or ""
        offsets = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
        if not offsets:
            return None
        payload = json.loads(raw[min(offsets):])
        status = payload.get("status")
        if status:
            return str(status).lower()
        # "NOT FOUND" IS AN ANSWER, and it has to be told apart from silence.
        #
        # The engine replying `{"ok": false, "error": "not_found"}` is it stating that
        # no such run exists -- a run that was never persisted, or has been pruned.
        # Nothing will ever hold that lock, so keeping it until the 180-minute stale
        # cap runs the factory at zero capacity for three hours over a run that is not
        # merely finished but was never there.
        #
        # This does NOT weaken the "empty is not an answer" rule the rest of this file
        # is built on. An unreachable engine, a timeout, or an unparseable reply all
        # still return None and still keep the lock. The difference is between being
        # told nothing and being told no.
        if payload.get("error") == "not_found" or payload.get("ok") is False:
            return "not_found"
        return None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return None


def run_cost(run_id: str) -> float | None:
    """What one settled run cost, or None if it cannot be read.

    Called ONLY on settle, which happens a few times an hour, because the bulk
    `workflow runs --json` payload carries no cost and the per-run query takes a
    couple of seconds. Doing this per tick would make the dispatcher slower than the
    work it dispatches.

    None rather than 0.0 on failure. Zero is a claim ("this was free") and would
    quietly deflate the spend detector to the point where it never fires.
    """
    if not run_id:
        return None
    try:
        out = subprocess.run(
            [config.ARCHON_BIN, "workflow", "get", run_id, "--json"],
            cwd=str(config.ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if out.returncode != 0:
            return None
        raw = out.stdout or ""
        offsets = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
        if not offsets:
            return None
        meta = json.loads(raw[min(offsets):]).get("metadata") or {}
        cost = meta.get("total_cost_usd")
        return float(cost) if cost is not None else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError, TypeError):
        return None


def managed_lock(lock: Path) -> bool:
    """Whether an SDLC dispatch journal owns this lock.

    UNREADABLE COUNTS AS OWNED. Everything that acts on a lock this returns False for
    ends up FREEING it, so a transient read error must not be able to turn a live
    dispatch into an orphan -- the same asymmetry release_settled_locks() is built on.
    """
    try:
        return any(line.startswith("record ") for line in
                   lock.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return True


def lock_run_id(lock: Path) -> str:
    """The Archon run id recorded on a lock, or empty if it carries none.

    Read the `run <id>` line dispatch() wrote rather than pattern-matching the file:
    the id is whatever the engine said it was, and a parser that only recognises one
    engine's id shape turns a held lock into a reaped one (see RUN_ID_RE).
    """
    try:
        text = lock.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("run "):
            return line[4:].strip()
    m = RUN_ID_RE.search(text)
    return m.group(1) if m else ""


def release_settled_locks(payload_override: dict | list | None = None,
                          status_probe: "Callable[[str], str | None] | None" = None) -> None:
    """Release a lock only when the run that took it is PROVABLY finished.

    The only question that can be answered honestly here is about a run id. The
    dispatching process exits the instant the run detaches, so its PID proves
    nothing, and the engine's run list does not report a branch -- so anything that
    infers liveness from a name is guessing.

    The first version of this guessed, and the guess collapsed to nothing: it built
    the set of active *branches*, every entry came back blank, the blanks were
    filtered out, and each lock was then compared against an empty set. `any()` over
    an empty set is False, so it concluded "no run holds this" for every lock and
    released all of them one tick after they were taken. The reconcile sweep then
    found a live lap holding no lock and escalated it as dead -- while it was still
    running, and while it went on to finish.

    EMPTY IS NOT AN ANSWER. Every unknown below KEEPS the lock. A lock held too long
    stalls one target and is visible in every tick's capacity line; a lock dropped too
    early runs two writers over one worktree and escalates work that was going fine.
    Those costs are not symmetric.
    """
    # LOCKS THIS FACTORY KEEPS A JOURNAL FOR ARE NOT ASKED ABOUT HERE. Their run's
    # result still has to be applied after it settles, and freeing the lock on
    # "the engine says it finished" would release the target to the next tick with the
    # verdict not yet recorded. `sdlc.consume` frees those, after applying.
    #
    # What is left is a lock taken by a dispatch that predates that journal -- a
    # factory upgraded while a lap was in flight. Nothing else knows what those runs
    # were, and asking the engine about the run they name is the only honest way to
    # find out they are over.
    locks = [lk for lk in in_flight() if lock_run_id(lk) and not managed_lock(lk)]
    if not locks:
        return
    # THE PROBE IS INJECTABLE, and it defaults to OFF for a caller supplying a payload.
    #
    # A caller that hands in `payload_override` is asserting something about THAT
    # payload -- that a windowed list omitting a run is not evidence the run ended, say.
    # Left wired to the live engine, the fallback answered those tests from production
    # data and three invariants flipped from proving the rule to proving whatever the
    # engine happened to say. A test that silently reaches the network is not testing
    # the thing it names.
    # A GRACE PERIOD BEFORE THE PROBE IS TRUSTED, and it was paid for immediately.
    #
    # `not_found` for a run dispatched SECONDS ago is a race, not a verdict: the engine
    # has not persisted the row yet. Without this, the very first tick after a dispatch
    # released the live lock, the reconcile sweep then found the PR in `validating`
    # with nothing holding it, and escalated a running validation to needs-human. The
    # fix for a three-hour wedge created a three-second one that was strictly worse,
    # because it killed work that was going fine.
    #
    # Only locks older than the grace are probed. A young lock falls through to the
    # bulk list exactly as before.
    probe = status_probe or (run_status if payload_override is None else (lambda _rid: None))
    if payload_override is not None:
        payload = payload_override
    else:
        try:
            out = subprocess.run(
                [config.ARCHON_BIN, "workflow", "runs", "--json"],
                cwd=str(config.ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            if out.returncode != 0:
                return
            raw = out.stdout or ""
            offsets = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
            if not offsets:
                return
            payload = json.loads(raw[min(offsets):])
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            return

    runs = payload.get("runs", []) if isinstance(payload, dict) else payload
    if not isinstance(runs, list):
        return
    status_by_id = {
        str(r.get("id") or r.get("runId") or ""): str(r.get("status", "")).lower()
        for r in runs if isinstance(r, dict)
    }
    status_by_id.pop("", None)
    if not status_by_id:
        # Unreadable or empty. That is silence, not "nothing is running" -- and
        # keeping those two apart is the entire job of this function.
        return

    for lock in locks:
        run_id = lock_run_id(lock)
        status = status_by_id.get(run_id)
        if status is None and lock_age_minutes(lock) >= PROBE_GRACE_MINUTES:
            # Not in the reported window. Ask about this run SPECIFICALLY before
            # falling back to the stale cap: "the bulk list did not mention it" and
            # "the engine cannot tell us" are different answers, and treating the
            # first as the second wedges capacity to zero for three hours.
            status = probe(run_id)
        if status is None:
            continue        # genuinely unanswerable; unknown, so keep
        if status in SETTLED_STATUSES:
            log(f"LOCK_RELEASED {lock.name} - run {run_id[:8]} is {status}")
            eventlog.record(eventlog.SETTLE, run=run_id, status=status,
                            target=lock.stem, cost_usd=run_cost(run_id))
            lock.unlink(missing_ok=True)


def run_deploy(reason: str, quiet_noop: bool = False) -> None:
    """Run the deploy poll and REPORT what it did. Called after the dispatcher's own
    merge, and once per tick regardless.

    THE MERGE IS NOT ALWAYS THE DISPATCHER'S. When the validate workflow merges
    inline it prints "NEXT: python factory/deploy.py" and finishes, and the first
    version of this only deployed on the dispatcher's own merge action -- so main
    advanced, the running service did not, and the loop the fifth component exists
    to close stayed open. Found on a real VPS: pull request merged at 16:49, three
    ticks of "nothing to do", the live page serving the new template through the old
    process. deploy.py already no-ops when nothing changed, so polling it every tick
    costs one fetch and is the mechanism its own docstring asks for: a poll cannot be
    silently skipped.
    """
    deploy = Path(__file__).parent / "deploy.py"
    if not deploy.exists():
        return
    # THE RESULT IS CHECKED. This used to discard it, so a deploy that failed after
    # a successful merge was completely silent: the code landed, the deploy broke,
    # and the lap reported clean. It never mattered while FACTORY_DEPLOY_CMD was
    # unset, because deploy.py then prints DEPLOY_NOT_CONFIGURED and exits 0 --
    # wiring the fifth component turned a dormant hole into a live one.
    dep = subprocess.run(
        [sys.executable, str(deploy)], cwd=str(config.ROOT),
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    tail = ((dep.stdout or "") + (dep.stderr or "")).strip()
    if dep.returncode != 0:
        # NOT an escalation of a pull request. The merge succeeded and the code is
        # on main; marking a merged PR needs-human sends a person to look at
        # something already done while leaving the real problem -- an undeployed
        # main -- unnamed.
        log(f"DEPLOY_FAILED {reason} (exit {dep.returncode})")
        for line in tail.splitlines()[-6:]:
            log(f"  {line[:200]}")
        log("  THE CODE IS MERGED. What failed is the deploy, so main is "
            "ahead of what is running.")
        try:
            config.NEEDS_HUMAN.parent.mkdir(parents=True, exist_ok=True)
            with config.NEEDS_HUMAN.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"- {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  "
                    f"main  (deploy)  the deploy failed {reason} (exit "
                    f"{dep.returncode}). main is ahead of what is running.\n"
                )
        except OSError:
            pass
        log(notify.send(
            "the deploy",
            f"the deploy failed {reason} (exit {dep.returncode}). main is ahead of "
            f"what is running.",
        ))
        return
    marker = "DEPLOYED" if "DEPLOYED" in tail else (
        "DEPLOY_NOOP" if "DEPLOY_NOOP" in tail else "deploy ran")
    if quiet_noop and marker == "DEPLOY_NOOP":
        return
    log(f"DEPLOY_OK {reason}: {marker}")


def main() -> int:
    # =========================================================================
    # 1. THE STOP BUTTON. Checked first, every time, before anything else is read.
    # =========================================================================
    stopped, why = state.stop_requested()
    if stopped:
        # SAY WHICH KIND OF STOPPED, because the remedies are opposites.
        #
        # This always said "Remove it to resume", which is right for a stop FILE and
        # wrong for the first thing a new install hits: no git remote yet, so the
        # remote stop-state read fails and the tick correctly fails closed. A person
        # following that instruction goes looking for a file that is not there and
        # concludes the factory is broken, on their first run, before it has done
        # anything. Failing closed is correct; telling them to delete a non-existent
        # file is not.
        if "could not read" in why:
            log(f"STOPPED: {why}.")
            log("  This is failing CLOSED on purpose: an unreadable stop signal must "
                "count as stopped.")
            log("  On a fresh install the usual cause is no `origin` remote yet, or "
                "`gh` not authenticated.")
            log("  Fix that and the tick resumes on its own; there is no file to delete.")
        else:
            log(f"STOPPED: {why}. Remove it to resume.")
        return 0
    log(f"STOP_CHECK ok ({why})")

    # =========================================================================
    # 1b. THE WATCHDOG. Second, because it can only be outranked by the stop button.
    # =========================================================================
    # A tick is stateless and therefore blind to sequence: it cannot tell its first
    # dispatch of an item from its sixty-eighth. The watchdog reads the ledger, which
    # is the only thing in the system that remembers, and HALTS rather than warning.
    # A warning is what the escalation already was, and the machine drove through it.
    if not DRY_RUN:
        try:
            wd_events = eventlog.read(since_minutes=watchdog.Limits().window_minutes)
            wd_findings = watchdog.assess(wd_events)
            halting = [f for f in wd_findings if f.severity == watchdog.HALT]
            for f in wd_findings:
                log(f"WATCHDOG {f}")
            if halting:
                watchdog.halt(halting)
                log("WATCHDOG_HALTED - .factory/STOP written, nothing dispatched this tick")
                return 0
            # EMPTY IS NOT PASS: report the EVIDENCE examined, not just the verdict.
            # "no findings" and "the ledger was unreadable so nothing was examined"
            # are the same sentence otherwise, and the second one is a watchdog that
            # is quietly switched off.
            log(f"WATCHDOG_OK events={len(wd_events)} findings=0")
        except Exception as e:  # noqa: BLE001
            # A broken watchdog must not take the factory down with it, but it must
            # not pass silently either: a watchdog that is quietly off is the exact
            # condition it exists to make impossible.
            log(f"WATCHDOG_BROKE {type(e).__name__}: {e} - running on, unwatched")

    # =========================================================================
    # 2. RECONCILE ON ENTRY. A sweep, not a dispatch, and it runs on EVERY tick.
    # =========================================================================
    # A stalled item is not work to schedule against other work -- it is a fault to
    # report. Doing it as a case in the priority order below was wrong in a way only
    # running it showed: `next` answers with ONE thing, so a single untriaged issue
    # at a dial below 4 outranks the stall forever. The tick then logs a HOLD and
    # exits 0: nothing dispatched, nothing escalated, and a dead PR invisible behind
    # a queue that could not move either. Two wedges, each hiding the other.
    #
    # So it is reported unconditionally, before the dial and before the capacity
    # check, and it never consumes the tick's dispatch budget.
    # APPLYING A SETTLED RUN IS THE FIRST THING, AND IT IS NOT A DISPATCH. Everything
    # below reads GitHub labels to decide what to do next, and a run that finished
    # since the last tick has not moved a label yet -- so a tick that dispatched first
    # would decide from state one lap out of date, and re-select a target whose work
    # is already done and merely unrecorded.
    if not DRY_RUN:
        import sdlc
        sdlc.reconcile()
    release_settled_locks()
    reap_locks()
    held = {p.stem for p in in_flight()}

    # =========================================================================
    # 2b. CLOSE THE LOOP. Every tick, not only after this process merged.
    # =========================================================================
    # A merge can land from the validate workflow, from a human, or from a tick that
    # died between merge and deploy. deploy.py no-ops when main is already what is
    # running, so this is one fetch per tick and never a second deploy.
    if config.DEPLOY_CMD and not DRY_RUN:
        run_deploy("on the tick's deploy poll", quiet_noop=True)

    # WHAT THIS TICK ESCALATED, so the same tick cannot dispatch it again.
    #
    # Escalating writes a label to GitHub and the queue is read back from GitHub
    # seconds later. GitHub does not promise you read your own write, so the read
    # can still show the pre-escalation state -- and it did: a validation was
    # escalated to needs-human at 19:21:55 and re-dispatched at 19:22:03, eight
    # seconds later, straight back into the state a human was just told to look at.
    #
    # `needs-human` being terminal in the transition table does not help here. The
    # table governs MOVES; this is a stale READ, and no amount of correctness in
    # state.py can fix a queue answered from data that predates the write.
    escalated_here: set[str] = set()

    if not DRY_RUN:
        for pr in state._list("prs", "validating"):
            key = f"validate-{pr['_target']}".replace(":", "-")
            if key in held:
                log(f"IN_FLIGHT {pr['_target']} is 'validating' and a run still holds its lock")
                continue
            escalated_here |= escalate(
                pr["_target"],
                "left in 'validating' with no run holding it; a validation died between "
                "the tripwire and the verdict",
            )
        for issue in state._list("issues", "in-progress"):
            key = f"implement-{issue['_target']}".replace(":", "-")
            if key in held:
                continue
            referenced = False
            for pr in state._list("prs"):
                try:
                    if state.linked_issue(pr["_target"]) == issue["_target"]:
                        referenced = True
                        break
                except Exception as e:  # noqa: BLE001
                    # SAY SO. If this throws for every PR, `referenced` stays False and
                    # an issue that IS answered by a live pull request is escalated as
                    # abandoned. Silently taking the wrong branch of a decision is worse
                    # than the exception that caused it.
                    log(f"  ! could not read the issue link on {pr['_target']}: {e}")
            if not referenced:
                escalated_here |= escalate(
                    issue["_target"],
                    "left in 'in-progress' with no PR record and no run holding it; an "
                    "implement lap died before it opened one",
                )

    # =========================================================================
    # 3. THE AUTONOMY DIAL.
    # =========================================================================
    if config.AUTONOMY < 1:
        action, target, why = state.next_action()
        log("AUTONOMY=0: nothing dispatches. Set FACTORY_AUTONOMY=1 when a lap has been proven by hand.")
        log(f"  would run: {action} {target} ({why})")
        return 0

    # =========================================================================
    # 4. CONCURRENCY.
    # =========================================================================
    running = in_flight()
    if len(running) >= config.MAX_PARALLEL:
        log(f"at capacity ({len(running)}/{config.MAX_PARALLEL}), nothing dispatched")
        log("  held by: " + " ".join(p.name for p in running))
        log("  a lock is freed when its run settles and its result has been applied")
        return 0

    # =========================================================================
    # 5. PRIORITY ORDER. Load-bearing: finish in-flight work before starting new.
    # =========================================================================
    # THE LOOP EXISTS SO MAX_PARALLEL MEANS SOMETHING. `next` names ONE thing, and a
    # target already in flight would otherwise consume the whole tick: ask, get the
    # head of the queue, find its lock taken, stop. A knob that silently does
    # nothing is worse than one that is not offered. Targets that could not be
    # locked are EXCLUDED and the question is asked again, so the priority order
    # still lives entirely in state.py.
    exclude: set[str] = set(escalated_here)
    slots = max(1, config.MAX_PARALLEL - len(running))

    while slots > 0:
        slots -= 1
        action, target, why = state.next_action(exclude)

        if action == "idle":
            log("nothing to do")
            break

        needed = REQUIRES_LEVEL.get(action)
        if needed is not None and config.AUTONOMY < needed:
            log(f"HOLD {action} {target} - requires autonomy >= {needed}, currently {config.AUTONOMY}")
            if action == "merge":
                log("  The PR passed every gate and is waiting for a human. This is level 2 working.")
            break

        if action in ("fix", "validate", "implement", "triage", "merge"):
            log(f"NEXT {action} {target} ({why})")
            dispatch(action, target)
            exclude.add(target)
            continue

        # Everything below is a decision about the whole queue, made once.
        slots = 0

        if action == "escalate":
            exclude |= escalate(target, f"fix-attempt cap reached (FACTORY_RULES 8): {why}")

        elif action in ("stalled-pr", "stalled-issue"):
            # Reported by state.py, acted on here -- only the dispatcher holds the
            # runtime lock and can tell "still running" from "died". The reconcile
            # sweep above has usually handled it; reaching here means it did not.
            log(f"STALLED {target} ({why}) - handled by the reconcile sweep on the next tick")

        else:
            log(f"UNKNOWN action '{action}' from state.py - refusing to guess")
            return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except state.GhError as e:
        # The dispatcher is the scheduled entry point. Nothing supervises it: cron
        # starts it, it exits, and the exit code goes nowhere anyone looks. So a
        # failure here does not lose one workflow, it loses THE WHOLE TICK -- and a
        # factory that has been dead for a week looks exactly like a factory with
        # nothing to do. Make the ending say why, and tell a human.
        log(f"DISPATCHER_FAULT: {e}")
        ledger("dispatcher", f"the dispatcher itself failed: {e}")
        log(notify.send("dispatcher", f"the dispatcher itself failed: {e}"))
        sys.exit(1)
