# Upgrading the factory

Upgrade an installed factory by pulling the factory repository and rerunning
`python <factory>/bin/factory.py init` from the application repository.

- `init` installs the files in `template/` and the Archon source pinned in
  `factory/pack.json`, then validates that source.
- Files you write for your project are never overwritten: `MISSION.md`,
  `harness/END-TO-END.md`, `harness/harness.config.json`,
  `.factory/holdout/HOLDOUT.md`, and the other project files listed in the installer.
- An installed factory file you changed is copied to `.factory/backup/` before
  the new version replaces it. Repeated upgrades keep numbered copies.
- Run `python factory/consumer.py doctor` afterwards. It checks the pinned source
  and that every shared workflow validates.

What the factory owns, and what Archon owns:

- **Archon** owns every workflow and agent: triage, delivery, review,
  validation, runtime and holdout verification, repair, discoveries, merging,
  deployment, and scheduling through native triggers.
- **The factory** owns your project's context files, the installation, and the
  thin consumer that invokes Archon and shows its state.
- **Runtime scenarios** are project inputs, and must exercise fresh environments
  running the actual candidate.

If you scheduled the factory, rerun `python factory/consumer.py schedule install`
after an upgrade, so the trigger binding picks up any new inputs.
