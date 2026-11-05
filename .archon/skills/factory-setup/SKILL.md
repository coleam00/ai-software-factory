---
name: factory-setup
description: Configure this repository's small factory layer without installing or firing automation.
---

# Factory setup

This skill requires Archon's own `archon-cli` skill from the matching engine revision.
Load that skill for workflow discovery, queue operation, attention handling, and trigger
commands. Do not duplicate or improvise its operating instructions here. If it is not
installed, stop and install it from the matching Archon source before operating queues.

1. Read `.archon/factory.yaml`, the existing `.archon/config.yaml`, and the project
   policy files. (The factory README lives in the factory repository, not here.)
2. Ask only for values the repository cannot supply: `OWNER/REPO`, the maintainer
   logins (default: `gh api user --jq .login`), app preparation/start and health
   details, optional provider and model profiles, and, when the factory should run on
   its own, `host_id` plus the opaque ID from `archon trigger whoami`.
3. Edit `.archon/factory.yaml`. Keep deployment out of setup: `factory-deploy` is a
   separate explicit workflow and has no generated timer.
4. Run `archon workflow run factory-setup --detach`, inspect the result, and review the
   exact diff. The workflow validates settings, maps the autonomy preset into marked
   policy blocks without changing surrounding prose, writes configured model bindings to
   `.archon/config.yaml`, adds the maintainers to `.archon/sdlc/policy/maintainers.json`,
   writes `.github/workflows/factory-ci.yml` when the repo has no CI, and generates
   trigger JSON under `.archon/triggers/` (git-ignored).
5. Commit and push: the queues read policy, maintainers and CI from the default branch
   on GitHub, not from this checkout. Only with the owner's yes, install the timers
   with `bun .archon/scripts/factory-schedule.ts install` (remove with `remove`).
6. Run `bun .archon/scripts/factory-ready.ts`. Setup is done only when it reports
   `"ready": true`; otherwise fix what each failing line says.

Queue launches, attention answers, stopping runs, and status checks are governed by the
`archon-cli` skill, not this one.
