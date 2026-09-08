---
name: factory-implement
description: Take one accepted issue from issue to reviewed pull request, the way the factory does it.
argument-hint: the issue, e.g. `gh:issue:12`
---

# factory-implement

**The whole issue-to-PR path is `archon-ship`'s, not ours.** One governed run: it
grounds the item against the current repository, takes the least speculative route
(investigate an unknown cause, plan an undecided shape, or deliver directly), then
implements, reviews and publishes a pull request.

```bash
archon workflow run archon-ship --branch factory/implement-issue-12 --base main \
  --input target="https://github.com/OWNER/REPO/issues/12" \
  --input publication_policy=/absolute/path/outside/the/checkout/publication.json
```

There is no separate plan step to run first. `archon-ship` decides whether planning is
owed, and `factory-plan` exists for when you want that decision on its own.

## What the factory adds around it

Only the things a generic pipeline cannot know:

- **the publication policy.** An absolute path to operator-owned JSON naming one argv
  command and the protected paths. The factory points it at `factory/fixed_gate.py
  --publication`, rebuilt from the BASE tree, which enforces the protected list, the
  size and scope caps and the secret preflight. **A model's summary cannot waive it**,
  and a nonzero exit refuses publication.
- **the issue linkage**, recorded on disk rather than parsed back out of the pull
  request body. `Closes #N` is prose, an agent writes it, and one run put it inside
  backticks so GitHub ignored it entirely.
- **the state machine.** The issue goes `accepted -> in-progress` before the dispatch
  and the pull request arrives at `factory:needs-review`. A delivery never marks its
  own work approved.

## The holdout

`.factory/holdout/**` is on the protected list, so no delivery can edit it. Reading is
the harder half: every node the pack expands into grants `Read`, and none of them knows
this factory has a holdout. Check the deny reaches them -- expand the workflow and look
-- because a builder that can read the assertions it is measured against optimises
directly against the answer key, with every check still green.
