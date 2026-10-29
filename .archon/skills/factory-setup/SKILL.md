---
name: factory-setup
description: Configure this repository's small factory layer without installing or firing automation.
---

# Factory setup

This skill requires Archon's own `archon-cli` skill from the matching engine revision.
Load that skill for workflow discovery, queue operation, attention handling, and trigger
commands. Do not duplicate or improvise its operating instructions here. If it is not
installed, stop and install it from the matching Archon source before operating queues.

1. Read `README.md`, `.archon/factory.yaml`, the existing `.archon/config.yaml`, and the
   project policy files. Do not read an external holdout directory.
2. Ask only for values the repository cannot supply: `OWNER/REPO`, app preparation/start
   and health details, a project-specific external holdout path if enabled, Codex model
   identifiers, and (only when schedules are requested) `host_id` plus the UUID from
   `archon trigger whoami`.
3. Edit `.archon/factory.yaml`. Keep deployment out of setup: `factory-deploy` is a
   separate explicit workflow and has no generated timer.
4. Run `archon workflow run factory-setup --detach`, inspect the result, and review the
   exact diff. The workflow validates settings, maps the autonomy preset into marked
   policy blocks without changing surrounding prose, writes Codex model bindings to
   `.archon/config.yaml`, and generates disabled-until-installed trigger JSON.
5. Validate with `archon workflow list --json` and the checks in `README.md`. Report all
   missing project policy and credentials. Never install or fire a timer merely because
   setup succeeded.

Queue launches, trigger installation, attention answers, stopping, and status checks are
all governed by the `archon-cli` skill, not this one.
