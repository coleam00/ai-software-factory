# Mission

<!--
  Owner: humans only. This file is on the protected list; the factory cannot edit it.

  THIS IS THE ONE FILE NOBODY CAN SHIP FOR YOU. `FACTORY_RULES.md` arrives nearly
  complete because the discipline that makes an unattended agent safe is the same
  everywhere. What is being built, and what must never be built, is not.

  Replace every <angle-bracket> before the first lap.

  This file is the compression of your spec down to the part an agent has to OBEY.
  When the product changes, this changes in the same commit -- or the factory keeps
  faithfully building the old scope and nothing warns you.
-->

**Derived from:** <path or URL of the PRD / spec this was compressed from>
**Last reconciled with it:** <YYYY-MM-DD>

## What <PRODUCT> is

<One paragraph, in the language a product manager would use. What it does, for whom,
and what a user gets out of it. Not the architecture.>

<A second short paragraph naming any assumption baked into the design -- single
tenant, single channel, one deployment -- because those become invariants below.>

## Who it is for

- <the user, and the specific thing they are trying to do>

<PRODUCT> is not <the nearest adjacent thing people will mistake it for>.

## Core capabilities (in scope)

The factory may accept issues in these areas.

**<Capability area>**
- <specific capability>
- <specific capability>

**<Capability area>**
- <specific capability>

## Out of scope -- the factory must never build this

<!--
  THE SECTION THAT EARNS ITS KEEP. This is how an agent recognises that a plausible,
  well-argued, easy-to-implement feature request is DRIFT rather than a good idea.
  Without it, every request is arguably in scope, because almost every feature is
  defensible in isolation -- and the factory will build all of them.

  NEVER, NOT "NOT YET". Everything here is rejected forever, including the quarter it
  lands on the roadmap. Anything merely deferred belongs in the backlog and must NOT
  appear here. Copying a spec's non-goals across without doing that sort is the most
  common way this section quietly becomes wrong.

  Aim for at least five, and make them things a reasonable person might ask for.
  Fewer than five means the list is too thin to do any work.
-->

**<Category>**
- <thing>
- <thing>

**<Category>**
- <thing>

## Hard invariants -- not tunable by any issue

These are not features. They are properties that define what <PRODUCT> is. The
factory cannot modify them even if an issue asks nicely, gives a good reason, or
calls it a bug. Changing one requires a human commit.

1. **<Invariant>.** <Why it exists, in one sentence, so a reader can tell whether an
   edge case is covered.>
2. **<Invariant>.**
3. **The factory cannot modify governance files.** `MISSION.md`, `FACTORY_RULES.md`
   and the conventions file are the constitution. A PR touching any of them is an
   automatic reject.
4. **The factory cannot modify its own judge.** `harness/` and `.factory/holdout/`
   define what "working" means here. Adding an assertion is
   always welcome; removing or loosening one is a human decision, always.

## Allowed evolutions

Explicitly in scope, so the factory does not reject them as architectural drift:

- <the one architectural change you are willing to let it make, if any>
- <areas where quality can be improved freely>

## Definition of done

Every change the factory ships clears all three gates.

**Gate 1 -- static checks and tests pass.** <the exact commands>

**Gate 2 -- <the product-level quality bar>.** <e.g. any new user-facing feature is
usable without documentation.>

**Gate 3 -- the end-to-end path passes as a real user.**

1. <start the app>
2. <the first user action>
3. <...>
4. <the observable result a user would notice>

This runs on every change that touches runnable code, including ones that "seem
unrelated". It is not optional.

## Open questions -- decisions nobody has made yet

<!--
  WORD THIS CAREFULLY, because the obvious wording breaks the factory.

  "Open questions the factory must never answer" makes every issue that touches one
  escalate, although an unspecified PRODUCT value is exactly what the plan step
  decides, records, and holds at the merge for a human. The wording would escalate
  the very issues this section exists to unblock.

  "Open" means I HAVE NOT DECIDED. It does not mean you may not propose.
-->

These are undecided, not forbidden. **The factory may propose an answer to any of
them**, build against it, and record what it assumed -- the merge is then held for a
human, so nothing ships on a guess and nothing stops for one.

- **Q1** <the question, phrased as a decision rather than a topic>
- **Q2** <...>

**Except these, which do stop the factory** -- they are irreversible (see
`FACTORY_RULES.md`) rather than open in the ordinary sense:

- <the one about identity, auth, or who may act as whom>
- <the one about migrating or deleting stored data>

Once answered, an entry moves to **Decisions** below with its answer and date, and
stops being asked. **A decision is asked once.**

## Decisions

<!-- Answered questions, newest first: the question, the answer, the date. -->

- <YYYY-MM-DD> **<the question>** <the answer>

## What the factory does NOT own -- permanently human

<!--
  THE FACTORY'S SCOPE IS SMALLER THAN THE PRODUCT'S, and saying so here is what stops
  a green gate being read as "the product is good". It never meant that. It means the
  layer a machine can check is intact.

  These are not a backlog and they are not "not yet". They are a different kind of
  work, and it stays with a person.
-->

- <does it FEEL right -- weight, pacing, difficulty, tone>
- <does it LOOK right -- layout, hierarchy, whether two states read as different>
- <is it UNDERSTANDABLE -- can a first-time user work it out without being told>

The factory owns <the domain rules · the data model · the simulation>: the layer
whose correctness can be asserted. That is usually where most of the risk lives, and
it is the half that can be defended. The list above is reviewed by a human, on
purpose, forever.
