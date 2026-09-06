---
name: factory-fix
description: Address review findings on an existing pull request, and nothing else, using Archon's sdlc builder.
argument-hint: the pull request, e.g. `gh:pr:14`
---

# factory-fix

**The fixer is Archon's, not ours.** Addressing findings *is* implementing -- the only
difference is where the work order came from, and `archon-implement` takes review
findings as a first-class input.

```bash
archon workflow run archon-implement "Address the findings in <findings file>, and nothing else. \
The issue this change was meant to serve is <issue>. Do not re-derive the findings from the diff."
```

The factory's own fix prompt is gone. It had no loop, no green verdict and no decline
guard while the build path had all three, so the correction path was the weaker of two
builders for no reason anyone chose.

## The one rule that is not about the code

**Read the findings. Do not re-derive them.**

The findings are the record of an independent judge that has already run and whose
working copy no longer exists. A fix node handed nothing does not crash -- it re-reads
the diff, invents an objection, and produces a confident commit that addresses something
nobody asked about, having spent one of two attempts. In the workflow, `prepare` asserts
the findings are on disk before the builder starts. By hand, that is on you:
`gh pr view <n> --comments` and read what the validator actually said.

A finding you cannot see the cause of is one to report, not one to reinterpret.

## What the factory adds around it

- **`prepare`** checks the attempt cap before anything is spent, and loads the findings.
- **`land`** commits, pushes, bumps the attempt counter and sets the PR back to
  `needs-review`. **One transition, and never to `passed`:** a fix is never
  self-certified. The node that made the change does not get to decide it worked.
- Since the builder commits its own work, a clean tree with commits ahead of base is
  success. Asserting on a dirty tree threw away a correct ten-minute fix once already.

## The holdout

The include carries a `denied_tools` list covering `.factory/holdout/**`, and Archon
unions it onto every node the pack expands into. It matters more here than anywhere: a
fixer that has already been told what is wrong, and can also read the assertions it is
measured against, is optimising directly against the answer key.
