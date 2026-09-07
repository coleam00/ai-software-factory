"""Does this factory look like it is making progress, or like it is stuck in a loop?

    python factory/watchdog.py              assess, halt if a detector trips
    python factory/watchdog.py --report     assess and print, change nothing
    python factory/watchdog.py --explain    the detectors and their thresholds

RUN AT THE TOP OF EVERY TICK, immediately after the stop button, before the queue is
read. A watchdog that only runs when somebody remembers to run it is a postmortem.

WHY IT EXISTS. On 2026-09-01 the dispatcher re-validated one rejected pull request 68
times in three and a half hours ($17.18) and never reached the rest of the queue. Every
individual tick was correct. `escalate()` wrote `factory:needs-human`, the next tick read
the PR back as `open` because of a table bug, and it went straight to the front of the
queue again. The pathology lived entirely in the SEQUENCE, and nothing in the system
looked at sequences.

The specific table bug is fixed. This file exists because the NEXT bug of that shape
will not be that bug, and the failure mode it produced -- burn money, make no progress,
look busy -- is the one an unattended factory must never be able to sustain.

THE DESIGN CONSTRAINT THAT MATTERS: `assess()` IS A PURE FUNCTION of a list of events
and a clock. It reads nothing, writes nothing, and asks no service anything. That is
what makes every detector below provable: a test hands it a synthetic history and
asserts the detector fires. A watchdog nobody has watched fail is a watchdog nobody
should trust, and the first version of a check on this project went VACUOUS instead of
red, which is worse than having no check at all.

HALTING IS THE POINT. A finding at HALT severity writes `.factory/STOP` and notifies.
It does not warn and hope. Escalating without stopping is what produced the incident:
the label was written correctly every time and the machine drove straight through it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


HALT = "HALT"
WARN = "WARN"


@dataclass
class Limits:
    """Every threshold, in one object, so a test can tighten them without env vars.

    The defaults are deliberately GENEROUS. A false halt costs a night of throughput
    and is visible in the morning; a missed runaway costs money continuously and looks
    like a working factory. Those are both real costs, so the thresholds sit where
    only a genuinely pathological sequence reaches them: three identical dispatches
    that never once completed is not a busy factory, it is a loop.
    """

    window_minutes: int = field(default_factory=lambda: _int("FACTORY_WATCH_WINDOW", 120))
    repeat_failing: int = field(default_factory=lambda: _int("FACTORY_WATCH_REPEAT_FAILING", 3))
    repeat_any: int = field(default_factory=lambda: _int("FACTORY_WATCH_REPEAT_ANY", 6))
    consecutive_failures: int = field(default_factory=lambda: _int("FACTORY_WATCH_FAILS", 5))
    dispatches_without_progress: int = field(
        default_factory=lambda: _int("FACTORY_WATCH_NOPROG", 8))
    spend_cap_usd: float = field(default_factory=lambda: _float("FACTORY_WATCH_SPEND", 25.0))
    spend_without_progress_usd: float = field(
        default_factory=lambda: _float("FACTORY_WATCH_SPEND_NOPROG", 8.0))


@dataclass
class Finding:
    detector: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.detector}: {self.message}"


# A run that ENDED WELL. `completed` is the only Archon status that means the workflow
# reached its end; everything else in SETTLED_STATUSES is a run that stopped early.
# Progress is defined as a completed run, never as "a run finished", because a run
# that failed also finished and that conflation is precisely the loop being detected.
PROGRESS_STATUSES = {"completed"}

# STATUSES THAT ARE NOT EVIDENCE OF ANYTHING, and getting this wrong halted a
# perfectly healthy factory within an hour of the watchdog going live.
#
# `not_found` is recorded when the engine has no record of a run. That is the right
# answer for RELEASING A LOCK -- nothing will ever hold it again -- and it is not an
# outcome. Archon reports a window of 20 runs and prunes beyond it, so a busy hour
# ages runs out routinely, and the settle written when the lock is freed says
# `not_found` for work that in fact succeeded.
#
# D3 counted those as failures and halted with "the last 5 settled runs all ended
# ['not_found'] with no completion. Nothing is getting through." Three pull requests
# had merged during exactly that window. Absence of evidence was being read as
# evidence of failure, which is the same mistake as "empty is not pass" wearing the
# opposite hat: here the safe-looking default was to ASSUME the worst, and assuming
# the worst on no evidence makes the safety system the outage.
UNKNOWN_STATUSES = {"not_found"}


def _in_window(events: list[dict], now: datetime, minutes: int) -> list[dict]:
    cutoff = now - timedelta(minutes=minutes)
    keep = []
    for e in events:
        ts = ledger.parse_t(e)
        # An event with no readable timestamp is KEPT. See ledger.read: narrowing the
        # evidence on the basis of a field we could not parse is how a detector goes
        # quiet without anybody deciding that it should.
        if ts is None or ts >= cutoff:
            keep.append(e)
    return keep


def _after_last_halt(events: list[dict]) -> list[dict]:
    """A HALT IS A BOUNDARY. Everything before it has been judged, written to STOP,
    and handed to a human; deleting STOP is that human saying they read it.

    Without this the factory could never be resumed. The first version re-read the
    whole window on the tick after STOP was removed, found the same escalation and
    the same dispatch, and halted again on evidence a person had already acted on --
    forever, until the ledger itself was edited. On a real VPS: cleared at 16:37,
    halted again at 16:38, on two events from an hour earlier. A stop button that
    cannot be un-pressed is a different failure from a runaway, but it is still the
    safety system being the outage.

    The cost is honest and worth saying: a spend cap is now "since the last halt" rather
    than "in the last window". A human who resumes after a spend halt has chosen to
    spend again, which is what resuming means.
    """
    last = -1
    for i, e in enumerate(events):
        if e.get("kind") == ledger.HALT:
            last = i
    return events[last + 1:] if last >= 0 else events


def assess(events: list[dict], now: datetime | None = None,
           limits: Limits | None = None) -> list[Finding]:
    """PURE. Given a history, what is wrong with it? No I/O, no clock of its own."""
    now = now or datetime.now(timezone.utc)
    lim = limits or Limits()
    win = _after_last_halt(_in_window(events, now, lim.window_minutes))
    findings: list[Finding] = []

    dispatches = [e for e in win if e.get("kind") == ledger.DISPATCH]
    settles = [e for e in win if e.get("kind") == ledger.SETTLE]
    escalations = [e for e in win if e.get("kind") == ledger.ESCALATE]

    status_by_run = {str(e.get("run")): str(e.get("status", "")).lower()
                     for e in settles if e.get("run")}
    completed_runs = {r for r, s in status_by_run.items() if s in PROGRESS_STATUSES}
    unknown_runs = {r for r, s in status_by_run.items() if s in UNKNOWN_STATUSES}
    # Settles that actually say something about how a run ended.
    known_settles = [e for e in settles
                     if str(e.get("status", "")).lower() not in UNKNOWN_STATUSES]

    # -- D1 ------------------------------------------------------------------------
    # THE SAME WORK, OVER AND OVER, NEVER ONCE FINISHING. This is the incident's exact
    # shape and the cheapest thing in the file to check.
    by_pair: dict[str, list[dict]] = {}
    for d in dispatches:
        by_pair.setdefault(f"{d.get('action')} {d.get('target')}", []).append(d)
    for pair, ds in sorted(by_pair.items()):
        good = sum(1 for d in ds if str(d.get("run")) in completed_runs)
        # A dispatch whose run we cannot ask about is not a failed dispatch. Requiring
        # that none of them are unknown keeps this pointed at runs that demonstrably
        # did not finish, which is what the original incident looked like.
        blind = sum(1 for d in ds if str(d.get("run")) in unknown_runs)
        if len(ds) >= lim.repeat_failing and good == 0 and blind == 0:
            findings.append(Finding(
                "repeat-dispatch", HALT,
                f"'{pair}' dispatched {len(ds)}x in {lim.window_minutes}m and not one run "
                f"completed. The queue is not advancing past this item."))
        elif len(ds) >= lim.repeat_any:
            findings.append(Finding(
                "repeat-dispatch", HALT,
                f"'{pair}' dispatched {len(ds)}x in {lim.window_minutes}m ({good} completed). "
                f"Even when runs finish, this much repetition on one target is a loop."))

    # -- D2 ------------------------------------------------------------------------
    # A TARGET WAS HANDED TO A HUMAN AND THE MACHINE TOOK IT BACK. needs-human is
    # terminal by construction (TRANSITIONS["needs-human"] is the empty set), so a
    # dispatch after an escalation means something is reading that state wrongly. This
    # is the incident's root cause expressed as a behaviour rather than as a table, so
    # it survives any future bug that produces the same effect by another route.
    escalated_at: dict[str, datetime] = {}
    for e in escalations:
        tgt, ts = str(e.get("target") or ""), ledger.parse_t(e)
        if tgt and ts and (tgt not in escalated_at or ts < escalated_at[tgt]):
            escalated_at[tgt] = ts
    for d in dispatches:
        tgt, ts = str(d.get("target") or ""), ledger.parse_t(d)
        first = escalated_at.get(tgt)
        if first and ts and ts > first:
            findings.append(Finding(
                "escalation-ignored", HALT,
                f"{tgt} was escalated to a human at {first.isoformat()} and then dispatched "
                f"again at {ts.isoformat()}. needs-human is terminal; something is reading "
                f"it as workable."))
            break

    # -- D3 ------------------------------------------------------------------------
    # A RUN OF PURE FAILURE. Not about repetition: five different things all failing
    # says the environment is broken, not that one item is cursed.
    tail = [str(e.get("status", "")).lower()
            for e in known_settles][-lim.consecutive_failures:]
    if len(tail) >= lim.consecutive_failures and not any(s in PROGRESS_STATUSES for s in tail):
        findings.append(Finding(
            "all-failing", HALT,
            f"the last {len(tail)} settled runs all ended {sorted(set(tail))} with no "
            f"completion. Nothing is getting through."))

    # -- D4 ------------------------------------------------------------------------
    # BUSY AND GOING NOWHERE. Catches a loop SPREAD ACROSS TARGETS, which D1 cannot
    # see because no single pair repeats enough.
    if (len(dispatches) >= lim.dispatches_without_progress and not completed_runs
            and known_settles):
        findings.append(Finding(
            "no-progress", HALT,
            f"{len(dispatches)} dispatches in {lim.window_minutes}m and not one run "
            f"completed."))

    # -- D5 ------------------------------------------------------------------------
    # MONEY. The one measure a person actually feels, and the reason this file was
    # asked for. Two questions: is the burn rate absurd, and is it buying anything.
    spend = sum(float(e.get("cost_usd") or 0) for e in win)
    if spend >= lim.spend_cap_usd:
        findings.append(Finding(
            "spend-cap", HALT,
            f"${spend:,.2f} spent in {lim.window_minutes}m, cap is "
            f"${lim.spend_cap_usd:,.2f}."))
    elif spend >= lim.spend_without_progress_usd and not completed_runs and known_settles:
        findings.append(Finding(
            "spend-without-progress", HALT,
            f"${spend:,.2f} spent in {lim.window_minutes}m with no run completing. "
            f"The incident this file exists for cost $17.18 exactly this way."))

    # -- D6 ------------------------------------------------------------------------
    # CAN THE SPEND DETECTORS SEE ANYTHING AT ALL? `run_cost` returns None when a run
    # has aged out of the window Archon reports, and a None is recorded rather than a
    # zero precisely so it cannot be mistaken for "this was free". But the consequence
    # is that D5 quietly weakens as coverage drops, and a spend cap computed from a
    # third of the spend is a spend cap that will not fire.
    #
    # This is the same failure this whole file exists to prevent, one level in: a check
    # that goes quiet instead of red. So the gap is REPORTED rather than tolerated. It
    # is a WARN, not a HALT: missing cost data is a blind spot, not a runaway, and
    # halting the factory because a price lookup failed would be its own outage.
    # THE DENOMINATOR IS KNOWN SETTLES, not every settle. A `not_found` run is one the
    # engine has no record of, so it has no cost to report -- counting it as "unpriced"
    # made this warning permanent, and a warning that never clears is how a monitor
    # teaches people to ignore it. The same reasoning as UNKNOWN_STATUSES above: no
    # record is not a measurement, in either direction.
    priced = sum(1 for e in known_settles if e.get("cost_usd") is not None)
    if known_settles and priced * 2 < len(known_settles):
        findings.append(Finding(
            "spend-blind", WARN,
            f"only {priced} of {len(known_settles)} settled runs carry a cost, so the ${spend:,.2f} "
            f"the spend detectors are judging is a floor, not the total."))

    return findings


# --- the acting half ----------------------------------------------------------

def halt(findings: list[Finding]) -> None:
    """Stop the factory, loudly. Writes the stop file the dispatcher reads FIRST.

    Uses the same STOP file a human uses, deliberately: one brake, one place to look,
    and clearing it is the same gesture either way.
    """
    reason = "; ".join(f.message for f in findings if f.severity == HALT)
    body = (
        f"WATCHDOG HALT {datetime.now(timezone.utc).isoformat()}\n"
        f"{reason}\n\n"
        f"Nothing is broken by leaving this in place. Read `factory/ledger.py stats`, fix\n"
        f"the cause, then delete this file to resume.\n"
    )
    try:
        config.STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.STOP_FILE.write_text(body, encoding="utf-8")
    except OSError as e:
        print(f"WATCHDOG_HALT_FAILED could not write {config.STOP_FILE}: {e}")
    ledger.record(ledger.HALT, reason=reason)
    try:
        import notify
        print(notify.send("the factory itself (watchdog halt)", reason))
    except Exception as e:  # noqa: BLE001
        print(f"WATCHDOG_NOTIFY_FAILED {e}")


def main(argv: list[str]) -> int:
    if "--explain" in argv:
        lim = Limits()
        print("The watchdog halts the factory when the SEQUENCE of its actions is")
        print("pathological, whatever each individual action looked like.\n")
        for name, why in [
            ("repeat-dispatch", f"one action+target {lim.repeat_failing}x with no completion, "
                                f"or {lim.repeat_any}x regardless"),
            ("escalation-ignored", "a target dispatched after being escalated to a human"),
            ("all-failing", f"{lim.consecutive_failures} settled runs, none completed"),
            ("no-progress", f"{lim.dispatches_without_progress} dispatches, none completed"),
            ("spend-cap", f"${lim.spend_cap_usd:,.2f} in {lim.window_minutes}m"),
            ("spend-without-progress", f"${lim.spend_without_progress_usd:,.2f} buying nothing"),
            ("spend-blind", "WARN only: most settled runs carry no cost, so D5 is half-blind"),
        ]:
            print(f"  {name:24s} {why}")
        print(f"\nwindow: {lim.window_minutes}m")
        return 0

    events = ledger.read(since_minutes=Limits().window_minutes)
    findings = assess(events)

    # EMPTY IS NOT PASS. Say how many detectors ran and how much history they saw, so
    # "no findings" can be told apart from "the ledger was unreadable and nothing was
    # examined". Those look identical in a log that only prints problems.
    print(f"WATCHDOG_CHECKED events={len(events)} detectors=7 findings={len(findings)}")
    for f in findings:
        print(f"  {f}")

    halting = [f for f in findings if f.severity == HALT]
    if halting and "--report" not in argv:
        halt(halting)
        print("WATCHDOG_HALTED - .factory/STOP written; the next tick will refuse to run")
        return 1
    if halting:
        print("WATCHDOG_WOULD_HALT (--report, so nothing was written)")
        return 1
    print("WATCHDOG_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
