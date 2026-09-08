# Project workflow guidance

Keep project constraints and irreversible-action requirements in this file and the
native project guidance. Shared workflow gates own authorization. Factory does not
interpret this document as a private merge policy.

Preserve scope, tests, security invariants and secrets. Report failed or unavailable
checks truthfully. Ordinary static/unit success does not replace required runtime
or holdout verification. Each required assertion needs observable evidence.

Specify this application's protected paths, required validation coverage and
publication constraints in the shared workflow's supported inputs. Preserving old
configuration during upgrade does not prove those rules remain enforced.

See [migration and integration requirements](factory/MIGRATION.md). No autonomy
level or locally written receipt permits bypassing an Archon gate.
