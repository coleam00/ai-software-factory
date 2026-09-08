# What comes from Archon's `sdlc` pack, and what is still this factory's

This repository used to carry five workflows and four judgment prompts of its own. It
carries none. Every AI step -- and every generic decision around one -- is the pack's,
dispatched by `factory/sdlc.py`, which supplies the trusted inputs and applies what
comes back.

That is a reversal of what this file said a version ago, and the reason is worth
recording, because the earlier refusals were correct about the pack as it then was. The
pack now answers each of them in its own terms, upstream, where one implementation
serves every consumer instead of each consumer keeping its own.

## Dispatched

| Factory action | Pack workflow | What the factory supplies | What it consumes |
|---|---|---|---|
| `triage` | `archon-admit` | `MISSION.md` and `FACTORY_RULES.md` read out of the base tree as trusted policy; the issue body as evidence | `disposition`, `priority`, `assumptions`, `rules_cited` |
| `implement` | `archon-ship` | the issue plus its recorded work order, and a publication policy naming `fixed_gate.py --publication` | `outcome`, and a `pr` identity it re-resolves before acting |
| `validate` | `archon-accept` | the original work order from outside every checkout as LF bytes, and a strict profile pinning `fixed_gate.py`, declaring what it covers, carrying `MISSION.md` and `FACTORY_RULES.md` from the base commit, and requiring a report back from the gate | an `acceptance.json` receipt, identity-checked and re-hashed |
| `fix` | `archon-revise-pr` | the same work order and the findings out of that receipt | the same pull request, repaired, never a replacement |
| `regress` | `archon-regress` | a trusted check profile running `fixed_gate.py --regression`, and a `public_probe_scope` input that is empty unless the operator set one | `clean` / `defects` / `inconclusive` against a named revision |
| `merge` | `archon-merge` | an authorization rewritten immediately before every read of it | `merged` / `held` / `revalidation_required` / `failed` |

`archon-ship` composes `archon-triage`, `archon-investigate`, `archon-plan`,
`archon-implement`, `archon-review` and `archon-pr` internally, so the four workflows
this factory used to compose by hand arrive inside one governed run with one artifact
store.

## What is still ours, and why the pack cannot own it

The state machine, the dial, the caps, the trusted project profile, the escalation
channel. Not because the pack does them badly -- because they are not general.

**The labels are a state machine and only this side knows the table.** `archon-admit`
returns `deferred`; what `deferred` means, which states it may be reached from, and
that no node may ever move an item out of `needs-human` are facts about this factory.
Every transition goes through `factory/state.py`, which refuses at the write.

**The gate is one fixed command and the pack must not choose it.** `archon-accept`'s
generic mode discovers a project's checks, which is the right primitive for a delivery
with a human at the pull request and the wrong one for a gate: it puts the choice of
what counts as validation inside the thing being validated. So the factory passes a
strict profile pinning one argv -- `factory/fixed_gate.py` -- and the pack runs it and
records its streams without interpreting them. `REQUIRED_MARKERS`, the ratchet, the
mutation score and the guard are all inside that command.

**A private gate still owes the judge something public.** A fixed profile withholds
argv and streams, which is what lets that command exercise a holdout no builder may
read -- and leaves an approval resting on "a command the operator vouched for exited
zero". A judge told only that is right to answer `inconclusive`. So the profile also
declares that this one command is the whole applicable gate, describes in public terms
what it covers, and registers one piece of required evidence:
`.factory/acceptance-report.json`, which the gate writes into the candidate checkout
bound to the evaluation's own id and identity. It carries the gate's status, which
required markers reported and the counts measured. It carries no failure text, no
holdout content, no evaluator path and not the command itself, and acceptance refuses
it outright if the path was tracked at the candidate SHA or already present in the
tree -- so it can only be output that evaluation produced. Publication mode writes no
report at all: nothing is judging a receipt then, and a file dropped into a delivery
worktree is a file that can be committed.

**That command is rebuilt from the base tree, every run.** `sdlc.snapshot` reads
`config.py`, `guard.py`, `gate.py`, `tripwire.py`, `fixed_gate.py` and the ratchet floor
out of the base revision the pull request targets -- never out of the candidate, never
out of a possibly-dirty operator checkout -- and refuses when any of them is missing.
A pull request cannot supply the judge that judges it, structurally.

**Nobody is at the pull request, so the correction loop has to be cold.**
`archon-deliver`'s bounded correction runs in the same run as the review it answers, so
a repair inherits the reviewer's reasoning and the next review judges a tree built with
its own previous opinion in context. `archon-revise-pr` is a separate dispatch: a fresh
clone, an engine-created worktree, no artifacts from the run that built it, and the
findings arriving as a file rather than as ambient memory. That is the property the
factory used to keep by writing its own fix workflow, and it is now upstream's.

**A candidate that cannot merge has to stop, not orbit.** `archon-merge` returns
`revalidation_required` both when the pull request has moved since the receipt was
issued and when it has not moved and its head still does not contain the base. Those
need opposite answers. Requeueing the first is right; requeueing the second asks
acceptance to judge the same head again, and acceptance judges a head -- it never
updates one -- so the approval and the ancestry refusal alternate for as many laps as
the operator is paying for. The consumer tells them apart by re-resolving the candidate
rather than by reading the refusal's prose, and holds the second for a person with the
one command that clears it: `gh pr update-branch`, an ordinary fast-forward of the head
branch, never a force-push. Bringing a candidate onto its base is not something this
factory dispatches.

**The merge's authority is a receipt, not a caller.** `archon-merge` reads an
operator-owned policy file and rereads it, and the stop path, in the instant before it
mutates. The factory revokes that file's authorization before rewriting it, so a crash
between the two writes leaves a denial. What it grants depends on the dial, the pull
request's own live state, and whether any assumption is still unagreed.

## What this migration cost, stated plainly

**The holdout's read barrier is no longer enforced by this factory.**
`.factory/holdout/**` is on the guard's protected list, so nothing can edit it. Reading
used to be blocked by a `denied_tools` list the factory attached to its own `include:`
of the review pack. There is no `include:` here now, and nothing in an input, a policy
file or a workflow argument lets a caller set a tool policy inside a workflow it did
not write. Every node `archon-ship` expands into grants `Read`, and none of them knows
this factory has a holdout.

So a builder can read the scenarios it will be judged on, and a builder that can see
the answer key writes to it -- with every check still green, which is the exact shape
of failure the holdout exists to make impossible. `factory doctor` reports this and
blocks level 3 until `FACTORY_HOLDOUT_DENY` records how the operator arranged the
barrier elsewhere: an agent-level deny list, a provider policy, a checkout the builder
does not get. That setting records a claim and names who made it. It verifies nothing,
and on a provider that cannot enforce a tool restriction at all -- Codex, today -- there
is nothing for it to verify.

**The scheduled regression diagnoses but does not file.** `archon-regress` will publish
an issue only for a case its trusted check profile handed it, already approved for
export, with a cause proven by `archon-investigate`. That is the right rule: it stops
private evaluator output from being copied into a public issue by a model that thought
it looked like a bug report. The shipped `fixed_gate.py --regression` reports whether
the base branch is green and authors no public cases, because it cannot state a root
cause without inventing one. So on its own, at level 4, a red regression escalates to a
person rather than filing.

`FACTORY_PUBLIC_PROBE_SCOPE` is the second route, and it ships empty. Setting it is the
operator's statement that the checks it names, and everything they print, are public
developer material with no private evaluator source in reach -- which the factory
cannot verify, because it does not know what those commands emit. The private gate
still runs first and still decides; only after it comes back non-clean does the
workflow re-run that public scope through its own recorder, and only what the probe
itself proves can become an issue. Nothing the private check produced crosses over, and
a green probe never makes a failed gate green. Publication also turns on when a
project's own check emits `public_cases`.

**Neither worktrees nor fresh contexts are a sandbox.** They never were, and nothing in
this migration changed it; what changed is that the claim is now written down in three
places rather than assumed. `FACTORY_REQUIRE_ISOLATION` refuses to deliver rather than
pretending, and `archon-accept` returns inconclusive before running anything when a
profile asks for an isolation it cannot attest.

## Still a gap

**`archon-upkeep`.** Ground a dependency update against the repository, then either stop
with the reason or take the bump through the full reviewed tail. This factory has no
dependency-update path at all, which is a real hole in something meant to run
unattended for months: the regression will eventually go red on an advisory nobody is
watching. The shape is a seventh action scheduled like `regress`, consuming
`archon-upkeep` the way `implement` consumes `archon-ship`. Not built.
