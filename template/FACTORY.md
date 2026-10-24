# Factory operations

This application runs on a pinned Archon SDLC source. See
[factory/MIGRATION.md](factory/MIGRATION.md) for installing and upgrading it.

Shared workflow gates own every decision. Inspect a run's identity and status
before responding to a declared gate.

Project runtime scenarios: `harness/END-TO-END.md`.
Holdout scenarios: `.factory/holdout/HOLDOUT.md`, fenced by `.factory/hooks/holdout_fence.py`
(only the holdout-writer and holdout-verifier subagents may read it).
Ordinary checks: `python harness/ci.py`.

Record this application's tested source SHA, candidate, run IDs, coverage,
fresh-environment evidence and open integration limits here after testing.
