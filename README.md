# AI Software Factory

A repo that takes work in as an issue and ships validated code out, with nobody at
the keyboard.

You file an issue. It gets checked against your mission, planned, built, judged by
something that did not write it, and merged. A scheduled regression can re-test what
already merged and publish bugs it finds.

Some people call this a dark factory, after lights-out manufacturing. Same idea.
It builds software from a PRD, so I call it a software factory.

The automation is the easy half. Being able to trust a merge nobody read is the
hard half, and most of what is in here exists for that.

All AI work runs through [Archon's SDLC pack](https://github.com/coleam00/Archon).
This repo supplies the setup, scheduling, project context and ordinary app resources.
Archon workflows handle every agent, from triage through verification and merge.

---

## Set it up

Paste this to your coding agent, in the repo you want the factory in:

```text
Help me set up my own AI software factory in this repo using this GitHub repo:
https://github.com/coleam00/ai-software-factory

Read its README and follow the "Instructions for the agent" section.
Run the installer for me, then help me write the mission and validation journeys
based on this project. Configure Archon with my chosen coding agent and model tiers.
Every AI step must use the shared Archon SDLC workflows.
Leave scheduling off until we have watched one issue go through the factory.
```

That is the install. It clones this, runs the installer, then walks you through
the three files nobody can write for you.

**You need:** git, Python 3.10+, [`gh`](https://cli.github.com) authenticated (and
`gh auth setup-git`, so git itself can push), a GitHub remote, a coding agent,
[`bun`](https://bun.sh) and [`uv`](https://docs.astral.sh/uv/).
Your agent can help install the missing tools. The installer brings its own pinned
Archon source, including the shared workflows; you do not need to check out Archon
or merge its PRs yourself.

**Current status:** the default uses the workflow additions on Archon's
[`cleanup/sdlc-workflows-only`](https://github.com/coleam00/Archon/tree/cleanup/sdlc-workflows-only)
branch. They are not merged upstream yet, and this integration still needs a live
end-to-end run.

---

## Instructions for the agent

> This section is addressed to the coding agent. Run the setup for the user.

**1. Install it.**

Read the application repo first. Check its GitHub remote, existing configuration,
toolchain and authentication. Reuse working logins and ask the user to complete any
interactive sign-in that is missing. Do not print credentials.

Clone the factory outside the application repo, or update an existing clean clone
with `git pull --ff-only`:

```bash
git clone https://github.com/coleam00/ai-software-factory ~/ai-software-factory
```

Then, from the root of the **application repo**:

```bash
python ~/ai-software-factory/bin/factory.py init
```

Use `python3` if that is the Python command on the machine. If the user's repo lives
on a server, run the installation and configuration there over SSH.

The installer writes the factory and project templates and installs the pinned
Archon source. It preserves project configuration on upgrades. It does not configure
provider authentication, change model tiers or start a schedule.

**2. Configure Archon.**

Read the installed source's configuration documentation and configure the user's
chosen provider through native Archon settings. Preserve existing working settings.
For Claude Code, Sonnet for small/medium and Opus for large is a useful starting
point; use model identifiers supported by the installed provider and the user's account.

All factory AI work must run through the shared SDLC workflows. Do not add direct
coding-agent subprocesses to factory scripts or create factory-local workflow copies.

**3. Write the three files with them.**

Read the repo FIRST. The language, the test command, the start command, the entry
point and the routes are all in there. Then ask at most four questions, each with
your proposed answer already filled in so the cheapest reply is "yes". Every
question the repo already answered is a reason to regret starting.

| File | What you are writing |
|---|---|
| `MISSION.md` | What this product is, and the list of things it must **never** become |
| `harness/END-TO-END.md` | Two to five journeys, in plain English, that a real user takes |
| `.factory/holdout/HOLDOUT.md` | Independent scenarios that combine the product's behavior |

**The out-of-scope list in `MISSION.md` is the one that decides whether any of this
works.** It is how the factory recognises that a plausible, well-argued, easy
request is drift rather than a good idea. Do not ask the user to produce it from
nothing. Propose seven entries yourself from what you read, make them things a
reasonable person would actually ask for, and have them strike the wrong ones.

Draft the holdout yourself, then tell the user to review it. A directory called
"holdout" does not make it private: arrange actual isolation from the builder if
these scenarios are meant to be hidden.

Journeys describe what the product **does today**, never what it should do. A
journey for behaviour that does not exist yet leaves the gate red before the first
lap, and nothing can merge, including the change that would make it pass.

Configure the project's static/unit commands and translate the journeys into the
runtime scenario inputs required by the shared workflows. Follow the installed
`factory/RUNTIME_HOST.md` for ordinary app startup, fresh state and candidate
worktrees. The agent handles this wiring; the user supplies the product intent.

**4. Check the installation and hand it back.**

```bash
python factory/consumer.py doctor
python factory/consumer.py list
```

Show any failures and what remains to configure. Doctor checks the installation
and available workflows; it does not prove a live agent can sign in or complete a run.

Give the user the exact command for one small issue, using `archon-ship` for
issue-to-PR or `archon-lifecycle` for the full verification-and-merge sequence.
For lifecycle, prepare the required scenario/holdout inputs and runtime host first.
Start with merge approval enabled and discovery publication in preview mode.

**5. Stop there.** Leave scheduling off until the user has watched a lap complete.
If they already asked you to run that first lap, continue within that scope.

---

## The three files that are yours

**`MISSION.md`** is what the product is, and what it must never become. The
out-of-scope list is the part that does work: it is how an agent recognises that a
plausible, well-argued, easy request is drift. Without it every request is arguably
in scope, because almost every feature is defensible on its own. Aim for at least
five, and make them things a reasonable person would actually ask for.

**`harness/END-TO-END.md`** is two to five journeys in plain English. Name
the value you expect. "The page loads" passes against an app that returns an empty
body forever. The setup agent turns those journeys into inputs for Archon's runtime
verification workflow, which drives the running app and records what it observes.

**`.factory/holdout/HOLDOUT.md`** checks the same product through different
combinations and edge cases. Checks the builder can read are useful, but they are
not a hidden holdout. Keep private scenarios outside the builder's accessible
checkout and give them only to the verification environment.

---

## Where the AI steps come from

The factory writes none of them. Every AI step is a workflow or command in Archon's
shared SDLC pack. You can run the workflows individually or use the full composition.

| What you want | Shared Archon workflow |
|---|---|
| Check whether an issue is ready and in scope | `archon-triage` |
| Take an issue through planning, implementation and review to a PR | `archon-ship` |
| Implement an existing plan or repair a PR | `archon-deliver` |
| Review code or run project checks | `archon-review`, `archon-validate` |
| Exercise a running app and record evidence | `archon-verify-runtime` |
| Check a baseline and whether verification catches deliberate defects | `archon-verify-runtime-suite` |
| Re-test existing behavior and diagnose regressions | `archon-regress` |
| Recheck discoveries, deduplicate them and optionally publish issues | `archon-discoveries` |
| Check PRs and CI, then merge according to the selected approval mode | `archon-merge-queue` |
| Run shipping, runtime/holdout verification, bounded repair, discoveries and merge | `archon-lifecycle` |

**Ship runs its own triage.** You do not have to run triage first. Deliver can use
an existing run's worktree to repair the same PR. Lifecycle connects the shared
workflows; factory Python does not dispatch their individual stages.

The agents use the GitHub CLI inside these workflows for issue publication, CI
checks and merging. Configure GitHub branch protection for the checks and reviews
your project requires.

The source revision lives in [`pack.json`](template/factory/pack.json). Once the
workflow PRs merge upstream, updating that pin moves new installs to the merged
version. Existing installations update by pulling this repo and rerunning `init`.
Workflow improvements belong in Archon's SDLC pack.

---

## Commands

Run these from the application repo after installation:

```bash
python factory/consumer.py doctor
python factory/consumer.py list
python factory/consumer.py run archon-ship --input target=https://github.com/OWNER/REPO/issues/1 --detach --json
python factory/consumer.py get <run-id> --json --events
python factory/consumer.py status --all --json
python factory/consumer.py approve <run-id> --comment "Approved"
python factory/consumer.py cancel <run-id>
python factory/consumer.py halt
```

`halt` blocks new launches and continuations. Cancel an active run explicitly;
`unhalt` allows launches again. Runtime-host runs stay in the foreground.

After the first successful lap, ask your agent to configure
`.factory/schedule.json` with a shared workflow and its inputs.
`python factory/consumer.py tick` submits one whole workflow.
`bash .factory/loop.sh` repeats it, every five minutes by default.

A fixed issue target repeats that same target; the scheduler does not select the
next issue from a backlog. Any AI intake or selection must also live in a shared
Archon workflow.

---

## Running it on a server

A factory that only runs while your laptop is open is a demo. It wants a Linux box
that never sleeps. Any provider. Use the same setup prompt and tell your agent
which server and application repo to use.

For Ubuntu, the agent can inspect and run [`bin/bootstrap-ubuntu.sh`](bin/bootstrap-ubuntu.sh)
to install the toolchain, then help with GitHub and provider sign-in. Run the
installer in the application repo on that server.

Watch one issue become a PR, verify the running app, and approve its merge before
starting a persistent loop or OS timer. The service needs the same tool paths and
authentication as the successful manual run. Installation does not start it for you.

Deployment is project-specific and is not automatically configured by a merge.
Have the setup agent wire the application's deployment separately.

---

## What it does not do

**It does not judge taste.** A green gate never means the product is good. It means
the layer a machine can check is intact.

**It does not invent a backlog.** A schedule runs the workflow you configured with
the inputs you supplied. Discovery can publish verified findings; that is different
from deciding what the product should become.

**It does not maintain a second set of AI workflows.** Factory runs the pinned
shared SDLC source. Changes to agent behavior belong there.

---

## Cost

Instrument your tokens on day one. Start with one small issue and watch the model
calls before enabling a loop. Use medium models for routine work and large models
where the workflow needs the extra reasoning.

---

## Layout

```text
bin/factory.py                install and CLI entry point
template/                    what init copies into your repo
  factory/consumer.py        invokes shared Archon workflows and shows their state
  factory/pack.json          shared source revision and required workflows
  factory/RUNTIME_HOST.md    app startup and runtime scenario configuration
  harness/                   project checks and END-TO-END.md
docs/first-hour.md            what to do after setup
docs/incidents.md             historical failures and lessons
```

Upgrading an older factory? See the [migration guide](template/factory/MIGRATION.md)
for retired commands and configuration.
