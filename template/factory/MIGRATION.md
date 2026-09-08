# Migration to the shared Archon SDLC pack

The old factory stage protocol is retired. User-authored app code, tests,
configuration and scenarios remain yours. Existing `factory/config.py` is preserved
byte for byte, but the consumer never imports it. Move desired project constraints
into native project guidance and shared workflow inputs. A setting being preserved
does not mean the old Python policy is still enforced.

## Responsibility map

| Previous owner | Current owner or migration requirement |
| --- | --- |
| dispatch.py, state.py, trigger.py, regress-trigger.py and loop.sh stage selection | Shared lifecycle and regression compositions, after producer integration |
| sdlc.py six-stage result consumption and repair retry counters | Native workflow topology and run lifecycle |
| accept, assumption files, autonomy dial, gate.py and merge.py receipts | Declared shared gates and queue policy; local receipts never authorize merges |
| label transitions, follow-up dispatch and private authorizations | Shared workflow and native forge operations |
| watchdog elapsed-time inference and monitor log liveness | Native get/status/cancel/resume; unknown observations remain unknown |
| deploy.py callback after merge | Shared release workflow with its own gate, pending integration |
| agentcheck.py provider launch, prompts and malformed-report retry | Shared runtime command nodes and their bounded retry policy |
| ci.py | Ordinary static/unit checks only; CHECKS_OK mode=ordinary is limited evidence |
| mutations/run.py | Deterministic list, apply-one and score only; no copies, subprocesses or verification loop |
| appproc.py | Optional ordinary app process helper; no provider execution |

## Configuration and scenario mapping

| Existing data | Destination and meaning |
| --- | --- |
| harness.config.json static, unit, unit_count_pattern | Same keys consumed by ci.py; command strings and argv arrays supported |
| harness/END-TO-END.md | Runtime scenarios, preserved at the same path |
| .factory/holdout/HOLDOUT.md | Holdout scenario data, preserved at the same path |
| driver, http, cli, library, browser | Environment data exported by runtime_data.py; same values and paths |
| e2e_timeout_s and agent.timeout_s | Legacy timeout observations in the export; map to producer-owned budgets |
| agent.cmd and FACTORY_AGENT_CMD | Retired and never executed; configure provider/model tiers natively |
| .factory/locks/floor.json and old marker requirements | Preserved measurements; explicitly translate to producer coverage requirements before treating them as enforced |
| mutations/defects.json id/file/find/replace/why | Same data, consumed one mutation at a time; copy lists are preserved but not executed |
| FACTORY_AUTONOMY and AUTONOMY | Ignored; cannot authorize a run, bypass a gate or permit merge |
| FACTORY_ARCHON_BIN and old WORKFLOW_* aliases | Ignored; only the configured complete source and its discovered SDLC names are callable |
| factory/config.py deploy commands and protected-path settings | Preserved for operator migration to ordinary commands and shared workflow policy; no implicit execution |

Export old runtime data without changing the original:

```text
python harness/runtime_data.py --config harness/harness.config.json
```

The export and `runtime.inputs.json` are project data, not a reserved producer
schema. The final runtime producer determines declared input names. Map the data
explicitly to that revision's inputs; factory passes `--input name=value` verbatim
and does not invent a runtime API or compose a workflow in Python.

The old fresh-environment requirements remain requirements: start a fresh app and
fresh database for the runtime pass, holdout pass, every malformed-report retry and
every mutation candidate. Persistence scenarios intentionally restart against the
same state only within that scenario. Preserve assertion-level observed evidence,
required coverage and product-failure versus infrastructure/inconclusive outcomes.
A retry belongs to a visible Archon node, never to these helpers.

The retained appproc helper is legacy best-effort machinery. Use the new
[runtime host](RUNTIME_HOST.md) for owned ordinary processes, independent source
snapshots and fresh filesystem state. Its supported command shapes, Windows job
ownership, Unix session limits, candidate identity contract and private scenario
placement are explicit. The final shared producer integration still needs live
verification; neither helper is a same-user filesystem sandbox.

Deterministic mutation commands:

```text
python harness/mutations/run.py list
python harness/mutations/run.py apply <id> --candidate <prepared-isolated-directory>
python harness/mutations/run.py score --log <ordinary-check-log> --exit-code <code>
```

Application is explicit and changes only a unique anchor inside the supplied
candidate. Missing, ambiguous, escaping and no-op anchors fail. The shared workflow
prepares candidates, invokes runtime verification explicitly per candidate, and
aggregates current typed evidence. `score` classifies a supplied log; it does not
bind a log to a candidate or authorize work. A timeout, missing tool, import error
or missing assertion evidence is inconclusive. Empty mutation sets are not
reported as successful coverage.

## Upgrade and source ownership

`init` and `sync-to.py` preserve user app/scenario/configuration files. Changed
consumer/harness machinery is backed up before replacement. All retired generated
skills are backed up, including custom versions, then removed from discovery.
Retired paths include `.archon/workflows/factory`, `factory/nodeio.py`, the nine
old `factory-*` skills, and the old notification script. Active stage entrypoints
are replaced by explicit refusals. Backups are inert data under `.factory/retired`;
do not execute them. Review reported stale references before using custom guidance.

Stop any old loop process and remove the old factory cron or Task Scheduler entries
explicitly. Upgrading files cannot revoke an already-running old Python process.
Native runs should be inspected and cancelled by run ID when required. `halt` is a
local brake only; it does not claim to revoke native gates or cancel live work.

The source lives in `<cache>/<full-SHA>` and is never repointed. Native parent
runtime children resolve their author's origin when launched, so preserve every
source directory for the lifetime of all associated runs and resumptions. Source
verification rejects changed tracked files, authoring symlinks and untracked or
ignored authoring files. The Bun dependency installation is locked but is not an
OS-enforced immutable environment. Do not edit source or installed dependencies.

Factory accepts plain literal packaged command/script/include references whose
resources are present in the pinned source. Archon validates their full definitions.
Unsupported reference shapes fail with a provenance diagnostic rather than using
home-scoped fallbacks. A producer syntax change may require a consumer update.

## Remaining integration

Select and record a complete tested producer SHA in pack.json (or explicitly pass
an integration SHA to init). Missing required workflows must keep installation red.
Finalize live runtime input mapping, coverage and result
schemas; queue and merge gates; regression/discovery composition; standing intake
and release workflow ownership. Preserve native provider authentication and validate
unsupported providers against the final producer. No unattended operation is claimed.

Run final live issue-to-PR, adopted same-PR repair, independent review, runtime and
holdout verification, explicit mutation candidates, denied/stale queue gates and
permitted isolated-base merge acceptance. Local fake-CLI tests do not establish
real provider launch provenance or live workflow correctness.
