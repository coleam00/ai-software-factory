---
description: Read a red regression run and turn it into issues a factory can actually build from.
argument-hint: (no arguments -- reads $ARTIFACTS_DIR/investigation.md and regress.log)
---

# File the regression as issues

The scheduled gate ran against the default branch and went red, and an investigation
has already established what broke and why. **You are not the detective.** Your job is
to turn that investigation into **issues a factory can build from** -- or to say,
clearly, that this was not a product failure at all.

## Read

- `$ARTIFACTS_DIR/investigation.md` -- **first, and in full.** This is Archon's
  `archon-investigate` report: the proven causal chain, the reproduction, and the
  evidence behind it. It is the work order. Where it and your own reading of the log
  disagree, the report has a reproduction and you do not -- but say so in `summary`
  rather than quietly overriding it.
- `$ARTIFACTS_DIR/regress.log` -- the full output of the run, for the exact assertions
  and values you quote into a body.
- `MISSION.md` -- so an issue you file is one triage can accept
- `git log -20 --oneline` and `git show` on the recent merges, to name a suspect

An investigation that ends inconclusive is still worth filing. Carry its honest gaps
into the body rather than inventing the certainty it declined to claim.

## First, the question that decides everything else

**Did the software break, or did the harness fail to run?**

Set `infrastructure_failure: true` and file nothing when the log shows a dependency
that would not install, a port already in use, a checkout that did not complete, a
missing environment variable, a browser that is not installed, a timeout on startup,
or the harness never printing `HARNESS_START` at all.

Those are real problems and they are **not product defects**. Filing them as bugs
puts fiction in the backlog, and every one of them costs a triage cycle and a
human's afternoon to work out that the actual problem was a missing package.

When in doubt, `infrastructure_failure: true`. A missed regression is found on the
next run; a fabricated backlog is found by a person.

## Then, one finding per actual failure

Not one per red line -- **per distinct cause**. Six assertions failing because one
function returns the wrong shape is ONE finding.

Each finding needs to survive being read cold by triage and then by a plan node that
has never seen this log:

- **`title`** -- what a user would notice, not which assertion failed.
  Bad: `test_settle_minimal failed`.
  Good: `Settling up suggests more transfers than necessary after a member leaves`.
  The title is also the dedup key, so keep it stable across runs of the same defect.

- **`body`** -- a real issue, in the shape this repo's issues take:
  - **What happens** -- the observable symptom, with the exact assertion and the
    values from the log.
  - **What should happen** -- the observable difference, not "it should pass".
  - **Reproduction** -- the steps, taken from what the harness did.
  - **Evidence** -- the relevant excerpt of the log, fenced. Trim it to what matters;
    a 400-line paste is a body nobody reads.
  - **Suspect** -- recent commits that touched the area, with SHAs, and why you
    suspect them. Say "unclear" if it is unclear. A confident wrong suspect costs
    more than an honest shrug.
  - **Which rung caught it** -- static, unit, e2e, holdout or mutation. It tells the
    fixer how far from the surface the defect is.

- **`severity`** -- `critical` if merged code is broken for every user or data is at
  risk; `high` if a core capability is broken; `medium` for a non-core break;
  `low` for something cosmetic that still failed a check.

- **`area`** -- one of `MISSION.md`'s capability areas, so triage can place it.

## Two rules about scope

**Do not propose the fix.** You are filing a bug, not planning one. The plan node
does that later with full context, and a proposed fix in an issue body anchors it
onto your first idea.

**Do not file anything the harness cannot see.** If the only evidence is your
suspicion, it is not a finding. Say it in `summary` instead.

## Summary

Two or three sentences a human reads first thing: what broke, how bad, and whether
anything was filed. If nothing was filed, say why in a way that does not require
opening the log.
