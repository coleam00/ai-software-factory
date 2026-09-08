"""The factory's memory of what it just did.

    python factory/ledger.py tail 20        the last 20 events
    python factory/ledger.py stats 180      what happened in the last 180 minutes

WHY THIS EXISTS. Every dispatcher tick is stateless by design: read GitHub, pick one
thing, dispatch, exit. That is a good design -- it means a tick cannot be poisoned by
what a previous tick believed -- but it has one consequence nobody had priced in.

**A process with no memory of its own actions cannot notice that it is repeating
itself.** The per-target lock prevents two dispatches at the SAME TIME. Nothing
prevented the same dispatch happening 68 times IN SEQUENCE, which is exactly what
happened on 2026-09-01: one rejected pull request re-validated every tick for three
and a half hours, $17.18, while the rest of the queue was never reached.

Each tick was individually correct. The pathology existed only in the sequence, and
the sequence was the one thing nothing was written down in.

So: an append-only line per event. This file is the substrate the watchdog reads. It
is deliberately dumb -- no analysis here, no thresholds, no opinions. It records.

APPEND-ONLY, AND TOLERANT ON READ. A corrupt or half-written line (the machine died
mid-write) is skipped rather than fatal. A watchdog that crashes on its own input is
a watchdog that is off exactly when things are going wrong.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

LEDGER = Path(os.environ.get("FACTORY_LEDGER") or (config.SHARED / ".factory/ledger.jsonl"))


def _path() -> Path:
    """Resolved PER CALL, not once at import.

    `_selftest.py` drives `release_settled_locks()` with a synthetic Archon payload to
    prove the lock logic, and that function now records a settle. Bound at import, the
    path is the production ledger, so every `doctor` run wrote FABRICATED settles --
    run id 11111111-2222-3333-4444-555555555555, alternating completed/failed -- into
    the evidence the watchdog reads. Fake history is worse than no history: it can halt
    a healthy factory, and it did not look like a bug because the entries were
    well-formed.

    So the test redirects `ledger.LEDGER` and every write follows it.
    """
    return LEDGER

# The kinds. Named here so a typo in a caller is a KeyError rather than an event
# that silently never matches a detector.
DISPATCH = "dispatch"
SETTLE = "settle"
ESCALATE = "escalate"
HALT = "halt"
# A target that had been handed to a human came back. The factory never removes the
# needs-human label, so its absence after an escalation is a person's signature;
# the dispatcher records what it saw before it takes the target again.
RESUME = "resume"
KINDS = {DISPATCH, SETTLE, ESCALATE, HALT, RESUME}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(kind: str, **fields: object) -> None:
    """Append one event. Never raises: a factory must not die because it could not
    write its own diary, and the diary going quiet is itself detectable (the
    watchdog's `loop-dead` finding, and the Monitor watching the loop's output)."""
    if kind not in KINDS:
        raise ValueError(f"unknown ledger kind {kind!r}; expected one of {sorted(KINDS)}")
    entry = {"t": now_iso(), "kind": kind}
    entry.update({k: v for k, v in fields.items() if v is not None})
    try:
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(since_minutes: int | None = None, path: Path | None = None) -> list[dict]:
    """Every event, oldest first. `since_minutes` trims by timestamp.

    An unparseable line is DROPPED, not fatal -- see the module docstring. An entry
    with an unreadable timestamp is KEPT when a window is requested, because the
    conservative reading of "I cannot tell when this happened" is to let the
    detectors see it rather than to quietly narrow their evidence.
    """
    p = path or _path()
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    cutoff = None
    if since_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    out: list[dict] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(e, dict) or "kind" not in e:
            continue
        if cutoff is not None:
            ts = parse_t(e)
            if ts is not None and ts < cutoff:
                continue
        out.append(e)
    return out


def parse_t(e: dict) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(e.get("t", "")))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tail"
    if cmd == "tail":
        n = int(argv[2]) if len(argv) > 2 else 20
        for e in read()[-n:]:
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if cmd == "stats":
        mins = int(argv[2]) if len(argv) > 2 else 180
        events = read(since_minutes=mins)
        by_kind: dict[str, int] = {}
        by_target: dict[str, int] = {}
        cost = 0.0
        for e in events:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
            if e["kind"] == DISPATCH:
                key = f"{e.get('action')} {e.get('target')}"
                by_target[key] = by_target.get(key, 0) + 1
            cost += float(e.get("cost_usd") or 0)
        print(f"window          {mins}m, {len(events)} events")
        print(f"by kind         {by_kind}")
        print(f"spend           ${cost:,.2f}")
        for k, v in sorted(by_target.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {v:3d}x  {k}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
