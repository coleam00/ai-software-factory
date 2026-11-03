# Run a batch of holdout scenarios on the default branch

Pull requests only face the scenarios they exercise. This run is the backstop: it checks a batch of scenarios against the default branch at `$INPUTS.merged`, in the prepared checkout `$INPUTS.checkout`, so one that a pull request skipped is still checked soon. Do not edit anything, push, or comment.

App config: `$INPUTS.config`. Scenario folder: `$INPUTS.holdout_dir`. Run exactly these ids: `$INPUTS.expected_holdouts`.

Start and stop the app exactly as `.archon/workflows/factory/app-check/commands/check.md` says: read that file now and follow its steps 1 and 2 (or **Runtime mode** when the app config's `runtime` is set) and its step 5. Its other steps are about a pull request and do not apply.

Read each scenario in the batch from the folder and run it as a user would, through the running app's public surface only. Do not read the application's source first. Give each a verdict:

- **PASS:** every expectation observed.
- **FAIL:** an expectation contradicted.
- **INCOMPLETE:** it could not be run to the end (the app would not start, the machine was too loaded for a timing to mean anything). Never a pass.

Where a scenario names a judge script beside it, run the judge on what you recorded; its verdict stands. If a scenario itself looks wrong (it contradicts the product document), say so in its line and give your verdict anyway.

Save screenshots and logs under `$ARTIFACTS_DIR/app-check/`. Write `report.md` and `result.json` there. `result.json` is `{ "overall": "PASS|FAIL", "lines": [string...], "summary": string }`. Every line must be exactly:

`H-<id>  PASS|FAIL|INCOMPLETE  <one sentence on what a user sees>  evidence: <file>`

Exactly one line for every id in the batch and no other. The sentence describes behaviour as a bug report would. It reaches public issues, so never quote a scenario's title, steps or expectations in it. Overall is PASS only if every line is PASS. Put the identical lines in report.md and return the same object.
