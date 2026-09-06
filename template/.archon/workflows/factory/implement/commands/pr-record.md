---
description: Turn the review pack's report into the PR record. Does not review, and does not open the PR.
argument-hint: (no arguments -- reads $ARTIFACTS_DIR)
---

# Node 8 · Write the PR record

> **THIS PROMPT IS THE PERSONALISATION LAYER.** The shape below is what `open-pr.py`
> reads. Change the prose freely; change the front matter and the pull request stops
> being validatable.

**You are not the reviewer.** The review already happened, in Archon's `sdlc` pack,
across parallel specialist lenses. Your job is to write the record a human will skim
in six months, and to carry that review's findings into it faithfully.

Do not re-review the diff. If you disagree with a finding, say so in the record and
say why; do not silently drop it. A record that quietly omits an Important finding is
worse than one that argues with it, because the argument is reviewable and the
omission is not.

## Read, in this order

1. `$ARTIFACTS_DIR/review/report.md` -- the canonical review report. This is the
   review.
2. `$ARTIFACTS_DIR/review/findings.json` -- the same findings, machine-readable, each
   with the lenses that raised it and a status of `open`, `fixed` or `disproved`.
   **Only `open` findings belong in the record's findings section.**
3. `$ARTIFACTS_DIR/implementation.md` -- the documented deviations. A documented
   deviation is an intentional decision, not a finding.
4. `$ARTIFACTS_DIR/issue.md` -- what was actually asked for.
5. `$ARTIFACTS_DIR/discoveries.md`, when it exists -- validated findings **outside**
   this run's scope. No issue tracker knows about them. Surface them under
   Assumptions rather than dropping them, because nobody else will.

If `review/report.md` is missing, say so plainly in the Review findings section
rather than inventing a clean review. A record that claims a review nobody ran is the
one failure this node can cause that nothing downstream will catch.

## Then write `$ARTIFACTS_DIR/pr.md`

One file, at exactly that path. A script turns it into a real pull request after you
exit -- **you do not open it, and you are not given `gh`.** Same rule as the merge: a
model's only output is a record, and code decides what happens to it.

The front matter is read by that script. `issue` and `title` are load-bearing.

```markdown
---
issue: <the issue number, digits only>
title: <the change, in the imperative, as a commit subject: "fix: ..." / "feat: ...">
---

## What changed
<2-4 sentences, in terms of the product, not the files.>

## Why
<the problem from the issue, in one or two lines.>

## Files
<path -- why it changed>

## Validation
<the counts from the self-check: static, unit, e2e steps, holdout, mutations.>

## Review findings
<For each OPEN finding: severity / the lenses that raised it / file:line / what and
why. Say "none" only when findings.json is an empty array or every entry is `fixed`
or `disproved`. For anything being shipped rather than fixed, say why that is
acceptable.>

## Assumptions
<if the plan recorded any, restate them here so the human merging sees them without
opening another file -- or "none". These hold the auto-merge. Add any discoveries
from discoveries.md here, marked as out of scope for this change.>

## Floor raise to apply
<if assertions were added, the new .factory/locks/floor.json values for a human to
commit, since that file is protected -- or "none">
```

Keep the body readable by a human who has not seen the issue.

**Do not merge, and do not approve.** The gate and the merge decide, and they
re-check the markers themselves rather than trusting anything in this file.
