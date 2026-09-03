---
name: factory-review
description: Review your own diff before anyone else sees it, then write the PR record the factory reads.
argument-hint: optionally the base branch to diff against (default: the repo's default branch)
---

# factory-review

This step is two things now, and only the second one belongs to this factory.

## 1. The review itself is Archon's, not ours

```bash
archon workflow run archon-review "review my working changes"
```

`archon-review` ships bundled in the engine -- code, seams, simplify and tests
always, errors and docs when the change warrants them, each finding carrying the
lenses that raised it. Given a PR it reviews the PR; given nothing it reviews the
working diff against the merge base, which is what you want here. It writes
`review/report.md` and `review/findings.json` and edits nothing.

The factory used to carry its own one-shot review prompt. It was replaced rather than
kept alongside, because two review steps in one lap means two things to keep true and
the weaker one sets the standard.

> **If you want a different review, this is the seam.** A workflow named
> `archon-review` in your own `.archon/workflows/` overrides the bundled one --
> project scope beats bundled -- so you can replace the pack wholesale without
> forking anything or editing this factory.

## 2. The record is ours

**The instructions live in `.archon/workflows/factory/implement/commands/pr-record.md`.
Read that file now and follow it.** This skill deliberately does not restate the
content, because a second copy is a second thing to keep true.

Two adjustments for running it by hand rather than as a workflow node:

1. **`$ARTIFACTS_DIR` does not exist here.** Where the file asks for something from
   that directory, get the same thing from the repository: `MISSION.md`,
   `FACTORY_RULES.md` and `CLAUDE.md` are at the root, the issue is
   `gh issue view <n>`, and the review report is wherever the run above wrote it.
2. **The line telling you to defer to a `piv-*` skill is for the workflow node, not
   for you.** If this repository has that skill, running it is still the better
   answer.

## Why the holdout still holds when the reviewer is somebody else's

Every node the pack expands into grants `Read`, and none of them knows this factory
has a holdout. The include in `factory-implement.yaml` carries a `denied_tools` list
that Archon unions onto every expanded node, so the wall survives composition.

**Verify it, do not trust it.** Expand the workflow and confirm every non-exec node
under `review__` carries the deny. Without it a reviewer could quote a holdout
assertion into a report the fix node then reads, and the wall would be gone with
every check still green -- which is the failure mode this whole system exists to
make impossible.
