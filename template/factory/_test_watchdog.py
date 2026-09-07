"""Proof that the watchdog can fire, and that it stays quiet when it should.

    python factory/_test_watchdog.py            the detector proofs
    python factory/_test_watchdog.py --replay   re-run the real 2026-09-01 incident

TWO OBLIGATIONS, and the second is the one that is usually skipped.

 1. EVERY DETECTOR MUST FIRE on a history that deserves it. A detector nobody has
    watched go red is a detector nobody should trust. On this project a check once went
    VACUOUS instead of red -- it derived its own test cases from the table it was
    auditing, so with the bug present it simply generated nothing and reported 118
    passes rather than 119 failures. Passing is not evidence.

 2. EVERY DETECTOR MUST STAY SILENT on a healthy history. A detector that always fires
    is not a safety net, it is an off switch with extra steps, and the first false halt
    teaches everyone to disable the watchdog. `healthy` below is deliberately BUSY --
    many dispatches, several targets, a couple of failures -- because a detector that
    only stays quiet on an idle factory has not been tested at all.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import ledger  # noqa: E402
import watchdog  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

NOW = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)
FAILED = 0
CHECKS = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global FAILED, CHECKS
    CHECKS += 1
    if not ok:
        FAILED += 1
        print(f"  FAIL  {what}" + (f" -- {detail}" if detail else ""))


def ev(kind: str, minutes_ago: float, **f: object) -> dict:
    e = {"t": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "kind": kind}
    e.update(f)
    return e


def fired(events: list[dict], detector: str, limits: watchdog.Limits | None = None) -> bool:
    return any(f.detector == detector and f.severity == watchdog.HALT
               for f in watchdog.assess(events, NOW, limits))


def healthy() -> list[dict]:
    """A busy, correct hour. Work dispatched, work completing, one honest failure.

    This is the control. If any detector fires here the thresholds are wrong, and a
    watchdog that cries wolf on a good night will be switched off before it ever
    catches a bad one.
    """
    out: list[dict] = []
    for i, (action, target) in enumerate([
        ("implement", "gh:issue:1"), ("validate", "gh:pr:20"), ("merge", "gh:pr:20"),
        ("triage", "gh:issue:9"), ("implement", "gh:issue:9"), ("validate", "gh:pr:21"),
    ]):
        run = f"run-{i}"
        out.append(ev(ledger.DISPATCH, 100 - i * 12, action=action, target=target, run=run))
        out.append(ev(ledger.SETTLE, 96 - i * 12, run=run, status="completed", cost_usd=0.9))
    # one genuine failure followed by a successful fix, which is a factory working
    out.append(ev(ledger.DISPATCH, 25, action="fix", target="gh:pr:21", run="run-f"))
    out.append(ev(ledger.SETTLE, 22, run="run-f", status="failed", cost_usd=0.4))
    out.append(ev(ledger.DISPATCH, 18, action="fix", target="gh:pr:21", run="run-g"))
    out.append(ev(ledger.SETTLE, 14, run="run-g", status="completed", cost_usd=0.5))
    return out


def detector_proofs() -> None:
    base = healthy()

    # --- the control -----------------------------------------------------------
    findings = watchdog.assess(base, NOW)
    check("a busy, healthy hour produces NO findings", not findings,
          "; ".join(str(f) for f in findings))

    # --- D1 repeat-dispatch, the incident's shape ------------------------------
    repeat = base + [
        ev(ledger.DISPATCH, 30 - i * 3, action="validate", target="gh:pr:14", run=f"r{i}")
        for i in range(3)
    ] + [
        ev(ledger.SETTLE, 29 - i * 3, run=f"r{i}", status="failed", cost_usd=0.25)
        for i in range(3)
    ]
    check("D1 fires on 3 identical dispatches with no completion",
          fired(repeat, "repeat-dispatch"))
    check("D1 does NOT fire on 2", not fired(repeat[:len(base) + 2] + repeat[len(base) + 3:-1],
                                             "repeat-dispatch"))

    # The same pair repeating even while COMPLETING is still a loop, just a slower
    # one. Separate threshold, so it needs its own proof rather than inheriting D1's.
    spin = base + [
        ev(ledger.DISPATCH, 60 - i * 5, action="validate", target="gh:pr:30", run=f"s{i}")
        for i in range(6)
    ] + [
        ev(ledger.SETTLE, 59 - i * 5, run=f"s{i}", status="completed", cost_usd=0.1)
        for i in range(6)
    ]
    check("D1 fires on 6 repeats even when every run completes",
          fired(spin, "repeat-dispatch"))

    # --- D2 escalation-ignored, the root cause as a behaviour -------------------
    ignored = base + [
        ev(ledger.ESCALATE, 40, target="gh:pr:14", reason="size cap"),
        ev(ledger.DISPATCH, 20, action="validate", target="gh:pr:14", run="x1"),
    ]
    check("D2 fires when an escalated target is dispatched again",
          fired(ignored, "escalation-ignored"))
    # ORDER IS THE WHOLE CLAIM. A dispatch BEFORE the escalation is the normal path:
    # you must dispatch something to discover it needs a human.
    ordered = base + [
        ev(ledger.DISPATCH, 40, action="validate", target="gh:pr:14", run="x2"),
        ev(ledger.ESCALATE, 20, target="gh:pr:14", reason="size cap"),
    ]
    check("D2 does NOT fire when the dispatch PRECEDES the escalation",
          not fired(ordered, "escalation-ignored"),
          "escalating after a run is the normal path and must not halt")
    # A HALT IS A BOUNDARY. Once the watchdog has halted on these two events and a
    # human has cleared STOP, re-reading them must not halt again -- on a real VPS the
    # factory was cleared at 16:37 and halted at 16:38 on an hour-old pair, forever.
    acknowledged = ignored + [ev(ledger.HALT, 10, reason="escalation-ignored")]
    check("D2 does NOT re-fire on events before the most recent halt",
          not fired(acknowledged, "escalation-ignored"),
          "a cleared halt came straight back on evidence a human already acted on; "
          "the stop button could never be un-pressed")
    # ...but the boundary is not amnesty. The same shape AFTER the halt fires again.
    repeated = acknowledged + [
        ev(ledger.ESCALATE, 8, target="gh:pr:14", reason="size cap"),
        ev(ledger.DISPATCH, 4, action="validate", target="gh:pr:14", run="x3"),
    ]
    check("D2 fires again on an escalate-then-dispatch AFTER the halt",
          fired(repeated, "escalation-ignored"))

    # --- D3 all-failing --------------------------------------------------------
    failing = [ev(ledger.SETTLE, 50 - i * 5, run=f"f{i}", status="failed", cost_usd=0.2)
               for i in range(5)]
    check("D3 fires on 5 settled runs with no completion", fired(failing, "all-failing"))
    check("D3 does NOT fire when one of them completed",
          not fired(failing[:-1] + [ev(ledger.SETTLE, 5, run="f9", status="completed")],
                    "all-failing"))

    # --- D4 no-progress, a loop SPREAD ACROSS targets --------------------------
    # D1 cannot see this one: no single pair repeats enough to trip it.
    # The settles are REAL failures, not an empty history. Eight dispatches with no
    # settles at all is not a factory going nowhere, it is a factory nobody has heard
    # back from yet -- and with a capacity of one it cannot even happen. The first
    # version of this proof used that shape, so it was asserting on a history the
    # system cannot produce.
    spread = [ev(ledger.DISPATCH, 60 - i * 5, action="implement",
                 target=f"gh:issue:{i}", run=f"n{i}") for i in range(8)] +              [ev(ledger.SETTLE, 59 - i * 5, run=f"n{i}", status="failed", cost_usd=0.2)
              for i in range(8)]
    check("D4 fires on 8 dispatches with nothing completing", fired(spread, "no-progress"))
    check("D4 is what catches a loop spread across targets, which D1 misses",
          not fired(spread, "repeat-dispatch"),
          "if D1 also fired here this proof would be measuring the wrong detector")

    # --- D5 spend --------------------------------------------------------------
    burn = [ev(ledger.DISPATCH, 60, action="validate", target="gh:pr:1", run="b0"),
            ev(ledger.SETTLE, 55, run="b0", status="completed", cost_usd=26.0)]
    check("D5 spend-cap fires above the cap even when work completes",
          fired(burn, "spend-cap"))
    quiet_burn = [ev(ledger.SETTLE, 55 - i, run=f"q{i}", status="failed", cost_usd=1.0)
                  for i in range(9)]
    check("D5 spend-without-progress fires on money buying nothing",
          fired(quiet_burn, "spend-without-progress"))

    # --- the false halt of 2026-09-01, kept as a regression --------------------
    # The watchdog halted a HEALTHY factory within an hour of going live: five settles
    # in a row reported `not_found`, D3 read that as "nothing is getting through", and
    # three pull requests had merged during exactly that window. `not_found` means the
    # run aged out of the engine's 20-run window, which is the right answer for
    # releasing a lock and is not an outcome. Absence of evidence read as evidence of
    # failure, which makes the safety system the outage.
    aged_out = [ev(ledger.SETTLE, 50 - i * 5, run=f"a{i}", status="not_found")
                for i in range(6)]
    check("aged-out runs do NOT trip all-failing", not fired(aged_out, "all-failing"),
          "not_found says the engine has no record, not that the work failed")
    check("aged-out runs do NOT trip no-progress",
          not fired(aged_out + [ev(ledger.DISPATCH, 60 - i * 4, action="validate",
                                   target=f"gh:pr:{i}", run=f"a{i}") for i in range(8)],
                    "no-progress"))
    repeat_blind = [ev(ledger.DISPATCH, 30 - i * 3, action="validate",
                       target="gh:pr:9", run=f"b{i}") for i in range(3)] +                    [ev(ledger.SETTLE, 29 - i * 3, run=f"b{i}", status="not_found")
                    for i in range(3)]
    check("aged-out runs do NOT trip repeat-dispatch",
          not fired(repeat_blind, "repeat-dispatch"),
          "three dispatches nobody can report on is not three failures")
    # and the half that must still work: REAL failures still halt
    real_fail = [ev(ledger.SETTLE, 50 - i * 5, run=f"c{i}", status="failed")
                 for i in range(5)]
    check("real failures still trip all-failing", fired(real_fail, "all-failing"),
          "the fix for the false positive must not disarm the detector")

    # --- D6 spend-blind, the watchdog auditing its own evidence ----------------
    blind = [ev(ledger.SETTLE, 50 - i * 5, run=f"c{i}", status="completed") for i in range(6)]
    fs = watchdog.assess(blind, NOW)
    check("D6 warns when most settled runs carry no cost",
          any(f.detector == "spend-blind" and f.severity == watchdog.WARN for f in fs))
    check("D6 is a WARN, never a HALT",
          not any(f.detector == "spend-blind" and f.severity == watchdog.HALT for f in fs),
          "a failed price lookup is a blind spot, not a runaway; halting on it is an outage")
    check("D6 stays quiet when costs are present",
          not any(f.detector == "spend-blind" for f in watchdog.assess(healthy(), NOW)))

    # --- the window ------------------------------------------------------------
    # A detector that ignores the window would fire forever on ancient history and
    # the factory could never be restarted after one bad night.
    old = [ev(ledger.DISPATCH, 60 * 24, action="validate", target="gh:pr:14", run=f"o{i}")
           for i in range(9)]
    check("history older than the window is not evidence", not watchdog.assess(old, NOW),
          "yesterday's runaway must not halt today's factory")


def replay_incident() -> None:
    """Re-run the REAL 2026-09-01 history through the watchdog, one dispatch at a time.

    A synthetic proof shows the detector CAN fire. This shows it would have fired on
    the thing that actually happened, and how much of the $17.18 it would have saved.
    """
    log = config.SHARED / ".factory/runs/validate-gh-pr-14.log"
    if not log.exists():
        print("  (no incident log on this machine, skipping replay)")
        return
    txt = log.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"\n=== (\S+) archon workflow run", txt)

    events: list[dict] = []
    halted_at = 0
    dispatches = 0
    for i in range(1, len(parts), 2):
        stamp, body = parts[i], parts[i + 1]
        if stamp < "2026-09-01T14:12":
            continue
        m = re.search(r"Run id: ([0-9a-f-]+)", body)
        if not m:
            continue
        dispatches += 1
        run = m.group(1)
        events.append({"t": stamp, "kind": ledger.DISPATCH, "action": "validate",
                       "target": "gh:pr:14", "run": run})
        events.append({"t": stamp, "kind": ledger.SETTLE, "run": run,
                       "status": "failed", "cost_usd": 0.253})
        now = datetime.fromisoformat(stamp)
        if not halted_at and any(f.severity == watchdog.HALT
                                 for f in watchdog.assess(events, now)):
            halted_at = dispatches

    check("the watchdog halts the real incident", halted_at > 0)
    if halted_at:
        saved = (dispatches - halted_at) * 0.253
        print(f"  replay: {dispatches} real dispatches, watchdog halts at #{halted_at}")
        print(f"  replay: ${saved:,.2f} of ${dispatches * 0.253:,.2f} would not have been spent")


def main(argv: list[str]) -> int:
    detector_proofs()
    if "--replay" in argv or True:
        replay_incident()
    print("")
    if FAILED:
        print(f"WATCHDOG_TESTS_FAILED checks={CHECKS} failed={FAILED}")
        return 1
    print(f"WATCHDOG_TESTS_PASSED checks={CHECKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
