"""Dispatch the scheduled regression. What the weekly cron entry calls.

Separate from the dispatcher on purpose. The dispatcher answers "what work is
pending?" from the queue; this answers "is what already merged still working?", which
is a different question with a different cadence and a different failure mode. Folding
it into the dispatcher's priority order would mean a busy queue silently starves the
one check that looks at main.

It respects the same two things the dispatcher does: the stop button, and the dial.
Level 4 is where a factory is allowed to file its own bugs -- below that the
regression still RUNS, it just reports rather than filing, because an issue queue that
fills itself before anyone has watched a full cycle is a queue nobody trusts.

WHAT IT DISPATCHES IS `archon-regress`, and the publication half is upstream's. It
files an issue only for a case the trusted check profile handed it, already approved
for export, with a cause it proved -- never from model-written prose. The fixed gate
this factory supplies reports whether main is green and authors no public cases, so a
red regression escalates to a person. Filing turns on when the project's own check
emits `public_cases`. See `factory/fixed_gate.py`.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import state  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def main() -> int:
    # A SCRIPT WHOSE ONLY ACTION IS IRREVERSIBLE MUST NOT DO IT BY ACCIDENT. This took
    # no arguments and ignored the ones it was given, so `regress-trigger.py --help`
    # dispatched a full regression -- a real run against main, on the premium tier,
    # which at level 4 can file issues. Anyone reaching for --help is by definition
    # someone who does not yet know what the script does.
    if len(sys.argv) > 1:
        if {"-h", "--help"} & set(sys.argv[1:]):
            print(__doc__)
            print("Usage: python factory/regress-trigger.py        (takes no arguments)")
            return 0
        print(f"unknown argument: {' '.join(sys.argv[1:])}", file=sys.stderr)
        print("This takes no arguments. Run it bare to dispatch, or --help.",
              file=sys.stderr)
        return 2

    stopped, why = state.stop_requested()
    if stopped:
        log(f"STOPPED: {why}")
        return 0

    if config.AUTONOMY < 1:
        log("AUTONOMY=0: the regression is not dispatched. Run it by hand to watch it.")
        return 0

    if config.AUTONOMY < 4:
        log(
            f"autonomy={config.AUTONOMY}: the regression will RUN and report, but issues "
            f"it finds are not filed automatically until level 4."
        )

    import sdlc
    if not sdlc.launch("regress", ""):
        log("SKIP - a regression run already holds its lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
