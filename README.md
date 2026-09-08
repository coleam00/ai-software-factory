# AI Software Factory

Factory installs and invokes the shared Archon SDLC pack. Archon owns every coding
agent, workflow sequence, retry, approval gate, tracker write and merge decision.
Factory owns installation, project input data, native invocation and state display.

This migration is a supervised integration milestone. The final complete producer
revision is still required. Queue, regression, standing intake, release automation
and live runtime integration need verification before unattended operation can
be advertised.

## Install

Python, Git and Bun are prerequisites. Configure and authenticate your provider
through native Archon configuration. Factory never changes provider configuration,
authentication files, global binaries or model tiers.

From the application repository:

```text
python /path/to/ai-software-factory/bin/factory.py init --source /path/to/complete/Archon --revision <40-character-integration-SHA> --cache /path/to/immutable-sources
```

The installer clones the complete source into `<cache>/<SHA>`, installs its locked
Bun dependencies, checks HEAD and source cleanliness, probes native capabilities,
and validates discovered workflows and their command/script references. It never
repoints an existing source directory. Keep every old source directory while any
run or runtime child can still use it. Do not edit a pin in place.

Required entry names, descriptions, capabilities and the future integration pin
have one owner: [pack.json](template/factory/pack.json). Its null
`integration_revision_required` deliberately prevents an unqualified install from
claiming readiness. Supply a tested revision explicitly until integration lands.
`init --scaffold-only` installs project files without claiming engine readiness.

The machine-local `.factory/consumer.json` records the absolute source path, SHA
and Bun executable. The CLI always executes `packages/cli/src/cli.ts` from that
source and supplies the same root as `--workflow-source`, with the application as
`--cwd`. Ambient `archon` binaries and application-local workflow overrides are
not used for factory dispatch.

## Operate

Use `python /path/to/ai-software-factory/bin/factory.py` as `factory` below, or use
`python factory/consumer.py` from an installed application:

```text
factory doctor
factory list
factory run archon-ship --input target=<request-file> --detach --json
factory run archon-deliver --adopt <producer-run-id> --input work=<findings-file> --detach --json
factory get <run-id> --json --verbose --events
factory status --all --json
factory approve <run-id> --comment "approved scope"
factory reject <run-id> --reason "candidate moved"
factory respond <run-id> <decision> "response text"
factory cancel <run-id>
factory resume <run-id> --detach --json
```

`run` accepts exact shared workflow names discovered from the pinned SDLC source,
including future compositions. Declared inputs and native run arguments pass
through unchanged. Archon validates them and owns worktree isolation. Native
output and exit codes pass through without receipt interpretation or follow-up
dispatch. Resume uses the engine's captured source. Factory does not synthesize a
terminal status from missing artifacts, elapsed time or old labels.

`halt` blocks new launches and continuations through this consumer. It does not
cancel active engine runs. Cancel each known run explicitly. `unhalt` clears only
the local launch brake. Native `get` and `status` expose run and checkout identity.
Provider configuration and actual authentication remain native concerns; doctor
checks source/capability readiness and does not perform a live agent login test.

## Project checks and upgrades

`python harness/ci.py` runs configured static and unit checks only. Empty checks,
zero counted tests and failed commands cannot claim success. Runtime and holdout
verification must be composed separately by the shared runtime workflow. Ordinary
checks succeeding do not authorize a merge or prove runtime coverage.

`harness/runtime.inputs.json` contains scenario and environment data. The
[project runtime host](template/factory/RUNTIME_HOST.md) provides fresh ordinary
apps, source snapshots and state for shared runtime, holdout, retry and mutation
nodes. Use `factory run <workflow> --runtime-host <trusted-config.json>` for a
foreground run, or keep the host foreground for manual native Archon use. Host
mode rejects detach/resume until durable ownership is supported. Python never
schedules evaluations. [Migration details](template/factory/MIGRATION.md) map
legacy inputs and list the remaining producer contracts.

```text
python /path/to/ai-software-factory/bin/sync-to.py /path/to/application --dry-run
python /path/to/ai-software-factory/bin/sync-to.py /path/to/application
```

Upgrade preserves user configuration, scenarios, app files and native provider
settings. Replaced machinery and retired prompt skills are backed up byte for byte
under `.factory/retired/`; references to retired paths are reported before removal.
Existing scheduler entries and running old processes require explicit retirement.
Old tick, arm, level, accept, merge and deployment paths fail with migration guidance.
No factory cron scheduler is installed.

## Verify this repository

All fixtures are local; the fake native CLI records argv without launching an agent.

```text
python bin/test_consumer.py
python bin/test_runtime_host.py
python template/factory/_selftest.py
python template/factory/_test_watchdog.py
python bin/audit.py
python bin/selfcheck-mutations.py
```

These checks establish the consumer boundary, installation preservation and ordinary
harness behavior. They do not replace final live acceptance on the integrated pack.
