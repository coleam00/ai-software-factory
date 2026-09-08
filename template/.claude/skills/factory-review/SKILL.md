---
name: factory-review
description: Review your own diff before anyone else sees it, using Archon's sdlc reviewer.
argument-hint: optionally the base branch to diff against (default: the repo's default branch)
---

# factory-review

**The review is Archon's, not ours.**

```bash
archon workflow run archon-review "review my working changes"
```

`archon-review` ships bundled in the engine -- code, seams, simplify and tests always,
errors and docs when the change warrants them, each finding carrying the lenses that
raised it. Given a pull request it reviews the pull request; given nothing it reviews
the working diff against the merge base, which is what you want here. It writes
`review/report.md` and `review/findings.json` and edits nothing.

**The automatic path does not call this either.** `archon-ship` runs the review inside
its own delivery tail, so a factory lap has already been reviewed by the time a pull
request exists. This skill is for the diff on your machine, before that.

> **If you want a different review, this is the seam.** A workflow named `archon-review`
> in your own `.archon/workflows/` overrides the bundled one -- project scope beats
> bundled -- so you can replace the pack wholesale without forking anything or editing
> this factory.

## The pull request record

`archon-pr` writes the title, body and base, reuses an existing open pull request rather
than opening a second one, and reads back what it published. The factory adds exactly
one thing to that record: a comment naming the issue this work answers, posted once per
run and keyed on the run id so a retry cannot post it twice.

## Why the holdout still holds when the reviewer is somebody else's

Every node the pack expands into grants `Read`, and none of them knows this factory has
a holdout. **Verify the deny reaches them, do not trust it.** Without it a reviewer could
quote a holdout assertion into a report the repair node then reads, and the wall would be
gone with every check still green -- which is the failure mode this whole system exists
to make impossible.
