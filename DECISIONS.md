# Decisions

The factory's key decisions, newest first. Append-only: a decision that is later reversed gets a
new entry that says so; old entries are never edited. Each entry has the same shape so the
mission-control dashboard can read it:

```
## YYYY-MM-DD Title in a few words
Decision: what was decided.
Why: the problem or evidence that forced it.
Replaces: what it replaced (or "nothing").
Version: the Archon pin it shipped in (or "none").
```

Pin commits carry the per-change reason; this file holds the larger calls.

## 2026-10-26 A lap runs only the holdouts its work item exercises
Decision: At normal depth the holdout verifier gets the contract's criteria and runs only the scenarios whose `Touches` line names a behaviour those criteria create or change; when unsure it skips and names the scenario. Deep keeps every scenario the changed files could affect. The manager still runs every scenario on the default branch each pass. Still open (plan with Cole): each verifier run writes its game-driving scripts from scratch (one lap wrote 160), so a run scenario costs about 9 minutes; a maintained script per scenario, kept where only the holdout agents can read it, would make each one a command.
Why: North Star's holdouts grew from 9 to 44 in ten days (added, never removed), and "when unsure, run it" made a normal lap on the HUD change run 16 of them: 147 of the app check's 190 minutes. Laps took 2 to 4 hours and nothing merged for a day.
Replaces: "When unsure whether a scenario is in scope, run it", and scope given as the changed files only.
Version: a8551bfe

## 2026-10-26 OPEN: the manager's schedule, health duty and config (plan with Cole soon)
Decision: Not decided yet; Cole asked for it to be planned with him soon and not built tonight. The manager is already a separate process on its own timer (North Star: a scheduled task every 3 h, `manager_live.py`). What broke is everything around that timer: a pass now runs 3 to 5.5 h (holdouts, every committed check, playtesting), a fire that lands while a pass still runs is skipped, and the manager looks at run health only at the start of a pass, so a dead lap waited up to a whole pass. Its rule for a running-but-stuck lap is also report-only, after 3 h. To decide: (1) split run health out of the pass into something short that runs every few minutes (the scheduler's silent-lap sweep, 2026-10-26, is the first piece), so a pass only reports; (2) cap or split the pass so the interval in the docs is the interval in practice; (3) make the interval and pass scope factory config (in `.factory/`, not in a project's runtime scripts) so every factory sets them the same way; (4) whether the manager's games should share a lap's game budget. Related: the Bun incident in `docs/incidents.md` (2026-10-25), where the manager saw the fault on its first pass and wrote it down, and nothing stopped launches.
Why: 2026-10-25: a lap died at 18:03 UTC while a 5.5 h manager pass ran; the next pass freed the slot at 23:16. Five hours with no lap and no merge.
Replaces: nothing yet.
Version: none

## 2026-10-26 The scheduler abandons a lap whose log went silent
Decision: Each scheduler tick abandons a `running` lap whose log has not been written for 60 minutes. Abandoning removes the worktree but keeps the run's branch, so committed work survives for the next lap (corrected 2026-10-26: the first wording said the worktree was kept).
Why: A lap whose review step crashed stayed `running` and held North Star's only slot for five hours. Over 150 recent runs the longest healthy silence in a lap was 29.5 minutes.
Replaces: Only runs that never started were abandoned by the scheduler; everything else waited for the manager.
Version: none (factory consumer 33173fa1)

## 2026-10-24 The complex route runs dev's archon-ship-block
Decision: The loop's complex route includes Archon dev's `archon-ship-block` (triage, investigate or plan, delivery rounds with one CI agent, review threads handled as work, delta reviews) instead of the factory's 10/7 fork of ship and deliver. The forked workflows and lifecycle are removed.
Why: The fork's script gates stopped good work at least 15 times and never caught a real bug, and the branch had drifted 650 commits from dev. Merge step 1 of bringing the two factories together.
Replaces: The forked archon-ship, archon-deliver, archon-review, archon-triage and archon-lifecycle.
Version: 29edfa995

## 2026-10-24 Holdouts live in the repository, behind a hook
Decision: Holdout scenarios live in `.factory/holdout/HOLDOUT.md`, committed so a team shares them. A hook (`.factory/hooks/holdout_fence.py`) refuses every agent except the `holdout-verifier` and `holdout-writer` subagents any call that would show it a scenario. The app check, reviewer and manager never read holdouts themselves.
Why: The 10/24 audit found builder steps reading North Star's holdout judges and baseline, which sat beside the runtime file every agent reads, with nothing enforcing the line.
Replaces: Holdout files kept outside the repository, read directly by the app check and the manager.
Version: 29edfa995

## 2026-10-24 No Archon engine changes for the factory
Decision: Factory needs are met with workflow files on the pinned branch. Where the pinned engine lacks something, the workflow works around it (inline subagents read their instructions from a file a script copies into the run).
Why: Engine changes on the pin branch broke twice on 10/17 and make every rebase onto dev harder.
Replaces: Engine fixes carried on `factory/sdlc-on-dev`.
Version: none

## 2026-10-21 Checking proportional to risk
Decision: Pick chooses a check depth (light, normal, deep) per item; a lap checks only what its change does and what it broke; exploring the product and running every holdout belong to the manager's pass on the default branch.
Why: Laps took 3.7 lap-hours per merge, the app check was 44% of lap time, and only about 12% of lap time was product work. Throughput had fallen to 0.3 merges per hour against a bar of 1.
Replaces: Maximal checking on every lap ("when unsure, test more").
Version: 63b79ad8

## 2026-10-18 Simplify the audit fixes
Decision: Keep evidence-checked gates, but reverted engine flags, moved the Postgres fix upstream, made the app check return structured lines, and froze the consumer (no new logic; retire it into Archon triggers).
Why: The 10/17 fixes added about 1,200 lines, guessed at file paths in parsers, and overfit one factory, while the SDLC pack serves more than the factory.
Replaces: The 10/17 audit implementation.
Version: 9155ff15

## 2026-10-17 Gates that hold on evidence
Decision: Every app-check line cites a file written during the check; a script verifies the evidence; a deterministic merge gate lands only what the record supports.
Why: The audit found 5 of 12 escaped defects had been seen before merge and graded PASS, about 10% of PASS lines had no evidence, and the loop reviewer had sent back 0 of 37 pull requests.
Replaces: The reviewer merging on its own word.
Version: 292c90dd

## 2026-10-14 Parallel laps with claims
Decision: Up to N laps run at once; each claims its work item with a GitHub comment and the oldest live claim holds it.
Why: One lap at a time left the factory idle while checks ran.
Replaces: One lap at a time.
Version: 02c96342

## 2026-10-13 The hybrid loop
Decision: One loop picks each move and routes it: continue an open pull request, build a simple item in the loop, or take a complex item through the shipping pipeline. A black-box app check and a white-box reviewer judge it; a manager runs above the loop every few hours.
Why: The simple loop was fast but its reviewer almost never sent work back; complex and brownfield work needed the full pipeline.
Replaces: The simple loop alone.
Version: 4dc0eb73

## 2026-10-12 Agents over deterministic scripts
Decision: A simple agent-driven loop (builder and reviewer) and a manager, with no gate scripts auditing agent output.
Why: One lap was 140 nodes, 86 of them deterministic; at least 15 false stops came from scripts auditing agents, and none caught a real defect.
Replaces: The 140-node lifecycle and the Stage 1 re-drive scripts.
Version: 855a1a2d

## 2026-10-07 Archon runs everything
Decision: No coding agent runs outside an Archon workflow. The factory is a thin wrapper: it owns the context documents, setup, the README, and invoking and displaying Archon. Missing capabilities are built in Archon, never in factory code.
Why: An agent proposed keeping the loop in factory Python; that path had already produced a second, divergent orchestrator.
Replaces: Factory-owned Python orchestration.
Version: none
