---
name: set-up-factory
description: Set up the AI Software Factory on this machine for a new project, from nothing to a factory that can take an issue all the way to a merged pull request. Use when someone in this repository says "set up the factory for me", "install the factory", "get me started", or asks how to start using the factory.
---

# Set up the factory

You are setting up the AI Software Factory for someone, usually for the first time. They
cloned this repository and opened you inside it. Your job: get them to a factory that
passes `factory-ready`, which means it can take an issue to a merged PR. Setup having
"run" is not the finish line.

Talk plainly. Before anything that leaves this machine or keeps running on it (creating a
GitHub repo, pushing, installing timers), say what you're about to do and get a yes.

## 1. Check the machine

Check, and install or fix only what's missing:

- **Git** and the **GitHub CLI**, signed in: `gh auth status`. If not signed in, ask them
  to run `gh auth login` themselves (it's interactive).
- **Bun 1.4.2**: `bun --version`. To install, on Windows:
  `powershell -c "iex \"& {$(irm https://bun.sh/install.ps1)} -Version 1.4.2\""`;
  on macOS/Linux: `curl -fsSL https://bun.sh/install | bash -s "bun-v1.4.2"`.
- **Archon** at the tested engine: `archon version`. Until the fixes are in an Archon
  release, install it from source (next to this repo, not inside the project):

  ```bash
  git clone -b v1/bun13-markdown-fallback https://github.com/coleam00/Archon.git ../archon-engine
  cd ../archon-engine && git checkout f47aa2bbf && bun install && cd packages/cli && bun link
  ```

  Archon's quick installer does NOT have these fixes yet; don't use it for the factory.
- **A provider**: `archon doctor` must show the provider they use as usable. If no default
  is set, set `defaultAssistant` in `~/.archon/config.yaml` to the coding agent they
  already use (`claude`, `codex` or `pi`). A failed line for a provider they don't use is
  fine; say so, so it doesn't worry them.
- **agent-browser** only if they want app checks later (`npm i -g agent-browser`). Skip it
  for now otherwise.

## 2. Ask for three things

1. The GitHub repo for their app: `OWNER/NAME`, new or existing, private by default.
2. Their app idea in a few sentences. You'll turn it into `PRD.md` with them, or use the
   PRD they already wrote.
3. How much it should ask them: `supervised` (every merge waits for them), `standard`
   (the default), or `autonomous`.

Everything else has a sensible default. Don't ask about models, timers or app commands
unless they bring it up.

## 3. Create the project

Make the project folder next to this repository (not inside it), then:

```bash
gh repo create OWNER/NAME --private --clone     # or clone their existing repo
cp -r <this repo>/.archon <project>/            # the factory layer
cd <project> && archon skill add .              # Archon's own skill, required
```

Write `PRD.md` from their idea if they don't have one.

## 4. Configure

Edit `<project>/.archon/factory.yaml`:

- `project.repo_path: OWNER/NAME`
- `maintainers: [their GitHub login]` (from `gh api user --jq .login`). Without a
  maintainer nothing is trusted and nothing ever merges.
- `autonomy:` their answer.
- `triggers.enabled: true`, `triggers.host_id:` a short name for this machine, and
  `triggers.run_as_user_id:` the `runAsUserId` printed by `archon trigger whoami`.
- Leave `app_check.enabled: false` until the app exists and has a start command.

Then apply it and look at what changed:

```bash
archon workflow run factory-setup --detach     # then: archon workflow wait <run-id>
git status
```

Setup writes `.archon/sdlc/policy/maintainers.json`, `.github/workflows/factory-ci.yml`
(only if the repo has no CI yet), the policy blocks, the timer bindings in
`.archon/triggers/` (ignored by git), and `.gitignore`. It never installs a timer.

## 5. Push and start the timers

After a yes:

```bash
git add -A && git commit -m "Add the AI Software Factory" && git push -u origin main
bun .archon/scripts/factory-schedule.ts install
```

The timers run the queues and a small intake poll that hands new GitHub issues to Archon
(no webhook needed). Tell them: the factory works while this machine is on and awake;
`bun .archon/scripts/factory-schedule.ts remove` stops it.

## 6. Prove it's ready

```bash
bun .archon/scripts/factory-ready.ts
```

Every line must say ok. CI takes a minute after the push, so "pending" means wait and run
it again. Fix anything else it reports (each line says how), then run it again. Only
call setup done when it says `"ready": true`.

## 7. First work

Offer to slice the PRD into tickets:

```bash
archon workflow run factory-backlog --input repo_path=OWNER/NAME --input prd_path=PRD.md --detach
```

The tickets land as GitHub issues, the intake poll picks them up within a few minutes,
and the build queue starts on the first one. Point them at `archon workflow attention`
(or the Archon web UI) for questions the factory asks them.

Day-to-day operation (queues, attention answers, stopping runs) is the `archon-cli`
skill's job, not this one.
