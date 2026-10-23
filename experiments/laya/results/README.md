# Compact routing prompt: initial CPU results

Evaluated 2026-09-28. **Do not adopt either configuration for live routing on this
evidence.** The contribution is the reproducible experiment and its negative
acceptance result, not a claim that Laya can replace Archon's grounded triage.

Only one feature is under consideration: shortening the decision prompt. The
base and specialized checkpoints are two controls for the same feature. No model
routing service, skill selector, context compressor or workflow change is bundled.

## Benefits and trade-offs

Each profile evaluates the same 20 synthetic cases. No prompt, label or case was
changed after observing model answers. Every sequence passed the full-input
audit, including instruction, per-option and state tokens.

| Checkpoint | Prompt | Agreement | Premature deliver | Laya input tokens |
| --- | --- | --- | --- | --- |
| English base | Detailed | 4/20 | 3 | 3,722 |
| English base | Compact | 12/20 | 5 | 2,162 |
| Typed decisions | Detailed | 12/20 | 4 | 3,722 |
| Typed decisions | Compact | 13/20 | 1 | 2,162 |

The compact prompt processes **41.9% fewer Laya input tokens** on this fixture.
These are encoder tokens measured by Laya, not Codex tokens or a measured
reduction in total factory cost. The profiles express the same intended policy,
but compression can lose useful nuance; semantic equivalence is a hypothesis.

Aggregate agreement hides individual regressions:

- Base: compact gets `s4` wrong after detailed gets it right, while correcting
  nine other cases. Premature delivery increases from three cases to five.
- Typed: compact gets `s2` (outside the mission) wrong after detailed gets it
  right, while correcting `d2` and `s1`. It gets only one of five stop cases right.

Neither checkpoint has a selected probability at or above the predeclared 0.9
diagnostic threshold. Applying that cutoff yields **zero coverage**, not proof
of a reliable useful router.

## Can the trade-off be removed?

Not established. On the typed compact results, a post-hoc 0.5 cutoff selects four
cases and all four agree with their labels. That observation is exploratory:
the cutoff was inspected after seeing these development results and is not a
validated acceptance policy. It sends the other 80% to fallback and adds local
inference overhead to every evaluated request. Zero observed mistakes in four
selected examples cannot establish zero production mistakes or net savings.

Keeping the original grounded triage mandatory preserves its role, but adding
Laya in front then costs extra work unless it demonstrably reduces later work.
Removing that gate to realize savings is not justified by these results.

The next evaluation of this same feature should use frozen prompt candidates,
untouched maintainer-reviewed examples and an existing-agent baseline. Measure
case-level regressions, premature delivery, fallback coverage, and total resources
through successful delivery. Do not tune against the final evaluation set. A
useful target is no observed quality regression plus a net resource benefit;
absolute zero trade-offs is not a conclusion these fixtures can support.

## Provenance and limits

- Raw reports: [English base](base-cpu.json), [typed decisions](typed-cpu.json).
  They include exact model revisions, all predictions, token counts, timing,
  selected probabilities, resolved inference precision, prompts and fixture hash.
- Laya was built from the upstream `v0.3.21` tag at
  `9d955671415fc19f069b9cc998928075c1f255ec`. The observed package environment is in
  [environment.txt](environment.txt); install CPU Torch from its CPU wheel index
  as described in the parent README. The Laya entry records the source revision
  rather than the machine-local build path.
- Linux x86-64, AMD Ryzen 5 2600X, Python 3.12.3, CPU FP32, two inference threads,
  one inter-op thread. Single sequential requests; no GPU or batching claim.
- Preliminary runs were repeated after review added resolved-precision metadata
  and loaded-revision verification. Predictions and token totals were checked
  against those runs. The committed reports are the final cached-model runs;
  setup time therefore does **not** measure a fresh model download.
- Laya warned about clamping the checkpoint's `choice:11+` temperature. This
  experiment has four choices, but no probability calibration is claimed for
  either checkpoint. The warning must not be interpreted as calibrated output.
- Public synthetic development fixtures, five per route, with provisional author
  labels. There is no maintainer-approved holdout, coding-model comparison,
  production task, real repository evidence collection or end-to-end savings
  measurement. CPU timing excludes setup, warmup and the input audit.

## Code validation

- Seven offline benchmark tests pass, including malformed-answer rejection,
  threshold accounting, token-loss detection and checkpoint provenance.
- Repository suite: 62 tests, 60 passed and two skipped (Windows launcher and
  an optional complete-Archon conformance checkout).
- Installed-template self-tests: three passed. Watchdog test: one passed.
- Consumer ownership audit: zero errors. Template/runtime files are unchanged.

The initial restricted-sandbox suite could not create local sockets. The full
suite was rerun with local socket access and passed; that environment failure was
not treated as a product failure or omitted from the verification process.

A later full-suite run intermittently failed
`test_readiness_failure_and_failed_setup_clean_descendants` (`setup=True`). The
same test passed three consecutive isolated runs on the untouched upstream
checkout. The existing `bin/` and `template/` files are byte-for-byte unchanged
by this contribution. A subsequent complete rerun passed (60 passed, two skipped).
The intermittent failure's root cause remains unresolved;
no runtime fix is bundled with this experiment.
