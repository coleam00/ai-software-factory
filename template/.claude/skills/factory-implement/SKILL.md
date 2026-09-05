---
name: factory-implement
description: Build one planned issue the way the factory builds it, using Archon's sdlc builder.
argument-hint: optionally the plan to implement (default: the run's plan.md)
---

# factory-implement

**The builder is Archon's, not ours.**

```bash
archon workflow run archon-implement "Implement plan.md in full. Nothing outside the plan."
```

`archon-implement` ships bundled in the engine. It loops the implement command until it
reports done (max 5), returns `{done, green, red_cause, summary}`, commits as it goes,
and ends in a deterministic `assert-changed` guard -- because an AI node that declines
its task still exits 0.

The factory's own single-shot implement prompt is gone. It had no loop, no green verdict
and no decline guard, so keeping it alongside this would have meant maintaining the
weaker of two builders.

## What the factory adds around it

- **`commit`** asserts the branch actually differs from its base. Since the builder
  commits its own work, a clean tree with commits ahead of base is success -- the fatal
  case is a branch identical to base, which is the whole lap being theatre.
- **`guard`** enforces the protected paths and the size and scope caps, before anything
  judges the diff.
- **`selfcheck`** runs the full gate for the builder's benefit, so a lap does not spend a
  validation cycle learning it is red.

## The holdout

The include carries a `denied_tools` list covering `.factory/holdout/**`, and Archon
unions it onto every node the pack expands into. Verify it rather than trusting it: none
of those nodes knows this factory has a holdout, and without the deny a builder could
read the assertions it is being measured against, with every check still green.
