# Budgeting: every place a spend ceiling can live

An unattended factory spends tokens on every lap, on a server that never sleeps,
with nobody watching. The consumer now enforces **lap-count ceilings** on scheduled
launches. Everything else that could burn money — token counts, dollars — is listed
below with the exact place it would have to be enforced, because most of it is not
enforceable from this repository.

**The default is unlimited.** No `budget` key in `schedule.json` means no ceiling of
any kind, exactly as before this document existed.

---

## What is enforced here

`.factory/schedule.json` gains an optional `budget` object:

```json
{
  "workflow": "archon-loop",
  "inputs": {},
  "budget": { "max_laps_per_hour": 2, "max_laps_per_day": 20 }
}
```

| Key | Window (rolling) | Blocks the tick when |
|---|---|---|
| `max_laps_per_hour` | last 60 minutes | laps started by this workflow in the window >= the value |
| `max_laps_per_day` | last 24 hours | same, over 24 hours |

Semantics, precisely:

- **Unlimited by default.** Absent `budget`, an empty object, `null`, or a key set
  to `"unlimited"` all mean no ceiling. Values must be whole numbers >= 1.
- **Rolling windows, not calendar.** "Per day" counts back 24 hours from now, so
  there is no midnight reset and no timezone involved.
- **Per scheduled workflow.** Counting matches everything else in the trigger
  binding (slots, capacity, the launch gate): only runs of the workflow named in
  `schedule.json` count. A second scheduled workflow needs its own budget.
- **Enforced before anything can launch.** The check runs first in
  `launch_decision`, ahead of every fail-open path: a board that cannot be read
  still launches *unless* the budget says stop. The budget is the one control that
  fails closed, because overspend is the one failure a sleepless machine cannot
  walk back.
- **Fail-closed on blindness.** Budget configured + the run list unreadable =
  no launch until it is readable. A malformed `budget` object also refuses to
  launch and names the key.
- **Self-clearing.** There is no brake file to forget: each tick recounts recent
  laps, and when the window rolls past older laps, launches resume on their own.
  (`halt` remains the manual, permanent brake; `pause` remains launch-only.)
- **Bookkeeping laps count.** An idle-board bookkeeping lap is still a lap that
  costs tokens, so it is budgeted too.
- **Wakes are free.** Resuming a run paused on CI or a usage limit is not a new
  lap and is never budget-blocked.
- **Manual `factory run` is exempt by design.** An explicit launch is the
  operator's own act, like `pause` and `halt` it is not second-guessed. See the
  placement list below for where to add that if a team machine needs it.
- **Counting is bounded by the run list.** The tick reads the most recent 100
  runs (`workflow runs --limit 100`) — with more than 100 runs inside a window
  the count is a lower bound. Keep ceilings well under 100/day, or raise the
  limit where the runs are fetched in `launch_decision`.

### Where the enforcement lives in code

| Piece | Location | What it does |
|---|---|---|
| `budget_windows()` | `factory/consumer.py` | Parses/validates `budget`; raises on malformed config |
| `budget_exceeded()` | `factory/consumer.py` | Counts recent laps against each ceiling, returns the refusal reason |
| `unreadable_times()` | `factory/consumer.py` | Warns when run timestamps stop parsing, so the counters cannot go silently blind |
| `launch_decision()` | `factory/consumer.py` | The enforcement point: budget first, fail-closed, then the launch gate |
| `trigger_config()` | `factory/consumer.py` | `schedule install` refuses a malformed budget before writing the binding |
| `schedule()` install | `factory/consumer.py` | Prints the resolved budget (`Budget: unlimited ...` or the ceilings) |
| `.factory/gate.json` | written by the tick | `last_budget_block` timestamp for observability |
| `.factory/schedule.log` | written by the tick | Every budget refusal line, with the key and the count |
| Tests | `bin/test_consumer.py` | `LaunchGateTests` budget cases; `ConsumerTests.test_schedule_install_validates_and_reports_the_budget` |

A tick that refuses to launch looks like this in the log:

```text
No launch: the last 24h started 20 lap(s) of this workflow, at or over
budget.max_laps_per_day=20; scheduled launches resume on their own when the window
rolls past older laps.
```

---

## Ceilings that already existed (related knobs)

These are not new, but they are part of the same spend story:

- `parallel` (1–8) in `schedule.json` — the concurrency budget: how many laps at once.
- `idle_pick_minutes` — how often a drained board still gets a bookkeeping lap.
- Empty-lap suppression (`EMPTY_LAP_SECONDS` + the board signature) — stops buying
  identical empty picks on an unchanged board.
- Runtime host `timeout_s` (max 300) — per runtime attempt.
- `harness/ci.py` rung timeout (600s) — per static/unit rung.
- `STUCK_AFTER_SECONDS` sweep — abandons launches that never started, freeing slots.

## Where budgeting still needs to be placed

Not enforceable from this repository today. Each entry names the owner and the
hook point, so it can be wired when that side grows the capability:

1. **Manual `factory run` launches** — *this repo, `consumer.invoke()`*, in the
   `action == "run"` branch after the STOP check. Currently exempt on purpose:
   the operator typed it. If a shared machine needs manual launches counted,
   apply `budget_windows()`/`budget_exceeded()` there against the same run list
   before launching.
2. **Token and dollar budgets** — *Archon engine (upstream)*. The consumer never
   sees token usage; only the engine does. A monetary ceiling needs, in Archon:
   (a) a cost/usage field per run at settle time, (b) that field visible through
   `workflow runs`, and (c) ideally an engine-side input (e.g. `max_spend_per_day`)
   that holds runs the way provider usage limits already pause them. The factory
   would then only read and report it — the same shape as `required_checks`.
3. **Spend reporting and escalation** — *`archon-manager`*. It already records
   metrics on a slower schedule; per-day spend, lap counts, and budget-refusal
   counts belong in that report, with an ask-the-owner escalation when spend
   trends past a declared comfort level.
4. **Provider hard caps** — *Anthropic/OpenAI console spend limits*. The only
   true monetary last resort. Set one regardless of everything above: provider
   rate limits already pause runs (the wake phase resumes them), and a console
   spend cap is the one ceiling no framework bug can bypass.
5. **Merge and deploy side** — *Archon `archon-merge-queue` / `archon-deploy`*.
   Batch sizes and deployment retries consume CI and provider quota; if that
   ever needs a ceiling it belongs in those workflows' inputs, not here.
6. **Per-slot wall-clock** — *`factory/runtime_host.py`, `Environments.start`*.
   Runtime attempts are bounded by `timeout_s` (<= 300s) per command, but there
   is no total budget per slot; a chatty verification loop inside one attempt is
   bounded only by the HTTP request timeout of 310s.
7. **OS-level brakes** — *operator*. `halt`, `pause`, and removing the timer
   (`schedule remove --apply`) remain the manual stops; none of them are
   budget-aware and none need to be.

## Observing what was spent

- `.factory/schedule.log` — every launch decision, including budget refusals.
- `.factory/gate.json` — `last_launch`, `last_launch_board`, `last_budget_block`.
- `archon-manager` metrics runs — the intended home for aggregated spend.
- The provider console — the ground truth for money.
