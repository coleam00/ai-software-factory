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

    A lock that names a run is freed by evidence about THAT RUN -- release_settled_
    locks() asking the engine -- or by the long stale cap, which is the backstop for
    an engine that can no longer be asked.
    """
    config.LOCKS_RUNTIME.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for lock in config.LOCKS_RUNTIME.glob("*.lock"):
        try:
            age_min = (now - lock.stat().st_mtime) / 60
            first = lock.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue

        if age_min > config.LOCK_STALE_MINUTES:
            log(
                f"LOCK_REAPED {lock.name} - older than {config.LOCK_STALE_MINUTES}m, so "
                f"its run is gone. Held since: {first}"
            )
            lock.unlink(missing_ok=True)
            continue

        if lock_run_id(lock):
            # Owned by a detached run. Only a report about that run frees it early.
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

WORKFLOW_FOR = {
    "triage": config.WORKFLOW_TRIAGE,
    "implement": config.WORKFLOW_IMPLEMENT,
    "fix": config.WORKFLOW_FIX,
    "validate": config.WORKFLOW_VALIDATE,
}

# Which actions need their own git worktree. Triage is advisory -- it reads the
# repo, writes a label and a comment, and never touches the checkout -- so giving it
# a worktree buys nothing and costs a checkout on every untriaged issue. Everything
# that edits or checks out a branch gets isolation.
NEEDS_WORKTREE = {"implement", "fix", "validate"}


def dispatch(action: str, target: str) -> bool:
    """Hand ONE unit of work to Archon, detached, and return.

    The dispatcher never waits: a tick that blocks for twenty minutes is a tick that
    overlaps the next one. Archon owns the run from here; the labels are how we find
    out what happened.
    """
    workflow = WORKFLOW_FOR[action]
    branch = f"factory/{action}-{target.replace('gh:', '').replace(':', '-')}"
    lock = lock_path(action, target)

    if DRY_RUN:
        if lock.exists():
            log(f"DRY-RUN would SKIP {action} {target} - already in flight")
            return False
        where = f"branch {branch}" if action in NEEDS_WORKTREE else "in place (no worktree)"
        log(f"DRY-RUN would dispatch: {workflow} {target} ({where})")
        return True

    if not acquire(lock):
        log(f"SKIP {action} {target} - already in flight")
        return False

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.RUNS_DIR / f"{action}-{target.replace(':', '-')}.log"

    cmd = [config.ARCHON_BIN, "workflow", "run", workflow]
    if action in NEEDS_WORKTREE:
        cmd += ["--branch", branch]
    else:
        cmd += ["--no-worktree"]
    cmd += ["--detach", f"{action} {target}"]
    log(f"DISPATCH {workflow} {target} -> {logfile.name}")
    dispatch_started = time.time()
    try:
        with logfile.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {datetime.now(timezone.utc).isoformat()} {' '.join(cmd)}\n")
            fh.flush()
            p = subprocess.run(
                cmd,
                cwd=str(config.ROOT),
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=300,
                env={**os.environ, "IS_SANDBOX": "1"},
            )
    except (OSError, subprocess.SubprocessError) as e:
        lock.unlink(missing_ok=True)
        escalate(target, f"could not dispatch {workflow}: {e}")
        return False

    # THE RUNNER'S EXIT STATUS IS NOT THE WORK'S VERDICT, and it must still be read.
    # A dispatch that could not even start is a fault in the machinery, and it looks
    # exactly like a factory with nothing to do unless somebody says so.
    if p.returncode != 0:
        lock.unlink(missing_ok=True)
        tail = ""
        try:
            tail = logfile.read_text(encoding="utf-8", errors="replace")[-600:]
        except OSError:
            pass
        escalate(
            target,
            f"{workflow} could not be dispatched (exit {p.returncode}). Last output: {tail[-300:]}",
        )
        return False

    # Detached: Archon owns it now. The lock is NOT released here, because returning
    # from a detached launch does not mean the work finished.
    #
    # Record WHICH run holds it. Everything downstream that decides a lap is dead
    # keys on this id, because it is the only identifier both sides agree on: the
    # dispatching process exits the moment the run detaches, so its PID says nothing,
    # and the engine's run list does not report the branch.
    # ASK THE ENGINE WHICH RUN THIS WAS. The id printed by the CLI is not the run's
    # id (see resolve_run_id), so the log is only the fallback now, not the source.
    run_id = resolve_run_id(action, target, dispatch_started)
    if not run_id:
        try:
            m = RUN_ID_RE.search(logfile.read_text(encoding="utf-8", errors="replace")[-4000:])
            run_id = m.group(1) if m else ""
        except OSError:
            pass
        if run_id:
            log(f"  ! could not resolve the engine's run id; falling back to the "
                f"printed one ({run_id[:8]}), which historically does not resolve")
    if run_id:
        with lock.open("a", encoding="utf-8") as fh:
            fh.write("run " + run_id + "\n")
    else:
        log(f"  ! no run id found in {logfile.name}; this lock can only be freed by age")
    eventlog.record(eventlog.DISPATCH, action=action, target=target,
                    workflow=workflow, run=run_id or None)
    log(f"DISPATCHED {workflow} {target} (detached; lock {lock.name} held until the run settles)")
    return True


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


def resolve_run_id(action: str, target: str, since_epoch: float,
                   attempts: int = 4, payload_override: dict | list | None = None) -> str:
    """The run id the ENGINE gave this dispatch, found by the message we sent it.

    THE PRINTED ID IS NOT THE RUN ID, and everything downstream was keyed on it.
    `archon workflow run --detach` prints "Run id: X" and even says "Track it with:
    archon workflow get X" -- and that id appears nowhere in the engine's run record.
    Measured on four consecutive dispatches: the timestamps matched to the second,
    the workflow names matched, and the overlap between the ids the factory tracked
    and the ids the engine had was ZERO.

    Every symptom that cost hours today traces back here. Lock liveness could never be
    answered, so locks sat until the 180-minute stale cap. No run ever read back as
    `completed`, so the watchdog's progress detectors had nothing to see and its spend
    detectors had no cost to add up. Each of those looked like its own bug and got its
    own patch; they were one bug wearing three hats.

    So the id is resolved by the one key the factory itself controls: `user_message`,
    which dispatch() sets to "<action> <target>" and the engine stores verbatim. The
    start time is required as well, so a re-dispatch of the same target cannot match
    the previous run.

    Returns "" if it cannot be resolved, which the caller treats exactly as it treats
    a missing id today -- a lock freed by age rather than by evidence. Degrading is
    fine; guessing is not.
    """
    want = f"{action} {target}"
    for attempt in range(attempts):
        try:
            if payload_override is not None:
                # A caller supplying a payload is asserting about THAT payload; it must
                # not reach the engine. Same rule as release_settled_locks' probe.
                payload = payload_override
                runs = payload.get("runs", []) if isinstance(payload, dict) else payload
                best, best_ts = "", 0.0
                for r in runs if isinstance(runs, list) else []:
                    if not isinstance(r, dict) or str(r.get("user_message")) != want:
                        continue
                    try:
                        ts = datetime.fromisoformat(
                            str(r.get("started_at") or "").replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                    if ts >= since_epoch - 120 and ts > best_ts:
                        best, best_ts = str(r.get("id") or ""), ts
                return best
            out = subprocess.run(
                [config.ARCHON_BIN, "workflow", "runs", "--json"],
                cwd=str(config.ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            if out.returncode == 0:
                raw = out.stdout or ""
                offsets = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
                if offsets:
                    payload = json.loads(raw[min(offsets):])
                    runs = payload.get("runs", []) if isinstance(payload, dict) else payload
                    best, best_ts = "", 0.0
                    for r in runs if isinstance(runs, list) else []:
                        if not isinstance(r, dict) or str(r.get("user_message")) != want:
                            continue
                        started = str(r.get("started_at") or "")
                        try:
                            ts = datetime.fromisoformat(
                                started.replace("Z", "+00:00")).timestamp()
                        except ValueError:
                            continue
                        # 120s of slack: the row is written a moment after we launched.
                        if ts >= since_epoch - 120 and ts > best_ts:
                            best, best_ts = str(r.get("id") or ""), ts
                    if best:
                        return best
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            pass
        # The row is written asynchronously by the detached child, so a miss on the
        # first look is expected rather than a failure.
        if attempt < attempts - 1:
            time.sleep(3)
    return ""


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

    EMPTY IS NOT AN ANSWER. Every unknown below KEEPS the lock and leaves it to the
    age/PID reaper, which is slow on purpose. A lock held too long stalls one target
    until LOCK_STALE_MINUTES; a lock dropped too early runs two writers over one
    worktree and escalates work that was going fine. Those costs are not symmetric.
    """
    locks = [lk for lk in in_flight() if lock_run_id(lk)]
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
    release_settled_locks()
    reap_locks()
    held = {p.stem for p in in_flight()}

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
        log(f"  a lock with no running workflow is reaped after {config.LOCK_STALE_MINUTES}m")
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

        if action in ("fix", "validate", "implement", "triage"):
            log(f"NEXT {action} {target} ({why})")
            dispatch(action, target)
            exclude.add(target)
            continue

        # Everything below is a decision about the whole queue, made once.
        slots = 0

        if action == "escalate":
            exclude |= escalate(target, f"fix-attempt cap reached (FACTORY_RULES 8): {why}")

        elif action == "merge":
            log(f"MERGE {target}")
            if DRY_RUN:
                continue
            rc = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "merge.py"), target],
                cwd=str(config.ROOT),
            ).returncode
            if rc == 0:
                deploy = Path(__file__).parent / "deploy.py"
                if deploy.exists():
                    # THE RESULT IS CHECKED. This used to discard it, so a deploy that
                    # failed after a successful merge was completely silent: the code
                    # landed, the deploy broke, and the lap reported clean. It never
                    # mattered while FACTORY_DEPLOY_CMD was unset, because deploy.py
                    # then prints DEPLOY_NOT_CONFIGURED and exits 0 -- wiring the fifth
                    # component turned a dormant hole into a live one.
                    dep = subprocess.run(
                        [sys.executable, str(deploy)], cwd=str(config.ROOT),
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=1800,
                    )
                    tail = ((dep.stdout or "") + (dep.stderr or "")).strip()
                    if dep.returncode != 0:
                        # NOT an escalation of the pull request. The merge succeeded and
                        # the code is on main; marking a merged PR needs-human sends a
                        # person to look at something already done while leaving the real
                        # problem -- an undeployed main -- unnamed.
                        log(f"DEPLOY_FAILED after merging {target} (exit {dep.returncode})")
                        for line in tail.splitlines()[-6:]:
                            log(f"  {line[:200]}")
                        log("  THE CODE IS MERGED. What failed is the deploy, so main is "
                            "ahead of what is running.")
                        try:
                            config.NEEDS_HUMAN.parent.mkdir(parents=True, exist_ok=True)
                            with config.NEEDS_HUMAN.open("a", encoding="utf-8") as fh:
                                fh.write(
                                    f"- {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  "
                                    f"{target}  (deploy)  merged, but the deploy failed "
                                    f"(exit {dep.returncode}). main is ahead of what is "
                                    f"running.\n"
                                )
                        except OSError:
                            pass
                        log(notify.send(
                            "the deploy",
                            f"{target} merged but the deploy failed (exit "
                            f"{dep.returncode}). main is ahead of what is running.",
                        ))
                    else:
                        marker = "DEPLOYED" if "DEPLOYED" in tail else (
                            "DEPLOY_NOOP" if "DEPLOY_NOOP" in tail else "deploy ran")
                        log(f"DEPLOY_OK after merging {target}: {marker}")
            elif rc == 2:
                # ALREADY HANDLED. The branch went stale while it was in flight --
                # somebody pushed to main, which on any repo with velocity is Tuesday
                # -- and merge.py requeued it for revalidation, which is the designed
                # remedy. Escalating on top would send it straight to needs-human,
                # which is TERMINAL for nodes: a recovery that undid itself, and a
                # person woken for a situation the factory had already resolved.
                log(f"REQUEUED {target} - the branch was behind base; it will be rebased and re-judged")
            else:
                # Everything else: merge.py printed the reason and could not recover.
                exclude |= escalate(
                    target, "merge refused for a PR that passed every gate; see the log above"
                )

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
