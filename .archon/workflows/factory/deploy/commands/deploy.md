# Deploy one explicit revision

Read `$INPUTS.deploy_doc` and deploy exactly revision `$INPUTS.revision`.

- Execute only deployment, health-check, and deployed-revision readback commands that
  `DEPLOY.md` explicitly documents. Do not infer a destination, credential, command,
  service name, cloud, or fallback. If any required instruction is absent, fail before
  changing external state.
- Pass the requested revision through the documented mechanism. Never substitute the
  current branch, default branch, a tag, or a newer commit.
- After deployment, run the documented health check and documented revision readback.
  A successful command is not evidence of health or identity.
- Save exact redacted command output under `$ARTIFACTS_DIR/deploy/`. Never save secrets.
  Write `$ARTIFACTS_DIR/deploy/report.md` with the requested revision, observed health,
  observed deployed revision, commands run, timestamps, and evidence file paths. Include
  that report itself in `evidence_files`.
- Return the exact requested and observed revisions, `healthy` only when the documented
  health check passed, every evidence path, and a concise summary. Any mismatch or
  incomplete readback is a failure, not a warning.
