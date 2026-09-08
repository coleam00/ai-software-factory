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
it does.

**The automatic path does not call this.** `archon-ship` decides for itself whether an
item needs planning, investigation or neither, and routes accordingly -- so running the
planner on its own is what you do when you want that judgement separately, or when you
want to read a plan before spending a delivery on it.

## If you want a different planner

Put a workflow named `archon-plan` in your own `.archon/workflows/`. Project scope beats
bundled, so yours wins with nothing here to change. That is the seam, not a fork.

The same seam reaches the workflow that JUDGES. The factory dispatches `archon-accept`
by name and does not pin where the definition comes from, so an `archon-accept` in your
own `.archon/workflows/` is what evaluates your candidates. That is your call to make --
`factory doctor` names any of the six it finds defined here so it is never invisible.
What a *candidate* cannot do is supply its own judge: the gate acceptance runs is
rebuilt from the base branch, and the guard rejects any pull request that touches it.

## The one rule that outlives any planner

Nobody reads the diff before it merges. A planner should declare `ready: false` rather
than plan around an ambiguity it would normally raise in review, because there is no
review to raise it in.
