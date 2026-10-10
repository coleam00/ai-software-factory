# Laya routing and token-budget experiment

Follow-up: [one review-scope candidate tested inside Archon](REVIEW-PROBE.md).
That probe found no accepted replacement or verified avoided agent calls.
Its follow-up compared both checkpoints and separate questions on seven public
inputs: no eligible replacements, and splitting nearly doubled encoder tokens.
The standalone experiment described below remains preliminary evidence.

Can a compact decision prompt preserve useful routing judgments while reducing
the input processed by a local Laya model? This opt-in experiment produces evidence
for that question. It does not replace grounded triage, select coding models,
invoke Archon, publish issues, or authorize delivery or merge.

Factory remains a thin consumer of Archon. Any future live decision-model adapter
belongs in the shared SDLC source; nothing here is copied by `factory.py init`.
No API key or paid provider is required. Initial setup downloads model weights.

## Reproduce

Python 3.10+ is required. Create an isolated environment outside the checkout:

```bash
python3 -m venv /tmp/factory-laya
/tmp/factory-laya/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
/tmp/factory-laya/bin/python -m pip install -r experiments/laya/requirements.txt
HF_HOME=/tmp/factory-laya-models /tmp/factory-laya/bin/python experiments/laya/routing.py \
  --output tmp/laya-base.json
```

The default is the English base model at exact Hugging Face revision
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, with `laya==0.3.21`, CPU and two threads.
An explicit model/revision pair can compare another checkpoint using the same
cases and questions, for example:

```bash
HF_HOME=/tmp/factory-laya-models /tmp/factory-laya/bin/python experiments/laya/routing.py \
  --model convaiinnovations/laya-typed-decisions \
  --revision 1a793eb568e6718f15941d08f85432581df534e3 \
  --output tmp/laya-typed.json
```

Validate fixtures and test the reporting code without Laya or network access:

```bash
python3 experiments/laya/routing.py --validate
python3 -m unittest discover -s experiments/laya -p 'test_*.py'
```

Reports refuse to overwrite an existing file. Model loading and warmup are timed
separately. An inference or validation error aborts the run with a nonzero exit;
it does not silently exclude the failed case and publish a success report.

## What the experiment measures

Twenty public, synthetic development cases describe already-gathered repository
evidence. There are five examples per label. Labels are provisional author
judgments, not maintainer-approved ground truth. Only `state` enters the model;
case IDs and expected labels are used for scoring afterwards.

The stage names follow the intent of the pinned
[Archon shipping workflow](https://github.com/coleam00/Archon/blob/24796870605b0fd576734b79a8d5c781f2b1c1e1/.archon/workflows/sdlc/ship/archon-ship.yaml):
`deliver`, `investigate`, `plan`, and the experiment's aggregate `stop` label for
blocked, unnecessary or out-of-scope work. This is not Archon's full contract or
a replacement for checking real repository evidence.

Two fixed prompts express the same intended routing policy with different detail.
Both use the same cases, model, 1,024-token sequence limit and 384-token head
budget. The runner alternates profile order by case. Before inference it compares
every encoded sequence with its uncut form and refuses any token loss, including
Laya's separate per-option limit. The adapter intentionally targets 0.3.21;
encoding changes require review rather than silently invalidating comparisons.

Each report includes:

- Package versions, model revision, fixture SHA-256, prompts, CPU settings and
  resolved inference precision. Laya honors `LAYA_CPU_AMP=bf16`; leave it unset
  for the default FP32 comparison and compare timings only at the same precision.
- Every prediction, selected-label probability, input tokens and elapsed time.
- Overall and per-route agreement; premature `deliver` selections.
- Coverage and agreement above a diagnostic probability threshold (default 0.9).
- Aggregate input-token reduction and median/p95 inference time per profile.

The threshold never grants permission to act and has not been calibrated. A
high-confidence mistake remains a mistake. Choosing to abstain trades coverage
for accuracy; the report preserves both, including `null` accepted accuracy when
no predictions meet the threshold.

## Interpreting token efficiency

These are **Laya encoder input tokens**, not Codex tokens, API billing savings or
the cost of delivering a change. A shorter prompt can change a model's answers.
Compare accuracy, per-route errors and premature delivery alongside token counts.
Do not describe a token reduction as an improvement when it removes needed
reasoning or increases rework.

These cases are public development fixtures, not a hidden evaluation set. They
contain short, preinterpreted evidence, so they do not test code understanding,
evidence gathering, full prompt-injection resistance, or real delivery quality.
Single-run CPU timings are descriptive, not throughput claims. The typed checkpoint
was trained for other workflows; its name does not establish fitness for this one.

Before proposing a live adapter, evaluate untouched maintainer-reviewed cases,
include simple rules and the existing agent as baselines, and account for the
router plus evidence gathering, fallbacks, retries and review corrections. The
acceptance measure should be total resources per successfully delivered change.

References: [Laya 0.3.21](https://github.com/NandhaKishorM/laya/releases/tag/v0.3.21),
[pinned sequence builder](https://github.com/NandhaKishorM/laya/blob/9d955671415fc19f069b9cc998928075c1f255ec/laya/common.py).
