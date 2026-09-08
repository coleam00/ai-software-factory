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

If the user's repo lives on a server, run every command there over `ssh` from where
you are; nothing here needs a terminal on the box (`init` asks no questions when it
has no tty, and installs the engine).

Run `init` from the root of the user's repo. It writes the runtime, the skills
and the governance templates, creates the GitHub labels that are
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
4  + it triages its own issues, and the scheduled regression may file its own bugs
```

Level 4's publication is deliberately narrow. The fixed gate reports whether the base
branch is green and cannot say *why* it is not without inventing prose, which is how
private evaluator output reaches a public issue -- so on its own a red weekly run
escalates to a person and files nothing. Setting `FACTORY_PUBLIC_PROBE_SCOPE` is your
statement that the checks it names, and everything they print, are public developer
material; the workflow then re-runs that scope after the private gate has already come
back red and may write up what the probe itself proves. A public probe can never make a
failed private gate green.

There is no level 5. It used to read "it writes its own issues from the mission",
which nothing here has ever implemented, and a dial that names a level the code cannot
reach is a promise the product does not keep.

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

The factory writes none of them. Judging an issue, taking it to a pull request,
accepting that pull request, repairing it, re-testing what merged and performing the
merge are all Archon's `sdlc` pack -- the same workflows Archon itself develops with,
bundled with the engine, so there is no separate workflow copy to install or keep in
sync.

| Factory action | What runs | What the factory hands it |
|---|---|---|
| triage | `archon-admit` | `MISSION.md` and `FACTORY_RULES.md`, read out of the base tree as trusted policy. Never the issue body: an issue that can supply the rules it is judged against is an issue that admits itself. |
| implement | `archon-ship` | the issue, its recorded work order, and a publication policy naming one fixed command. `archon-ship` decides for itself whether the item needs investigating, planning or neither, and ends in a reviewed pull request. |
| validate | `archon-accept` | the ORIGINAL request text kept outside every checkout, and a strict profile pinning `factory/fixed_gate.py`, declaring what that gate covers, carrying `MISSION.md` and `FACTORY_RULES.md` from the base commit, and requiring a sanitized report back from the gate itself. It fetches the exact head and base into a repository of its own and returns a receipt. |
| fix | `archon-revise-pr` | the same work order, and the findings out of that receipt. A fresh clone, a new worktree, and no artifacts from the run that built it. |
| regress | `archon-regress` | the same fixed command as a private check profile, permission to publish only at level 4, and `FACTORY_PUBLIC_PROBE_SCOPE` -- empty by default -- naming what may be re-run in public when that private gate comes back red. |
| merge | `archon-merge` | an authorization file rewritten immediately before every read of it, naming the base, the required checks, the hold labels and the stop file. |

Five workflows and four judgment prompts were deleted outright when these landed.
What the factory still owns is the part the pack has no opinion about: the state
machine, the dial, the caps, the guard, the ratchet, the holdout, the escalation
channel -- and the one fixed command all of the above run.

**The gate is one command, and the pack does not get to choose it.**
`factory/fixed_gate.py` runs the guard, the secret preflight, the tripwire and your
`VALIDATE_CMD`, then reads the log for the markers, the ratchet floors and the mutation
score. Acceptance runs it, publication runs it, and the weekly regression runs it. Three
copies of a gate are three gates that drift, and the one nobody runs is always the one
that is wrong.

**And it is rebuilt from the base branch every run.** `config.py`, `guard.py`,
`gate.py`, `tripwire.py` and the ratchet floor are read out of the base revision the
pull request targets -- not the candidate, not your working copy -- and a base tree
missing any of them refuses to validate. A pull request cannot supply the judge that
judges it, and that is now structural rather than a rule somebody has to follow.

> [!IMPORTANT]
> **This needs an Archon carrying the SDLC pack.** `factory init` asks the engine for
> the six workflows by name rather than trusting a version string, and refuses rather
> than installing a factory whose every dispatch would fail at the first tick. When no
> engine is on PATH it builds one from the pinned revision in `bin/factory.py`; when the
> engine there cannot run these workflows it tells you to update it or to point
> `FACTORY_ARCHON_BIN` somewhere else, rather than linking a second one behind it.

> [!WARNING]
> **The holdout can be read by the thing that writes the code.** `.factory/holdout/**`
> is on the protected list, so nothing can edit it. Reading is a tool policy, and
> nothing lets a caller set one inside a workflow it did not write -- so a builder can
> open the scenarios it will be judged on, with every check still green. `factory
> doctor` blocks level 3 until `FACTORY_HOLDOUT_DENY` records how you arranged that
> barrier elsewhere: an agent deny list, a provider policy, a checkout the builder does
> not get. It records your claim. It cannot verify one, and on a provider that cannot
> enforce a tool restriction at all there is nothing to verify.

**Two commands, no model calls:**

```bash
python factory/_selftest.py      # the machinery's own invariants, offline, ~2 seconds
python factory/doctor.py         # runs them, asks the engine what it has, and 30 more
```

The doctor also runs the pack's own dry-run fixtures for each of the six workflows. A
fixture executes the actual graph with the AI nodes stubbed -- `when:` conditions,
trigger rules, `if_skipped` defaults, cancel nodes, and the namespaced nodes an
`include:` expands into. That is the layer where an upstream change breaks a consumer,
and none of it is visible by reading the YAML.

### What acceptance is handed

`archon-accept` runs one fixed command -- `factory/fixed_gate.py`, rebuilt from the base
branch -- and keeps its argv and its output streams private, which is the only reason
that command may exercise a holdout the builder must not read. The judge would otherwise
have an exit code and nothing else, so the profile the factory writes also carries:

- a **gate declaration**, stating that this one command is the whole applicable gate and
  describing in public terms what it covers;
- **`MISSION.md` and `FACTORY_RULES.md` read from the base commit**, because the judge
  runs with no tools and cannot follow a pointer from one document into another;
- **one required piece of evidence**: `.factory/acceptance-report.json`, which the gate
  writes into the candidate checkout bound to that evaluation's id and identity. It
  carries the gate's status, which required markers reported and the counts measured --
  never a raw failure, a holdout scenario, an evaluator path or the command itself.
  Acceptance refuses the file if the path was tracked or already present, so it can only
  ever be output that evaluation produced.

A declaration never establishes a requirement and never overrides a deterministic
failure. It lets a judge tell an authorized whole gate from an arbitrary command that
exited zero, which without it is a fair reason to answer `inconclusive` forever.

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

## Running it on a server

A factory that only runs while your laptop is open is a demo. It wants a Linux box
that never sleeps: root, cron, a firewall you control, and a way to reach it. Any
provider. The steps are the same everywhere; only who you ask differs.

**1. The box.** The smallest plan with 8 GB of RAM is plenty; the expensive part is the
model, not the server. Most hosts now have an MCP server, a CLI, or an API your coding
agent can drive, so the provisioning, the SSH key, the firewall and the snapshot can all
be one conversation. Make a key on your laptop first:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/factory -N ""
```

then ask the agent to install the public half, open only 22/80/443, and snapshot.

**2. The toolchain.** One script, idempotent, Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/coleam00/ai-software-factory/main/bin/bootstrap-ubuntu.sh | bash
```

**3. The two logins only a person can do.** Both need a browser once, on your laptop.

```bash
gh auth login --hostname github.com --git-protocol https --web   # device code
gh auth setup-git                                                 # so git itself can push
claude setup-token          # on your LAPTOP; then on the box:
echo 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' >> ~/.bashrc && source ~/.bashrc
```

**4. Install and arm.** `factory init` in your repo, the three files, `factory doctor`
until it is green, then `factory arm` writes the crontab entry. It snapshots the shell's
environment into `.factory/cron.env` (mode 600, gitignored) because cron has no PATH and
no login of its own. Set `FACTORY_NTFY_TOPIC` or `FACTORY_WEBHOOK_URL` before you arm.

**5. Close the loop.** Three lines in `factory/config.py`: `DEPLOY_CMD` (for a Python
app behind Caddy: `git pull --ff-only origin main && systemctl restart <app>`),
`HEALTH_CMD` (`curl -fsS https://<domain>/health`) and `HEALTH_MARKERS` (`ok`). The
dispatcher polls the deploy every tick and only moves the pointer when the health check
answers, so a merge reaches a user within one interval or is reported.

---

## What it does not do

**It does not push.** Filing an issue does not trigger a run. A scheduler wakes on
a timer, reads the state, and dispatches. An issue filed at 09:01 waits for the
next tick. A push trigger that breaks fails silently and looks exactly like a
factory with nothing to do. A poll that breaks is a poll you can see not running.

**It does not judge taste.** A green gate never means the product is good. It means
the layer a machine can check is intact.

**It does not own your process.** The workflows are Archon's and a project-scope
workflow of the same name overrides the bundled one, so a file in your own
`.archon/workflows/` replaces a step wholesale with nothing here to edit.

That includes the one that judges. The factory dispatches `archon-accept` by name and
does not pin where the definition comes from, so a workflow of that name in your repo
is what evaluates your candidates. Your workflow definitions and your engine install
are trusted here, deliberately -- this is your factory and customizing it is the point
-- and `factory doctor` names any of the six it finds defined locally rather than
letting the substitution be invisible. What a *candidate* cannot do is supply its own
judge: the gate acceptance runs is rebuilt from the base branch, and the guard refuses
any pull request that touches `factory/` or `.factory/locks/`.

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
bin/_test_install.py a fresh install, and an existing one brought forward
template/            what init copies in
  factory/           the runtime: dispatcher, state machine, guard, gate, merge
  factory/sdlc.py    dispatches the SDLC pack and applies what it returns
  factory/fixed_gate.py  the one command acceptance, publication and regress all run
  factory/_selftest.py  the harness for that runtime, run by doctor
  harness/           the gate ladder, the mutation runner, END-TO-END.md
  .claude/skills/    the same loop, by hand
docs/derivation.md   what the sdlc pack owns, what this factory still owns, and what the move cost
docs/first-hour.md   what to do after init, in order
docs/incidents.md    every way this has been wrong, and the mechanism each time
```
