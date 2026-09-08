# AI Software Factory

Factory installs and invokes the shared Archon SDLC pack. Archon owns every coding
agent, workflow sequence, retry, approval gate, tracker write and merge decision.
Factory owns installation, project input data, native invocation and state display.

This branch consumes a workflow-only integration candidate. Discovery and merging
use gh inside shared command nodes. Factory may schedule whole runs; it never
launches a coding agent or dispatches individual stages. The simplified integration
has not been run end-to-end. Earlier test results do not validate these edits.

## Install

Python, Git and Bun are prerequisites. Configure and authenticate your provider
through native Archon configuration. Factory never changes provider configuration,
authentication files, global binaries or model tiers.

From the application repository:

```text
python /path/to/ai-software-factory/bin/factory.py init
```

The installer clones the complete source into `<cache>/<SHA>`, installs its locked
Bun dependencies, checks HEAD and source cleanliness, probes native capabilities,
and validates discovered workflows and their command/script references. It never
repoints an existing source directory. Keep every old source directory while any
run or runtime child can still use it. Do not edit a pin in place.

Required entry names, descriptions, capabilities and the integration pin have
one owner: [pack.json](template/factory/pack.json). The pinned source is an
unmerged, untested workflow-only candidate, not an Archon release. It excludes
all newly proposed engine features. A pin fixes source identity, not readiness.
`init --scaffold-only` installs project files without claiming engine readiness.

The default install uses commit `27b8fe6debae7adb4d622431e3addb9031589c07` from
Archon's [`cleanup/sdlc-workflows-only`](https://github.com/coleam00/Archon/tree/cleanup/sdlc-workflows-only)
branch. No manual Archon checkout or upstream PR merge is needed. Custom source
installs can still supply `--source`, `--revision`, and `--cache`.

Once the workflow PRs are merged upstream, change only
`integration_revision_required` in `template/factory/pack.json` to the complete
upstream commit SHA. New installs use that revision. Existing installations keep
their current source until the operator pulls Factory main and reruns `factory init`
from the application repository. Old pinned sources remain available to active runs.

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
including archon-lifecycle for the complete sequence. Declared inputs and native run arguments pass
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
Factory scheduling is optional and external to Archon. Configure
`.factory/schedule.json` with `workflow: "archon-lifecycle"`, an `inputs` object
(target, absolute scenario/holdout paths, merge_mode and discovery_publication),
and optionally `runtime_host` pointing to the trusted environment configuration.
Run `factory tick` for one foreground invocation, or `bash .factory/loop.sh` for
serial invocations separated by FACTORY_INTERVAL_SECONDS (default 300). An OS
timer can also call tick with overlapping runs disabled. No timer is installed
or started by this migration. The schedule submits one whole shared workflow;
all issue interpretation and stage decisions remain in its Archon nodes.

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

## Workflow-only review set

- Triage: Archon #3229; existing ship/deliver improvements: #3204 and #3205.
- Runtime verification and control suite: #3227 and #3235.
- Regression diagnosis: #3230.
- Discovery, including gh publication: #3231.
- Merge queue, including gh CI checks and authorization: #3243.
- Shared lifecycle composition: Archon #3246.

The forge extensions, native trigger admission and separate publication/automatic
queue follow-ups are not dependencies. Existing Archon review/PR command nodes
remain the implementation; the experimental forge review publisher is excluded.
No tests, agent runs or application upgrades were performed in this cleanup.
