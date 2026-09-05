---
name: factory-plan
description: Plan one issue the way the factory plans it, using Archon's sdlc planner.
argument-hint: the issue to plan, as gh:issue:<n>
---

# factory-plan

**The planner is Archon's, not ours.**

```bash
archon workflow run archon-plan "Plan gh:issue:<n>. Read MISSION.md and FACTORY_RULES.md first."
```

`archon-plan` ships bundled in the engine. It grounds itself against the current
repository, writes `plan.md`, returns `ready`, and refuses to mutate the checkout while
it does. The factory used to carry a `prime` prompt and a `plan` prompt of its own;
both are gone, because two planners in one system is exactly the duplication this
integration exists to end. `priming.md` had one reader and went with them.

## What the factory adds around it

Only the things a planner cannot do for itself:

- **`preflight`** puts `issue.md`, `MISSION.md`, `FACTORY_RULES.md`, `decisions.md` and
  `PRIOR-ATTEMPT.md` into the run's artifacts first, so the planner is pointed at them
  instead of sent looking.
- **`gate-plan`** turns `ready: false` into a parked issue and a cancelled run. That is
  the one thing the pack cannot do, because only this repo knows what "park it" means.

## If you want a different planner

Put a workflow named `archon-plan` in your own `.archon/workflows/`. Project scope beats
bundled, so yours wins with nothing here to change. That is the seam — not a fork.

## The one rule that outlives any planner

Nobody reads the diff before it merges. A planner should declare `ready: false` rather
than plan around an ambiguity it would normally raise in review, because there is no
review to raise it in.
