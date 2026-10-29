# AI Software Factory v1

This repository is a small project-scoped factory layer for Archon's current SDLC
pack. Archon supplies the engine, durable state documents, attention queue, aliases,
native triggers, and the intake/build/merge/discoveries queues. The `.archon` package
here adds project configuration, black-box app checks, private scenario writing,
backlog slicing, a policy-bound manager, and explicit deployment.

Rasmus deserves credit for Archon's engine and SDLC architecture. This repository is
a consumer layer on that architecture, not a fork or replacement for it. It does not
override bundled SDLC workflows or modify the engine.

## Status and prerequisite

This is an early v1 package for review and soak testing. It is not production-ready,
and it makes no claim that ten unattended laps have passed. That soak is still in
progress.

Use an Archon checkout containing baseline commit `395b8e96b` plus these compatibility
fixes:

- `0bdc3a888`: SDLC PR description matching on Bun before 1.4
- `6a09dd0ad`: empty bunfig handling for the Windows job broker
- `aceae89bd`: stream inline Bun scripts over stdin
- `e3f8fe39c`: refactor the ratchet checks
- `f47aa2bbf`: bound the Codex pipe drain

The current tested engine tip is `f47aa2bbf`. Confirm the baseline with:

```bash
git -C /path/to/Archon merge-base --is-ancestor 395b8e96b f47aa2bbf
```

Bun 1.4.2 has passed an actual native-trigger detached-Git smoke test and 49 SDLC
fixtures. A historical detached-Git issue on Windows was not reproduced in that smoke
test, but this does not disprove the earlier report.

Archon, Bun, Git, and authenticated `gh` are prerequisites. Configure a supported
Archon provider and its credentials through Archon. Factory workflows inherit the
operator's selected provider and tier defaults unless `factory.yaml` explicitly sets
an assistant or provider-qualified tier/alias profile.
When app checks are enabled, the scheduled environment must also provide the
`agent-browser` command used by the sourced black-box check; setup does not install it.

## Packaging limitation

Packaging is currently a project copy. Copy this repository's `.archon` directory into
the target project and review the diff. There is no installer, registry package, live
factory update, or production deployment in this branch. Updating a project means
reconciling a newer `.archon` copy with that project's configuration and policy prose.

Only `workflows/factory/*` belongs to this layer. Leave Archon's bundled SDLC pack in
the engine checkout; do not copy or override it in a project.

## Configure and apply

Use the `factory-setup` skill together with Archon's own `archon-cli` skill. The former
covers factory settings; the latter remains authoritative for queue operation,
attention, and trigger commands.

1. Edit `.archon/factory.yaml` in the target project.
2. Leave `triggers.enabled: false` until manual queue runs are understood.
3. Install the matching engine's `archon-cli` skill into the target project, then run
   setup. Do this before setup or any queue operation:

   ```bash
   archon skill add .
   archon workflow run factory-setup --detach
   ```

4. Read the completed run and review the repository diff:

   ```bash
   archon workflow attention --json
   archon workflow get <run-id> --json
   git diff -- .archon
   ```

Setup validates malformed and unknown settings. It writes only an explicitly configured
`models.assistant` and provider-qualified tier/alias profiles into the supported
`.archon/config.yaml` fields. Existing assistant selection and unrelated profiles are
preserved. It maps `supervised`, `standard`, or `autonomous` into marked blocks in the
project triage/build/merge policy files. Text outside those blocks is preserved. The
mapping never expands product scope: agents judge policy prose, while the setup script
only selects the preset's fixed lines.

The app configuration is deliberately generic:

- `app.prepare`: optional exact preparation command
- `app.start`: required when app checks are enabled
- `app.port_env`: environment variable receiving a free port
- `app.database_env`: optional variable receiving a fresh database path
- `app.health_path`: HTTP health path

No start command, port, database variable, holdout path, provider, model ID, credential,
cloud, or deployment destination is assumed. Add project paths to `protected_paths`.
Leave `models.assistant` empty to inherit Archon. Every configured tier or alias must
name both its Archon provider and model explicitly.

## Native queues and triggers

Archon's bundled queues remain the core factory:

| Purpose | Workflow |
| --- | --- |
| Intake and triage issue events | `archon-intake` |
| Build a triaged item to a reviewed PR | `archon-build-queue` |
| Merge policy-approved, fresh reviewed heads | `archon-merge-queue` |
| Drain verified discoveries | `archon-discoveries` |
| Slice a PRD into deduplicated issues | `factory-backlog` |
| Check the oldest eligible PR through the app | `factory-app-check` |
| Tend only policy-settled attention waits | `factory-manager` |
| Write external private scenarios | `factory-holdouts` |
| Deploy one exact documented revision | `factory-deploy` |

Run a first queue pass manually using the inputs shown by
`archon workflow list <name> --full`; do not rely on remembered flags. A typical build
queue launch is:

```bash
archon workflow run archon-build-queue \
  --input repo_host=github.com --input repo_path=OWNER/REPO --detach
```

Intake is event-driven and needs the GitHub event binding documented by the matching
Archon SDLC pack. This package does not synthesize that binding because a timer cannot
supply the issue event fields.

For timer queues, get the execution identity with `archon trigger whoami`, then set
`triggers.host_id`, the opaque `triggers.run_as_user_id`, and intervals in
`factory.yaml`. Set `triggers.enabled: true` and rerun `factory-setup`. It generates
version-1 timer files under `.archon/triggers/` using the current binding schema,
including absolute `cwd`, explicit isolation, resource, overlap, host, and run-as user.
Native capacity is fixed at `1`; app-check cannot be configured above one concurrent
launch. Binding identities are stable per configured project so separate projects in
one Archon home do not collide. An interval of `0` disables that generated binding.
Setup never installs or fires a timer, and it never edits legacy
`.factory/trigger.json`.

After a successful manual pass, installation is an explicit operator action:

- macOS: `archon trigger schedule install --config <absolute-trigger-json>` installs
  Archon's per-user launchd job. Remove it with `archon trigger schedule remove` using
  the same config. The user must be logged in; sleep and missed intervals follow
  launchd behavior.
- Windows: Archon does not install the scheduler task. Configure Task Scheduler under
  the intended user to run the absolute executable with
  `trigger fire --config <absolute-trigger-json>`, plus the correct working directory
  and `ARCHON_HOME`. Disable or remove that task to stop future starts. Test it as the
  scheduled user.
- Linux: point a user systemd timer or cron entry at the same absolute `archon trigger
  fire --config ...` command.

Stopping the scheduler prevents future requests but does not cancel accepted work.
Use `archon trigger list`, `archon trigger inspect <id>`, `archon workflow attention
--json`, and `archon workflow get <run-id> --json` for status. Cancel a run only when
ending its current work is intended; follow the `archon-cli` skill for lifecycle
operations.

## App-check freshness and protected changes

`factory-app-check` tests the selected PR head merged with the current base head. It
publishes the `factory/app-check` commit status and binds the status description to a
hash of the exact base branch name and base commit. Publication rereads the PR and
fails if its head or base changed. A pending status older than two hours is stale and
does not suppress another check. Evidence must be newer than preparation, every check
line must cite a real file, and `INCOMPLETE` is failure.

GitHub branch protection must require the repository CI check, `factory/app-check`, and
`factory/policy`, with strict "Require branches to be up to date before merging"
enabled. A green status for another base, an older head, or an expired pending check is
not fresh evidence. Archon's merge queue independently pins review, CI, and merge to the
exact PR head; project merge policy should name every required check. Protected-path
changes fail closed unless the app-check input carries an exact operator grant for that
head and the complete matched path set.

## External holdouts

Optional holdouts live at the absolute, project-specific external path in
`holdouts.path`; there is no repository default. The writer and normal/deep app-check
nodes receive that path, while ordinary builders do not. Public reports contain IDs
and visible outcomes, not scenario text. Setup normalizes the configured path and
rejects the checkout itself or any path inside it without reading scenario contents.

This is context separation, not an operating-system security boundary. A process with
filesystem permission can still read the directory. Use a separate account, ACLs,
container boundary, or host appropriate to the threat model. Never put credentials in
scenario files or rely on prompt instructions as access control.

## Deployment is separate and explicit

Setup never enables deployment and no trigger is generated for it. A target project
must provide `DEPLOY.md` documenting the exact revision-aware deploy command, health
check, and deployed-revision readback. Then an operator may explicitly run:

```bash
archon workflow run factory-deploy \
  --input revision=<exact-40-character-commit> --detach
```

The deploy agent may execute only commands documented in `DEPLOY.md`. It cannot infer a
cloud, destination, credential, service, or fallback. The workflow fails if health is
not healthy, evidence is missing, or the read-back revision differs from the requested
revision. No deployment is performed by this repository's validation.

## Validate the package

Use the matching sandbox environment and its configured Archon provider. Workflow
fixtures are stubbed and make no provider call:

```bash
source /path/to/v1test-runtime/env.sh
bun test ./.archon/workflows/factory/app-check/__tests__/*.test.ts \
  ./.archon/workflows/factory/backlog/__tests__/*.test.ts \
  ./.archon/workflows/factory/deploy/__tests__/*.test.ts
archon workflow test .archon/workflows/factory
archon validate workflows
```

Also run `archon workflow list --json` and confirm the factory workflows are discovered
beside, not instead of, the bundled SDLC workflows. Validation proves parsing, fixture
outcomes, and deterministic boundaries; it does not prove provider login, GitHub
permissions, a scheduled user environment, external holdout isolation, or a real
deployment. Those remain operator acceptance steps.

## Layout

```text
.archon/
  factory.yaml                  operator-owned factory settings
  config.yaml                   inherited Archon defaults and generated bindings
  scripts/factory-config.ts     strict parser and mechanical setup
  skills/factory-setup/         setup guidance; delegates queue use to archon-cli
  skills/factory-manager/       policy-only attention tending
  sdlc/policy/                  project additions, not bundled-pack overrides
  workflows/factory/            factory-named project workflows only
```

Licensed under the MIT License; see `LICENSE`.
