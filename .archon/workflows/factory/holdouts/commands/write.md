# Write private holdout scenarios

Read issue `$INPUTS.repo_path#$INPUTS.number` with `gh` and read MISSION.md. Write 1–3 scenarios into `$INPUTS.holdout_dir`; create the directory if needed. This is the only node allowed to read or write that directory.

Each file is `H-<nn>-<slug>.md`, continuing after existing ids, and contains:

```markdown
# H-<nn> Short private title
Given: user-visible starting state
When: actions through the running app's public UI
Then: exact user-visible outcome
Protects: product rule or defect class
```

Scenarios must be orthogonal to the issue's acceptance criteria, black-box, stable, and test a product invariant or adjacent failure class—not implementation details. Never write them into the repository, change existing scenarios, quote their contents in output, or mention them on GitHub. Return only written paths, ids, and a summary naming the protected product rules.
