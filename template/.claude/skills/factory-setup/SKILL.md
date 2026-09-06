---
name: factory-setup
description: Set up the factory in this repo. Reads the code, asks four questions, writes the three files only a human can decide.
argument-hint: none
---

# factory-setup

Get this repo from "installed" to "a lap can run". You do the reading. The person
answers four questions.

## The rule that matters

**Never ask what you can read.** The language, the test command, the start command,
the entry points, the routes, the existing docs are all in the repo. Go and look.
Every question you ask that the repo already answered is a question that makes
somebody regret starting.

Four questions is the budget. Not four rounds, not four topics. Four.

Everything else you decide yourself and say so in one line, so it can be corrected
later. Getting a default wrong costs an edit. Asking about it costs trust.

## Before you ask anything

1. Read `MISSION.md`, `harness/END-TO-END.md` and `.factory/holdout/HOLDOUT.md`.
   If they no longer contain `SCAFFOLD_EXAMPLE`, they are already written. Skip
   straight to the doctor at the end and say what you skipped.
2. Read the README, any PRD or spec, the package manifest, the entry point, the
   route or command table, and the test directory.
3. Run `python factory/doctor.py` and read the failures. They are the todo list.
4. Write down, for yourself: what this software does, who uses it, how it starts,
   how its tests run, and the three or four things a user most obviously does with
   it. You are about to propose all of that back.

## The four questions

Ask them **one at a time**, each with your proposed answer already filled in, so
the cheapest reply is "yes". Keep each one under four lines. No preamble.

**1. What this is.**
> Here is how I would describe it: *<your one-paragraph draft>*.
> Right, or what would you change?

**2. What it must never become.**
> Here are seven things I would put on the never-build list: *<list them>*.
> Strike any that are wrong and add anything I missed.

This is the load-bearing question and it is the one to spend the extra minute on.
Propose things a reasonable person would actually ask for, because that is the
only kind worth refusing. "No time travel" protects nothing. "No multi-tenancy",
"no plugin system", "no mobile app" are the ones that get argued for at 3am.

Sort what comes back:
- **never** goes in `MISSION.md`
- **not yet** goes in the backlog and must NOT go in `MISSION.md`, because
  anything listed out of scope is refused forever, including the quarter it
  becomes the roadmap
- **never, and it is a property rather than a feature** goes in the invariants
  section

**3. The journey that matters most.**
> The most valuable thing someone does here looks like: *<your draft, as steps>*.
> Is that the one, and what would you check at the end?

**4. How it runs, and how a test reaches it.**
> Start: `<detected>`  ·  Tests: `<detected>`  ·  Agent: `<detected>`
> Reached as: `<http | cli | library>`
> Anything wrong there?

The last one is the only part of question 4 you cannot read off the repo with
certainty, and getting it wrong is expensive: `driver` ships as `http`, so a
library with no server sits there waiting for a port that never opens. Propose the
shape you actually see (`module.exports` and no listener means `library`) and set
`driver` plus its config block to match.

Then stop asking. If something else is genuinely undecidable, pick the safer
option, write it down, and mention it at the end in one sentence.

## What you assume rather than ask

| | |
|---|---|
| Autonomy level | 0. It is raised later, after a lap has run. Never ask on day one. |
| Protected paths | The defaults in `FACTORY_RULES.md`, plus anything auth, billing or migration shaped that you found. Say which you added. |
| Priority order, size caps, attempt caps, cost caps | The shipped defaults. |
| Which agent runs the journeys | Whatever `init` detected on PATH. |
| Notification channel | The shipped `.factory/notify.sh`. Mention ntfy once, at the end, in one line. |
| Deployment | Leave unset. It is the last component and it needs a real answer, not a guess. |

## What you write

**`MISSION.md`** from answers 1 and 2. Replace every `<angle-bracket>`. The
out-of-scope list needs at least five entries or the doctor warns, and the warning
is right.

**`harness/END-TO-END.md`** from answer 3, plus one or two more journeys you
propose yourself from the routes and commands you read. Two to five total. Name
real values, not "returns successfully".

**Write what the product DOES, not what it should do.** A journey for behaviour
that does not exist yet makes the gate red before the first lap has run, and a
permanently red gate means nothing can ever merge, including the change that would
make the journey pass. Measured: a setup run put "survive a restart" in MISSION as
in-scope, correctly, and then wrote a restart journey against an in-memory store.
The scope belongs in `MISSION.md`. The gap belongs in an issue. The journey file
describes today.

**`.factory/holdout/HOLDOUT.md`** yourself, and do not ask. Same rule: today's
behaviour, not the roadmap. Two or three
scenarios that compose several features in a sequence, use values appearing
nowhere else in the repo, and assert exact figures worked out by hand. Do not
reuse a journey from `END-TO-END.md`. Then say, in one line, that the person
should read it, because it is the file the whole auto-merge rests on.

**`harness/harness.config.json`** from answer 4.

**`FACTORY_RULES.md` section 5 and `factory/config.py`'s `PROTECTED_EXTRA`, together
and in the same pass.** They are one fact written twice and `factory doctor` fails when
they disagree: section 5 is what the planner and the judge read, `PROTECTED_EXTRA` is
the only half that can stop a commit.

Replace section 5's `<angle-bracket>` spans with the real paths you found while reading
the repo -- the auth module, the rate-limit constant AND the code that enforces it,
payment paths, migrations, Dockerfiles, `deploy/`, `infra/` -- and put the same patterns
in `PROTECTED_EXTRA`. Name what carries a blast radius the project cannot absorb, not
everything that looks important; over-protecting means every ordinary issue auto-rejects.

Two things to get right, both of them paid for:

- A glob cannot express a REGION of a file. "the branch in `server.py` that consumes
  the rollout" is not enforceable, so it is not a rule -- protect the module that owns
  the decision instead, and say in the commentary what is deliberately left open.
- Anything the operator edits goes in `config.py`. It is the one file `bin/sync-to.py`
  will not overwrite; a list added anywhere else in `factory/` is deleted by the next
  sync with every check still green.

## Finish

```bash
python factory/doctor.py
```

Show what is still failing and what each failure blocks. Do not try to make it
green. A red doctor after setup is the correct state, and the remaining failures
are the honest list of what is left.

Then say exactly this much, and no more:

- the three files you wrote, and which one they should read first
- anything you assumed that they might want different
- the next command: `factory run implement gh:issue:<n>`, one lap, watched
