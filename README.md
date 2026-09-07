# AI Software Factory

A repo that takes work in as an issue and ships validated code out, with nobody at
the keyboard.

You file an issue. It gets checked against your mission, planned, built, judged by
something that did not write it, and merged. A scheduled run re-tests what already
merged and files its own bugs. Nobody reads the diff.

Some people call this a dark factory, after lights-out manufacturing. Same idea.
It builds software from a PRD, so I call it a software factory.

The automation is the easy half. Being able to trust a merge nobody read is the
hard half, and most of what is in here exists for that.

---

## Set it up

Paste this to your coding agent, in the repo you want the factory in:

```
Help me set up my own AI software factory in this repo using this GitHub repo:
https://github.com/coleam00/ai-software-factory

Read its README and follow the "Instructions for the agent" section.
```

That is the install. It clones this, runs the installer, then walks you through
the three files nobody can write for you.

**You need:** git, Python 3.10+, [`gh`](https://cli.github.com) authenticated (and
`gh auth setup-git`, so git itself can push), a GitHub remote, a coding agent CLI,
[`bun`](https://bun.sh) and [`uv`](https://docs.astral.sh/uv/). Everything else it
installs, including the workflow engine, which is built from source with bun and runs
its script nodes with uv.

On a server with no browser, the coding agent still needs to sign in: for Claude Code,
run `claude setup-token` on a machine that has one and put the token on the server as
`CLAUDE_CODE_OAUTH_TOKEN`.

If you would rather do it by hand:

```bash
git clone https://github.com/coleam00/ai-software-factory ~/ai-software-factory
cd /path/to/your-repo
python ~/ai-software-factory/bin/factory.py init
python factory/doctor.py
```

---

## Instructions for the agent

> [!IMPORTANT]
> This section is addressed to the coding agent, not to you. If you are reading it
> yourself, the manual commands above do the same thing.

**1. Install it.**

```bash
git clone https://github.com/coleam00/ai-software-factory ~/ai-software-factory
python ~/ai-software-factory/bin/factory.py init
```

Run `init` from the root of the user's repo. It writes the runtime, the workflow
pack, the skills and the governance templates, creates the GitHub labels that are
the state machine, and installs [Archon](https://github.com/coleam00/archon) if it
is not already there. It changes no application code.

**2. Write the three files with them.** This is the actual work, and `init`
installs a skill for it: read `.claude/skills/factory-setup/SKILL.md` and follow it.

Read the repo FIRST. The language, the test command, the start command, the entry
point and the routes are all in there. Then ask at most four questions, each with
your proposed answer already filled in so the cheapest reply is "yes". Every
question the repo already answered is a reason to regret starting.

What comes out of it:

| file | what you are writing |
|---|---|
| `MISSION.md` | what this product is, and the list of things it must **never** become |
| `harness/END-TO-END.md` | two to five journeys, in plain English, that a real user takes |
| `.factory/holdout/HOLDOUT.md` | the same product composed, where the builder cannot read it |

**The out-of-scope list in `MISSION.md` is the one that decides whether any of this
works.** It is how the factory recognises that a plausible, well-argued, easy
request is drift rather than a good idea. Do not ask the user to produce it from
nothing. Propose seven entries yourself from what you read, make them things a
reasonable person would actually ask for, and have them strike the wrong ones.

Write the holdout yourself and do not ask at all. Then tell them it exists and that
they should read it, because it is the file the whole auto-merge rests on.

Journeys describe what the product **does today**, never what it should do. A
journey for behaviour that does not exist yet leaves the gate red before the first
lap, and nothing can merge, including the change that would make it pass.

**3. Run `python factory/doctor.py` and show what failed.** It will fail. That is
it working: the doctor is a checklist and its failures are the remaining todo
list, each naming the autonomy level it blocks. Do not try to make it green in one
sitting.

**4. Stop there.** Do not raise the autonomy dial, do not run `factory arm`, and do
not start the loop. Those come after a lap has run and the user has watched it.

---

## The three files that are yours

Everything else ships done. These three are the build.

**`MISSION.md`** is what the product is, and what it must never become. The
out-of-scope list is the part that does work: it is how an agent recognises that a
plausible, well-argued, easy request is drift. Without it every request is arguably
in scope, because almost every feature is defensible on its own. Aim for at least
five, and make them things a reasonable person would actually ask for.

**`harness/END-TO-END.md`** is two to five journeys in plain English. An agent
reads them every validation run, drives your app, and reports what it saw. Name
the value you expect. "The page loads" passes against an app that returns an empty
body forever.

**`.factory/holdout/HOLDOUT.md`** is the same product, composed, in a directory the
builder is blocked from reading. Everything in `harness/` sits inside the builder's
optimisation loop: it can read those checks and iterate until they are green, and
given enough attempts it will. The holdout is different only because the builder
never sees it, and that is the only honest reason to merge code nobody reviewed.

The journeys and the scenarios are markdown, not scripts, on purpose. A scripted
end-to-end runs the same two requests forever and goes stale the week after it is
written, and the staleness is invisible because it still passes.

---

## The autonomy dial

```
0  workflows exist, you run them by hand           <- every install starts here
1  an accepted issue becomes a branch and a PR
2  + the validator runs and writes a verdict
3  + it MERGES when every structural gate is green <- the target
4  + it triages its own issues, and the scheduled regression files its own bugs
5  + it writes its own issues from the mission
```

**Level 3 is the destination.** It is the first level where code merges without a
human reading it. A factory that stops at 2 is a code generator with a queue, and
you are still the bottleneck you were trying to remove.

`factory level 3` refuses until the doctor says the evidence supports it: real
journeys, a holdout, a mutation set shown to catch things, a ratchet with numbers
in it, and a channel that can reach you.

---

## What is enforced in code, not in a prompt

A gate written as an instruction in a prompt is a suggestion with good manners.
These are not.

- **The merge.** A script reads a verdict file and branches on it. Never a model
  deciding to merge.
- **Proof it ran.** `APP_STARTED` and `E2E_PASSED` must appear in the output. A
  check that never ran produces no failures, and "did anything fail?" reads that as
  success.
- **Evidence, not a claim.** Every assertion the journey agent reports carries the
  value it actually observed. A report that restates the expectation instead of
  what happened is rejected before anything is counted.
- **The protected list.** A PR touching governance, the harness, the locks or the
  holdout is auto-rejected first, and the validator reads the rulebook from the
  **base branch**, so a PR cannot weaken the rules it is about to be judged by.
- **The ratchet.** Assertion counts have a floor in a protected file, so "delete
  the check and lower the number" is not available to the factory.
- **The stop button.** A local file and a remote label, because they fail in
  different places. The remote half fails closed: any error reading it counts as
  stopped.
- **The watchdog.** A tick has no memory, so it cannot notice it is repeating
  itself. A ledger records every dispatch and seven detectors halt the factory on
  the shapes of stuck. One rejected PR re-validated 68 times in three and a half
  hours before this existed.

---

## Where the AI steps come from

The factory writes almost none of them. Planning, building, reviewing and
root-causing are Archon's `sdlc` pack -- the same workflows Archon itself develops
with -- composed in by `include:` and shipped inside the engine binary, so there is
nothing to install and nothing to keep in sync.

| Step | What runs | Why not ours |
|---|---|---|
| plan | `archon-plan` | Grounds itself against the repo, writes `plan.md`, returns `ready`, and refuses to mutate the checkout while planning. It replaced a prompt *and* the `prime` node that fed it. |
| implement | `archon-implement` | Loops until done (max 5), returns `{done, green, red_cause}`, and ends in a deterministic guard that fails a node which declined its task. Our one-shot had none of the three. |
| fix | `archon-implement` | Addressing findings *is* implementing. A second, weaker prompt meant the correction path had no loop and no green verdict while the build path had both. |
| review | `archon-review` | Six parallel lenses -- code, seams, simplify, tests, errors, docs -- each finding carrying the lens that raised it. On one real PR the tests lens copied the app to a scratch directory, reordered the change, and proved a new test could not fail. |
| root cause | `archon-investigate` | Establishes a *proven* causal chain rather than the first plausible explanation, and the engine fails it if it edits the repository while investigating. |

Four prompts were deleted outright when these landed. What the factory still owns is
the part the pack has no opinion about: the state machine, the guard, the gate, the
merge, the holdout, the ratchet, and the scripts between the AI steps.

The pack ships ten workflows; these four cover every AI step here.
`docs/derivation.md` accounts for the other six. Four are refused because the pack
keeps a human at the pull request, which is the one step this factory does not have.
One, `archon-upkeep`, is not a refusal but a gap: nothing here updates a dependency.

> [!IMPORTANT]
> **This needs an Archon that carries one unreleased primitive.** `include:` must be able
> to carry `denied_tools`, or the holdout wall does not survive composition -- and it
> fails silently, with every check still green. No released Archon has it yet; it is
> branch `feat/include-tool-policy`. `factory doctor` asks the engine directly and
> refuses to leave level 0 without it, so you cannot run into this by accident, but you
> do have to build Archon from that branch to use this as intended.

**The composition is only safe because of the deny list.** Every node the pack expands
into grants `Read`, and none of them knows this factory has a holdout. `include:`
unions `denied_tools` onto every expanded node, so the wall survives a block somebody
else wrote -- 16 AI nodes across the five workflows, all walled or tool-sealed. That
primitive did not exist; it was contributed upstream for this. `factory doctor`
therefore asks the *engine* whether it kept the field rather than trusting a version
string, and blocks the dial when it did not.

**Two commands, no model calls:**

```bash
archon workflow test factory     # 11 dry-run fixtures, the real DAG, ~2 seconds
python factory/doctor.py         # runs them, and 30 other checks
```

A fixture executes the actual graph with the AI nodes stubbed -- `when:` conditions,
trigger rules, `if_skipped` defaults, cancel nodes, and the namespaced nodes an
`include:` expands into. That is the layer where composing somebody else's workflow
goes wrong, and none of it is visible by reading the YAML.

---

## Commands

```bash
factory init          # install into this repo
factory doctor        # the checklist. It will fail. That is it working.
factory status        # what is in flight, what the dial is, what needs you
factory run implement gh:issue:4   # one lap, by hand, watching
factory level 1       # raise the dial (refused without the evidence)
factory arm           # install the schedule (refused below level 1)
factory halt          # the stop button
```

And the three things `init` installs that actually run it:

```bash
bash .factory/loop.sh        # the dispatcher. One tick a minute, forever.
python .factory/monitor.py   # prints only what you would act on
.factory/notify.sh           # where escalations go. Set one of these first:
                             #   FACTORY_NTFY_TOPIC, FACTORY_WEBHOOK_URL
```

Set a notification channel before you leave it running. The watchdog can halt the
factory on its own. It cannot tell you that it did.

---

## What it does not do

**It does not push.** Filing an issue does not trigger a run. A scheduler wakes on
a timer, reads the state, and dispatches. An issue filed at 09:01 waits for the
next tick. A push trigger that breaks fails silently and looks exactly like a
factory with nothing to do. A poll that breaks is a poll you can see not running.

**It does not judge taste.** A green gate never means the product is good. It means
the layer a machine can check is intact.

**It does not own your process.** The node prompts in `.archon/workflows/factory/`
are yours to rewrite. That is where your planning step and your review step go.

---

## Cost

One published comparison, on one task: a solo agent produced a non-functional
result in about twenty minutes for single-digit dollars. A planner, generator and
evaluator harness where the evaluator drove the live page produced a working result
in about six hours for roughly twenty times the cost.

Twenty times, for the only version that worked. Instrument your tokens on day one.
Projections for this are wrong by 10-20x in the same direction every time.

---

## Layout

```
bin/factory.py       the CLI
bin/sync-to.py       push template fixes into a repo that already installed
bin/audit.py         cross-file invariants no single file can check alone
template/            what init copies in
  factory/           the runtime: dispatcher, state machine, guard, gate, merge
  factory/_selftest.py  the harness for that runtime, run by doctor
  harness/           the gate ladder, the mutation runner, END-TO-END.md
  .archon/workflows/ the five workflows, their prompts, and their dry-run fixtures
  .claude/skills/    the same loop, by hand
docs/derivation.md   which AI steps come from Archon's sdlc pack, and why the rest do not
docs/first-hour.md   what to do after init, in order
docs/incidents.md    every way this has been wrong, and the mechanism each time
```
