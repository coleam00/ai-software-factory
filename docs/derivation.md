# What comes from Archon's `sdlc` pack, and what does not

The pack ships ten workflows inside the engine binary. Four of them cover every AI step
in this factory, composed by `include:` at five sites, and it writes no prompt for any
of those steps. The rest are listed
here with the reason, because "we did not use it" and "it does not fit" are different
answers and only one of them is worth trusting.

Nothing here is a criticism of the pack. Four of the five refusals below are refusals
of the *same* good design: the pack keeps a human at the pull request, and this
factory's entire premise is that nobody is there.

## Composed in

| Factory step | Pack workflow | What arrived with it |
|---|---|---|
| `factory-implement` → plan | `archon-plan` | Grounds itself against the repo, writes `plan.md`, returns `ready`, refuses to mutate the checkout. Deleted our plan prompt **and** the `prime` node that existed only to feed it. |
| `factory-implement` → implement | `archon-implement` | Loops until done (max 5), returns `{done, green, red_cause, summary}`, ends in a deterministic guard that fails a node which declined its task. |
| `factory-fix` → fix | `archon-implement` | Addressing findings is implementing. A second prompt meant the correction path had no loop and no green verdict while the build path had both. |
| `factory-implement` → review | `archon-review` | Six parallel lenses, each finding carrying the lens that raised it. |
| `factory-regress` → root cause | `archon-investigate` | A *proven* causal chain rather than the first plausible explanation, engine-enforced as advisory. |

Four prompts deleted. What remains ours is the part the pack has no opinion about:
the state machine, the guard, the gate, the merge, the holdout, the ratchet, the
dispatcher, and the scripts between the AI steps.

Also adopted, and not workflows: `include:`-carried `denied_tools` (contributed
upstream for this), `fixtures/*.stubs.yaml`, node-level `mutates_checkout: false`,
`requires: [github]`, and the pack's habit of a deterministic assertion after every
advisory node.

## Not composed in

**`archon-deliver` — the one that looks closest, and cannot be used.**
Implement, gate, PR, review, a bounded correction loop, validate, wait for CI, flip
ready. That is the same *sequence* this factory runs, and adopting it would collapse
the property the factory exists to have. The correction loop runs in the same run as
the review it answers, so a fix inherits the reviewer's reasoning; the next review
then judges a tree built with its own previous opinion in context. This factory
dispatches `factory-fix` as a **separate run, separate worktree, separate context**,
and the findings reach it as a file on disk rather than as ambient memory. Deliver's
human gate is PR review on GitHub — correct for a team, and it is precisely the step
that does not exist here.

**`archon-ship`** is `archon-triage` plus routing plus `archon-deliver`. Same refusal,
one level up.

**`archon-validate` — a model decides which checks to run.**
"Discover and run the project's own checks" is the right primitive for a human-in-the-
loop delivery. It is the wrong one for a gate. `config.VALIDATE_CMD` is one fixed
command printing a fixed set of markers, and `REQUIRED_MARKERS` refuses a merge when
any of them is absent — because a check that never ran produces no failures, and
"did anything fail?" reads that as success. A discovery step puts the choice of what
counts as validation inside the thing being validated.

**`archon-pr` — an AI node opens the pull request.**
Same rule as the merge: a node holding `gh pr create` can open a PR against any branch
it likes, including one nothing validated. `open-pr.py` pushes and opens from the
record node's file, applies the label the validator's state machine reads, and reads
the PR back to confirm the body landed — the pack's own read-back discipline, already
present, done by a script.

**`archon-triage` — the same word, a different job.**
It routes to investigate / plan / deliver / no_action after grounding itself against
the repository. `factory-triage` emits accepted / deferred / rejected / needs-human
plus a priority, an area and the rules it cited, which is what drives a GitHub label
state machine, and it runs with `allowed_tools: []` so the disposition is traceable to
`MISSION.md` and `FACTORY_RULES.md` rather than to something it found on the way past.
The pack's version is better at its question. It is not this question.

**`archon-upkeep` — not a refusal. A gap.**
Ground a dependency update against the repo, then either stop with the reason or take
the bump through the full reviewed tail. This factory has **no dependency-update path
at all**, which is a real hole in something meant to run unattended for months: the
regression will eventually go red on an advisory nobody is watching. The obvious shape
is a sixth workflow, `factory-upkeep`, scheduled like `factory-regress`, composing
`archon-upkeep`'s assessment stage and landing the result through this factory's own
implement/validate path rather than the pack's delivery tail. Not built yet.

## The one thing that made any of this possible

Every node the pack expands into grants `Read`, and none of them knows this factory has
a holdout. Before `include:` could carry `denied_tools`, composing any pack workflow
meant a reviewer that could quote a holdout assertion into a report the fix node then
reads — with every check still green. That primitive was added to Archon for this, and
`factory doctor` asks the **engine** whether it kept the field rather than trusting a
version string, because a dropped field is silent and a version string tests what the
binary claims.

Last verified across the expanded graphs of all five workflows: **16 AI nodes, all
walled by the holdout deny or sealed with `allowed_tools: []`, zero gaps.**
