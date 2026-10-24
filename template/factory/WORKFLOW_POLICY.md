# Factory workflow policy

This project uses Archon's shared SDLC workflows. The requirements below are
project guidance for those workflows; they do not replace workflow gates or grant
permission to publish, comment, merge, deploy, or change schedules.

## Scope and backlog bootstrap

Read `MISSION.md` in full. Its product scope, exclusions, hard invariants, and
human-owned decisions govern every backlog and delivery decision. Leave work that
contradicts them out of a proposed backlog and report the conflict.

For a product-document bootstrap, make the first ticket the smallest real core path
that can be run and verified end to end. The ticket must name an exact start-command
argv with a port placeholder, health path, build-identity path, persistent-state
path or environment variable, test command, and pull-request CI check. HTTP apps
report `FACTORY_RUNTIME_CANDIDATE` exactly at the build-identity path when it is set,
and otherwise report their Git commit. Reuse the project's declared ordinary gate;
adding tests or CI is allowed, but weakening its judge is not. In an existing
codebase that already provides this foundation, the first ticket is an ordinary
first story.

## Factory-owned workflow inputs

Factory supplies `holdout=.factory/holdout/HOLDOUT.md` to `archon-loop`, `archon-manager` and
`archon-holdouts`, and the project's `protected_paths` (from `harness/harness.config.json`) to
`archon-loop` and `archon-merge-queue`, unless the caller supplies them. The holdout scenarios are
read only by the `holdout-verifier` and `holdout-writer` agents; `.factory/hooks/holdout_fence.py`
refuses every other agent.

## Runtime and review evidence

Runtime and holdout evidence must exercise the delivered candidate and match its
current source identity. When using the factory runtime host, follow
`factory/RUNTIME_HOST.md`: prepare the resource from the delivering checkout at its
expected revision, require the configured health and build-identity checks, use
fresh state for each attempt, and preserve the typed result as evidence. Ordinary
checks do not substitute for required runtime or holdout evidence.

Independent review evidence may be Archon's canonical current-head report, a
genuine review by an independent human, or an external independent report format
that project guidance accepts. In every case it must identify the PR's current head,
be credible and independent of the implementer, state a ready verdict, and contain
no unresolved blocking findings. Stale, ambiguous, or self-attested evidence holds.
