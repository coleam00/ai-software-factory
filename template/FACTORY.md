# The factory

<!--
  Maintainer: whoever raises the autonomy dial. Update the level and the date in the
  SAME commit that changes the level -- a stale level here is a lie about what is
  running unattended.

  This file is on the protected list. The factory cannot edit it.
-->

**Current autonomy level: <N>** -- <one line: what is automatic at this level>
**Raised to this level on:** <YYYY-MM-DD>
**Stop button:** `touch .factory/STOP`, or label any open issue `factory:stop`
**Built from:** <path or URL of the spec `MISSION.md` was compressed from>

---

## The process this encodes

<The AI coding workflow this factory automates, as the ordered steps you already ran
by hand. Name the skills and rules files loaded at each one. The factory is this
process with the approvals removed, and writing it down here is what makes that
legible to the next person.>

```
plan -> implement -> commit -> guard -> self-check -> review -> open PR
   ^        ^                                            ^          |
   |        |                                            |          |
   +--------+--- Archon's sdlc pack, composed by include:+          |
                                                                    |
                                (independent, separate process)     |
                                                                    v
                      prepare -> guard+gate -> judge -> merge / hold / fix

           weekly:  sync main -> full gate -> investigate -> file issues
                                                  ^
                                       also the pack's (archon-investigate)
```

The plan, implement and review steps are `archon-plan`, `archon-implement` and
`archon-review`; the weekly regression's root-cause step is `archon-investigate`. They
ship inside the Archon binary, so there is nothing to install and nothing to keep in
sync, and a same-named workflow in `.archon/workflows/` overrides any of them. The
`prime` step this diagram used to open with is gone: `archon-plan` does its own
grounding, so the prompt that existed to feed a planner had exactly one reader and went
with it.

---

## The five components, as built here

| # | Component | This repo's version |
|---|-----------|---------------------|
| 1 | Workflow-driven repo | Archon, five workflows in `.archon/workflows/factory/` |
| 2 | The trigger | `factory/dispatch.py` every <N> minutes, `factory/regress-trigger.py` weekly |
| 3 | Deployment | <strategy -- or "not yet closed; merging is where this stops"> |
| 4 | Guidance layer | `MISSION.md` · `FACTORY_RULES.md` · `CLAUDE.md` |
| 5 | Validation harness | `harness/ci.py`, holdout at `.factory/holdout/`, <N> deliberate defects |

---

## The gates that are actually code

Everything else is a prompt instruction, which is a suggestion with good manners.
These are the ones a model cannot argue past:

1. **`factory/gate.py`** -- asserts every required marker, checks the counts against
   the ratchet, and **overrides the judge when the raw output disagrees with it**.
2. **`factory/merge.py`** -- re-checks the guard and the merge state itself before
   touching a branch. It does not trust that the gate already did.
3. **`factory/guard.py`** -- the protected list and the two caps. Fails **closed**: a
   diff that cannot be computed is not a diff that was checked.
4. **`factory/state.py`** -- the transition table. A node that wants a move the table
   forbids has misunderstood something, and inventing the transition would bury it.
5. **The `held` state.** A hold is not a message: a PR the gate held gets
   `factory:held`, which nothing dispatches and no node may leave. Only a human moves
   it back to `open` for a fresh validation.
6. **`factory/_selftest.py`** -- and this one checks the four above. What counts as
   alive, what counts as passed, what may move: each was once wrong in a way that read
   as a quiet, healthy repository. `doctor` runs it on every audit.

---

## The end-to-end path

The single user journey that gates every merge:

1. <start>
2. <action>
3. <the observable result a user would notice>

Required step count: **<N>** (`E2E_PASSED steps=<N>`).

**Last deliberately broken and confirmed failing:** <YYYY-MM-DD>
An end-to-end check that has never failed is not known to work. The mutation set does
this on every run; record here the last time you watched it happen on purpose.

---

## The mutation set, and which rung catches what

<N> deliberate defects, and the spread matters more than the score:

| rung | defects aimed there |
|---|---|
| unit | <...> |
| e2e | <...> |
| holdout | <...> |
| app-start | <...> |

**If that column collapses onto one rung, the set has stopped measuring what it was
built to measure.** A perfect score where everything is caught by the unit suite means
"the unit suite can fail", not "the gate can fail".

**Known gaps, stated rather than hidden:** <a rung you do not have -- e.g. no browser,
so no presentation defect. A defect nothing can catch is worse than the gap.>

---

## The ratchet

`.factory/locks/floor.json`, protected, so only a human commit moves it.

| key | floor | why it is that number |
|---|---|---|
| `e2e_steps_asserted` | <N> | <...> |
| `holdout_assertions` | <N> | <...> |
| `unit_tests` | <N> | <...> |

**Slack pins the dial.** The gap between observed and floor is exactly how many
assertions could be deleted with the gate still green -- and it *grows* as the harness
improves, because raising the floor is a protected edit the factory cannot make. So
any slack caps autonomy at level 2 until a human raises the numbers. That is
deliberate: printing slack as a note and carrying on is how the hole widens forever.

---

## The autonomy ladder, and where we stop

| Level | Automatic | Reached |
|---|---|---|
| 1 | labelled issue → branch + PR | <date> |
| 2 | + the validator runs and writes a verdict | <date> |
| 3 | + auto-merge on green structural gates | <date> |
| 4 | + self-triage, and the regression files its own bugs | <date> |
| 5 | + it writes its own issues from the mission | <date> |

**Before the next notch, these must be true:**

- [ ] <the specific thing>

---

## Operating notes

- **Cost.** <what one completed lap actually costs, MEASURED not projected.>
  Instrumented on <date>. Projections for this are wrong by 10–20× in the same
  direction every time.
- **Model routing.** Planning slot: <model>. Implementation slot: <model>. A premium
  model in one of the two buys most of the quality of both.
- **What reaches a human.** Only `factory:needs-human`, via `FACTORY_NOTIFY_CMD`.
  <the exact channel>
- **Known gotchas for this repo.** <e.g. the deploy polls rather than using a push
  trigger, because commits made with the default GitHub token do not fire workflows>

---

## Incident log

Append only. Every entry is a rule that now exists because of it.

| Date | What happened | What changed as a result |
|---|---|---|
| <YYYY-MM-DD> | <what broke> | <the rule or gate added> |
