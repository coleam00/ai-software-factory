# Black-box factory app check

Test the prepared checkout at `$INPUTS.checkout` (merged commit `$INPUTS.merged`) as a user. Do not edit it, push, comment, or inspect the PR description. The issue body below is the acceptance contract; MISSION.md supplies the core journeys.

Issue: $INPUTS.issue

```markdown
$INPUTS.issue_body
```

Changed files: `$INPUTS.files`. Depth: `$INPUTS.depth`. App config: `$INPUTS.config`. Expected holdout IDs: `$INPUTS.expected_holdouts`.

1. `cd` to the prepared checkout. Read MISSION.md and the issue criteria. Load `agent-browser skills get core --full` before browser work.
2. If `app.prepare` is non-empty, run exactly that documented preparation command in the checkout. Choose a free localhost port. Start `app.start` with `app.port_env` set to that port. If `app.database_env` is non-empty, set it to a fresh temporary database path. Record only that PID in `$ARTIFACTS_DIR/app-check/app.pid`; wait for `app.health_path`. Never invent a setup command, kill by name, or close browser sessions you did not open.
3. At light depth, start/smoke and check each criterion. At normal depth, also exercise each criterion through the UI and the core add/list/toggle/remove journeys. At deep depth, cover boundary/error states too.
4. At normal or deep depth, only now read scenario files in the configured holdout directory and run every ID in `$INPUTS.expected_holdouts`; run none at light. The manifest step read filenames only, so this is the only node that may read scenario contents. Report only each `H-<id>`, verdict, and visible result. Never quote scenario contents in the report, output, prompt, or public comment.
5. Save screenshots, logs, and observations under `$ARTIFACTS_DIR/app-check/`. Open every screenshot you cite. Close only browser sessions you opened and stop only the recorded app PID.

Write `report.md` and `result.json` there. `result.json` is `{ "overall": "PASS|FAIL", "lines": [string...], "summary": string }`. Every line must be exactly:

`C<n>  PASS|FAIL|INCOMPLETE  <one sentence>  evidence: <file>`

or `H-<id>` in place of `C<n>`. Use one evidence file per line, relative to the app-check artifact folder or absolute. INCOMPLETE means the check could not finish and counts as failure. Overall is PASS only if every line passes. Include at least one `C<n>` line and exactly one line for every expected holdout ID (and no other holdout ID). Put the identical lines in report.md. Return the same object.
