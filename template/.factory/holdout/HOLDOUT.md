# Holdout scenarios

Acceptance checks the agents building the code never see. They are written from the product document,
not from the issues or the code, and they test the space between issues: one rule on every surface,
sequences, what must never happen by any route, and old behaviour that has to keep working.

This file is committed so the team shares it. People read and edit it freely. Agents cannot: the hook
in `.factory/hooks/holdout_fence.py` refuses every agent except the `holdout-writer` and
`holdout-verifier` subagents, and `.ignore` keeps the folder out of searches. The app check and the
manager run these through the verifier, which reports only a verdict and what a user sees.

No scenarios yet. Write the first set before the backlog is built (ask your coding agent to dispatch
the holdout-writer with the product document), or by hand in this form:

```
## H-01 A short title in the user's words
Protects: the product rule it guards (PRD section or rule number)
Touches: the product areas it exercises, so a change is checked only against scenarios it can affect
Setup: the state to start from
Steps: what a user does, through the app's public surface
Expect: what a user sees, exactly where the product document fixes it
Author: owner
```

Scenarios are added and tightened, never loosened, except by the owner with a reason recorded here.
