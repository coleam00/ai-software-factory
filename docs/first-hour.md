# The first hour

What to do after `factory init`, in order.

The doctor is a checklist and working through it is the build. It fails on a fresh
install. That is it working.

---

## 1. `MISSION.md`, and mostly its out-of-scope list

Half an hour, and the half hour that decides the most.

The in-scope list is easy and you will get it roughly right first time. The
out-of-scope list is the one that does work:

> It is how an agent recognises that a plausible, well-argued, easy-to-implement
> request is drift rather than a good idea.

Aim for at least five, and make them things a reasonable person would genuinely
ask for. "No payments" only earns its keep if somebody would otherwise ask for
payments.

**Sort your non-goals into three piles.** A spec's non-goals are always a mix, and
a human reading them just knows which is which. An agent does not.

- **Never.** Goes in `MISSION.md`.
- **Not yet.** Goes in the backlog and must not appear in `MISSION.md`. Anything
  listed out of scope is refused forever, including the quarter it becomes the
  roadmap.
- **Never, and it is a property rather than a feature.** That is an invariant and
  gets its own section.

Then write the section people forget: what the factory does not own. Whether it
feels right, looks right, reads right. A green gate never means the product is
good. It means the layer a machine can check is intact.

---

## 2. `harness/END-TO-END.md`

Two to five journeys, in plain English, each one the whole path a person takes to
get something they wanted.

An agent reads this file on every validation run, drives your app, and reports what
it saw. There is no script to keep in sync, which is the point: a scripted
end-to-end runs the same two requests forever and goes stale the week after it is
written, still passing.

**Assert what a person would notice and complain about.** `200 OK` is not evidence
the page said the right thing. Name the number:

> Ana is owed exactly 666. She paid 1000 and her own share was 334.

**Use values that appear nowhere else in the repo.** A string the builder can grep
is a string it can special-case.

**The reachability constraint is an architecture decision.** The harness reaches
software three ways: `http`, `cli`, `library`. A rendered window, a game loop, a
canvas is none of them. The rules have to live behind a headless surface something
can drive. On a new project that is nearly free to arrange, and a rewrite
afterwards.

---

## 3. `.factory/holdout/HOLDOUT.md`

Everything in `harness/` sits inside the builder's optimisation loop: it can read
those checks, run them, and iterate until they are green. Given enough attempts it
will. That is what you asked for, and it is exactly why passing them proves less
than it feels like it does.

The holdout is different only because the builder never sees it. Verify that, do
not assume it: probe the deny list in both directions and watch a node read the
file without it and fail to read it with it.

Four rules, and the third earns its keep:

1. **Write them before the work.** A scenario written after seeing the
   implementation is a description of the implementation.
2. **Do not reuse a journey.** If it is in `END-TO-END.md`, the builder has read it.
3. **Compose.** The dominant real failure is not cheating, it is feature isolation:
   parts individually correct that never work together. Unit tests test features in
   isolation by definition, so what they measure is precisely the thing that is not
   broken.
4. **Assert exact figures, not properties.** This cost a real escape here: a
   holdout that recorded five expenses and asserted "the balances sum to zero" was
   sailed past by a defect that dropped four of them, because one expense's
   balances sum to zero exactly as five do. Work the numbers out by hand.

---

## 4. The mutation set

Six to ten deliberate defects, injected into real source on a throwaway copy. Each
one must make the gate go red.

Everything else in the gate asks "is this build good?". This asks "would this gate
know if it were not?", and until you have run it you have no evidence any of your
checks can fail at all.

**Aim one at each rung, and read which rung caught each one.** A set built only
from logic defects gets caught entirely by the unit suite, and the journey, holdout
and app-start rungs are never once shown to be able to fail. A perfect score can
mean "the unit suite can fail" and nothing more.

**Aiming is not landing.** Both non-unit defects here landed on the unit suite the
first time. 10/10, four rungs claimed, two of them never demonstrated. A defect
aimed at a rung has to be invisible to every rung above it.

This is also what keeps the agent-driven rungs honest. A model reporting on its own
work is a claim; a planted defect it failed to notice is a measurement.

---

## 4b. The one thing you do not have to build

`factory/_selftest.py` ships done and the doctor runs it every time. It is the
mutation set aimed at the factory rather than at your software: an empty run list
must keep a lock, an unrecognised status must keep a lock, `needs-human` must stay
terminal, an assertion with nothing observed must be rejected.

You do need to know it runs, because the failures it pins do not look like
failures. A dispatcher that mis-decides which laps are alive produces a repository
that looks exactly like a quiet one.

---

## 4c. The protected list, in both places at once

`FACTORY_RULES.md` section 5 says what a builder may never touch, and
`factory/config.py`'s `PROTECTED_EXTRA` is the half the gate can enforce. Fill both in
the same sitting: `factory doctor` fails when they disagree, and blocks level 3.

Name what has a blast radius you cannot absorb -- the auth module, the rate-limit
constant *and* the code that enforces it, payment paths, migrations, Dockerfiles,
`deploy/`, `infra/`. Not everything that looks important: over-protecting makes ordinary
issues auto-reject.

Two things worth knowing before you write it:

**A glob cannot express a region of a file.** "the redirect branch in `server.py`" is
not a rule, it is a wish. Protect the module that owns the decision and write in the
commentary what is deliberately left open. Both of the factories built on this template
shipped a clause like that, and in one of them a planner refused to build an issue until
somebody resolved it.

**Anything you edit goes in `config.py`.** It is the one file `bin/sync-to.py` will not
overwrite. A list added anywhere else in `factory/` is deleted by the next sync, and the
guard keeps printing `PROTECTED_OK` the whole time.

---

## 4d. The fixtures already work

```bash
archon workflow test factory
```

The pack's own dry-run fixtures for each of the six workflows, no model call and
no GitHub call. They execute the real graph with the AI nodes stubbed, so a broken
binding or a `when:` that never fires shows up here instead of eight nodes into a paid
lap. The doctor runs them and blocks level 1 if any fails.

When you edit a workflow, run this before you dispatch anything. When you add a node,
add the stub -- an unstubbed node fails, which is deliberate: it is how a fixture says
"nothing after this point should have run".

---

## 5. The ratchet

Set the floors to what the gate just asserted. Then understand the one failure
mode.

**Slack is the gap between observed and floor, and it is exactly the number of
assertions that could be deleted with the gate still green.** It grows as the
harness improves, because raising the floor is a protected edit the factory cannot
make. Measured on a real factory, a hole went from 7 to 33 in one cycle because the
harness got better.

So slack holds the merge rather than printing a note. A note gets read once, by the
person who already knew.

**A hold is a state, not a sentence.** A held PR gets `factory:held`, which nothing
dispatches and no node may leave. `factory accept <target>` archives the
assumptions and sends it back to `open` for a fresh validation. The first version
posted the explanation as a PR comment and set the PR to `passed`; the dispatcher
merged it forty-five seconds later, because `passed` is what a mergeable PR is
called.

---

## 6. The escalation channel

`FACTORY_NOTIFY_CMD` in `factory/config.py`, defaulting to `.factory/notify.sh`.
Set `FACTORY_NTFY_TOPIC` or `FACTORY_WEBHOOK_URL` and test it once:

```bash
python factory/notify.py --test
```

"I will just check the file" is not an answer. Nobody checks the file. That is the
whole reason this exists.

---

## 7. One lap by hand

```bash
factory run implement gh:issue:1
```

Watch it. Read the plan it wrote, the PR body, the judge's verdict. Do not proceed
on a factory that has never completed a lap.

Then merge that first PR yourself, whatever the gate says. You are not testing the
merge yet, you are reading the work.

---

## 8. The dial, one notch at a time

```bash
factory level 1     # an accepted issue becomes a PR
factory level 2     # + the validator writes a verdict
factory level 3     # + it merges without you reading the diff
```

Each is refused until the doctor says the evidence supports it. Watch one full
cycle at each before the next.

Stopping below 3 is legitimate, for an unmovable review requirement or a blast
radius you cannot absorb. Write into `FACTORY.md` what would have to be true to go
further, so it stays a decision rather than a dial nobody touched again.

---

## 9. The trigger, last

```bash
factory arm
```

Refused below level 1, because a scheduler at level 0 wakes up forever and
correctly does nothing, which is exactly how people convince themselves a factory
is running when it has never completed a lap.

**Check what it installed.** `arm` schedules two jobs: the dispatcher on the
interval, and the weekly regression. The first version installed only the
dispatcher on Windows and reported `ARMED`, so merged code was never re-tested and
nothing reported the job that was never created.

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like "factory-*" }
```
```bash
crontab -l | grep factory
```

---

## 10. Use the stop button once, on purpose

```bash
factory halt "testing the stop button"
factory tick          # refuses to dispatch anything
factory resume
```

Then the remote half, which is the one you reach for from a phone:

```bash
gh issue edit <any open issue> --add-label factory:stop
```

It is one tick behind. GitHub does not guarantee you read your own write
immediately, so the tick right after you add the label can still dispatch. The
local file is instant. That is the trade for a button that works when you are not
at the machine.

A stop button nobody has used is a stop button nobody knows works. Write the date
in `FACTORY.md`.

---

## Then: deployment

Until `FACTORY_DEPLOY_CMD` and `FACTORY_HEALTH_CMD` are set, merging is where this
stops, and the loop is not closed until a stranger can see the change. Everything
before that is a PR generator with very good gates.
