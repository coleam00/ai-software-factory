# Shared Archon workflow migration

Factory submits shared workflows from the source revision in pack.json. All coding
agents, including browser/runtime checks and discovery/merge decisions, execute
as Archon command nodes. Project checks and the resource host remain ordinary code.
No provider subprocess or factory-owned stage dispatcher is permitted.

Use archon-lifecycle for issue-to-merge, archon-ship for issue-to-PR, archon-deliver
for repair on the original PR estate, archon-regress for diagnosis, and the shared
runtime/discovery/merge workflows individually when needed. Runtime scenarios
are project inputs. Scenario and holdout data must refer to fresh environments
running the actual candidate, not a previously deployed revision.

Scheduling is Archon's: `factory schedule install` writes an Archon trigger binding
(`.factory/trigger.json`) and the host timer runs `factory schedule fire`, which calls
`archon trigger fire` and `archon workflow wake`. Archon admits each start on a
per-repository resource, queues an overlapping one, and resumes durable waits.
`factory tick` and `.factory/loop.sh` are retired; delete an installed `loop.sh`
and any `factory-timer` service that runs it. Agents use gh inside workflows. Do not
infer merged state from a queued merge request or successful local tests.

Upgrade preserves customized project files and backs up retired generated files.
Do not run the old stage scheduler alongside the new consumer. No application
upgrade or scheduler installation was performed during this cleanup. The pinned
candidate and the new lifecycle still need a focused real run before recording.
