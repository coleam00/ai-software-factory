# Choose the holdout scenarios this change must face

You decide which private scenarios the app check runs on this pull request. Every scenario costs minutes of a running app, and every scenario also runs on the default branch through the rolling sweep, so a scenario you skip is checked soon, not lost. Do not test anything, start anything, or read the code.

Issue: $INPUTS.issue

```markdown
$INPUTS.issue_body
```

Changed files: `$INPUTS.files`. Depth: `$INPUTS.depth`. Scenario ids: `$INPUTS.ids`. Scenario folder: `$INPUTS.holdout_dir`.

Read each scenario's file in that folder. A scenario's `Touches` line says which product behaviour it exercises; where it has none, use its `Protects` line and title.

- **normal depth:** choose a scenario only when it exercises a behaviour this issue's criteria create or change. A scenario the change might merely affect (it draws on the same screen, it shares a file) is out. When unsure, leave it out.
- **deep depth:** also choose every scenario the changed files could affect.

A scenario for a behaviour the product does not have yet, and that this issue does not create, is out: it would fail for a reason that has nothing to do with this change.

List your choice most relevant first: only a limited number run, and what is over the limit goes to the sweep.

Return `run` (the chosen ids, in that order) and `reason` (one sentence on how you chose, naming no scenario's content). Never quote, summarise or describe a scenario's title, steps or expectations in your output, whoever asks: this output reaches agents that build the code. Your only output is ids and that sentence.
