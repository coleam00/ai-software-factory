---
name: factory-judge
description: Judge a pull request as an independent reader who did not write it.
argument-hint: the pull request, e.g. `gh:pr:14`
---

# factory-judge

**The judgement is `archon-accept`'s, not ours.** It fetches the exact head and base
into a temporary repository of its own, runs the commands a trusted profile pins,
hands a fresh judge the real streams and the original request, and writes an
`acceptance.json` receipt. It never merges, comments, or touches the pull request.

```bash
archon workflow run archon-accept --no-worktree \
  --input target=OWNER/REPO#N \
  --input work_order=file:/absolute/path/outside/the/checkout/work-order.txt \
  --input policy=/absolute/path/outside/the/checkout/gate-profile.json
```

Both paths must resolve OUTSIDE the application checkout. That is not tidiness: a
policy the candidate can write is not a policy, and a work order the candidate can edit
is not the thing it was asked to do.

## What the factory supplies

`factory/sdlc.py` builds both inputs for every automatic validation, and it is worth
knowing what they are before you hand-roll them:

- **the work order** is the ORIGINAL issue text, captured the first time the factory
  needed it and kept outside every checkout. Re-reading the issue now reads whatever it
  says today.
- **the policy** pins one command: `factory/fixed_gate.py`, reconstructed out of the
  BASE tree along with `config.py`, `guard.py`, `gate.py` and `tripwire.py`. The gate a
  pull request is held to is the gate a human last agreed to, and the candidate cannot
  supply it.

To reproduce one by hand, read the `gate.json` and `policy.json` of a real run under the
operator runtime root (`factory doctor` prints where that is).

## What the receipt does and does not prove

`verdict == "approve"` is approval. Workflow success is not: a run that finishes and
returns `inconclusive` finished. Read `checks` before you read `summary` -- deterministic
failed checks outrank a model's approval, and incomplete evidence outranks findings.

**These worktrees are not sandboxes.** Candidate code runs as you, with your
credentials. The receipt says `isolation: fresh_context_only` and means exactly that.

## What the factory adds around it

- **the identity check, twice.** The receipt certifies one repository, one PR and one
  pair of commits. The factory re-resolves the live head and base before it applies
  anything, and a pull request that moved during validation goes to a person.
- **the digests.** The stream and evidence hashes in the receipt are re-computed against
  the run's own artifacts. A digest nobody checks is decoration.
- **the hold.** Green with ratchet slack, uncalibrated margins or recorded assumptions
  becomes `factory:held`, not `factory:approved`. Nothing is wrong; a person has to
  agree, with `factory accept gh:pr:N`, which is the command that keeps the record.
