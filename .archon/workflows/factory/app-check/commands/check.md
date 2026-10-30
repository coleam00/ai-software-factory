# Black-box factory app check

Test the prepared checkout at `$INPUTS.checkout` (merged commit `$INPUTS.merged`) as a user. Do not edit it, push, comment, or inspect the PR description. The issue body below is the acceptance contract; MISSION.md supplies the core journeys.

Issue: $INPUTS.issue

```markdown
$INPUTS.issue_body
```

Changed files: `$INPUTS.files`. Depth: `$INPUTS.depth`. App config: `$INPUTS.config`. Expected holdout IDs: `$INPUTS.expected_holdouts`.

1. Before changing directories, resolve `.archon/workflows/factory/app-check/scripts/app-process.ts` from this workflow's original working directory to an absolute path. Use that absolute helper path for every `app-process.ts` command below. Never load the helper from the prepared PR checkout, which may contain an older package. Every shell invocation of the helper must explicitly set the `ARTIFACTS_DIR` environment variable to this run's exact path: `$ARTIFACTS_DIR`. For PowerShell, put `$env:ARTIFACTS_DIR = '$ARTIFACTS_DIR'` before invoking the helper in that same command. Agent tool shells do not automatically inherit this workflow variable. Then `cd` to the prepared checkout. Read MISSION.md and the issue criteria. Load `agent-browser skills get core --full` before browser work.
2. Before starting an app, run `bun .archon/workflows/factory/app-check/scripts/app-process.ts support`. If it does not report `supported: true`, do not start the app; record the limitation as `INCOMPLETE`. If `app.prepare` is non-empty, run exactly that documented preparation command in the checkout. Choose a free localhost port. Start `app.start` directly with `app.port_env` set to that port and retain the native launcher process object/handle (on Windows, use `Start-Process -PassThru`, not a detached PID-only shell launch). If `app.database_env` is non-empty, set it to a fresh temporary database path. From that retained object read `Id` and `StartTime.ToFileTimeUtc()`, then immediately run `bun .archon/workflows/factory/app-check/scripts/app-process.ts record <PID> <CREATION_FILETIME>`; the helper compares the launcher identity with a newly opened handle before writing the versioned record under `$ARTIFACTS_DIR/app-check/`. Never write a bare PID. If the configured command cannot be started directly with a retained handle, or identity recording fails, mark the check `INCOMPLETE` and stop only through the exact launcher handle you retained; never fall back to a numeric PID. After recording succeeds, wait for `app.health_path`. Never invent a setup command, kill by name, or close browser sessions you did not open.
3. At light depth, start/smoke and check each criterion. At normal depth, also exercise each criterion through the UI and the core add/list/toggle/remove journeys. At deep depth, cover boundary/error states too.
4. At normal or deep depth, only now read scenario files in the configured holdout directory and run every ID in `$INPUTS.expected_holdouts`; run none at light. The manifest step read filenames only, so this is the only node that may read scenario contents. Report only each `H-<id>`, verdict, and visible result. Never quote scenario contents in the report, output, prompt, or public comment.
5. Save screenshots, logs, and observations under `$ARTIFACTS_DIR/app-check/`. Open every screenshot you cite. Close only browser sessions you opened. Stop the app only with `bun .archon/workflows/factory/app-check/scripts/app-process.ts stop`; it verifies the recorded identity before termination and retires the record only after the owned process has exited. Do not use `kill`, `taskkill`, `Stop-Process`, or any other PID-based fallback. The workflow cleanup node calls the same helper if this checker exits early.

Write `report.md` and `result.json` there. `result.json` is `{ "overall": "PASS|FAIL", "lines": [string...], "summary": string }`. Every line must be exactly:

`C<n>  PASS|FAIL|INCOMPLETE  <one sentence>  evidence: <file>`

or `H-<id>` in place of `C<n>`. Use one evidence file per line, relative to the app-check artifact folder or absolute. INCOMPLETE means the check could not finish and counts as failure. Overall is PASS only if every line passes. Include at least one `C<n>` line and exactly one line for every expected holdout ID (and no other holdout ID). Put the identical lines in report.md. Return the same object.
