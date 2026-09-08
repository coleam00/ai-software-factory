---
name: factory-fix
description: Repair the findings on an existing pull request, in place, without replacing it.
argument-hint: the pull request, e.g. `gh:pr:14`
---

# factory-fix

**The repair is `archon-revise-pr`'s, not ours.** It checks the pull request out COLD --
a fresh clone and an engine-created worktree, with no artifacts from the run that built
it -- reuses the same implementation workflow the build path uses, and pushes the repair
to the SAME head without force. It never creates a replacement pull request and never
merges.

```bash
archon workflow run archon-revise-pr --branch factory/fix-pr-14 --base main \
  --input target_pr=14 \
  --input work_order=/absolute/path/outside/the/checkout/work-order.txt \
  --input findings=/absolute/path/outside/the/checkout/findings.json \
  --input publication_policy=/absolute/path/outside/the/checkout/publication.json
```

## The one rule that is not about the code

**Read the findings. Do not re-derive them.**

The findings are the record of an independent judge that has already run and whose
working copy no longer exists. A repair handed nothing does not crash: it re-reads the
diff, invents an objection, and produces a confident commit addressing something nobody
asked about, having spent one of two attempts. The factory passes the `findings` array
straight out of the acceptance receipt, and refuses to dispatch at all unless the
recorded verdict is `request_changes`. By hand, that is on you.

## What the factory adds around it

- **the attempt cap** (FACTORY_RULES 8), checked before anything is spent and counted in
  append-only `factory:attempt-N` labels, so the count survives the worktree.
- **the transition.** A repaired pull request goes back to `factory:needs-review`, never
  to `factory:approved`. The node that made the change does not get to decide it worked.
- **the identity refusal.** A returned pull request that is not the one we asked to
  repair is refused rather than adopted.

`delivered` here means the repaired head was published to the same pull request. It does
not mean acceptance passed; the factory validates it again, independently.

## The holdout

It matters more here than anywhere. A fixer that has already been told what is wrong,
and can also read the assertions it is measured against, is optimising directly against
the answer key.
