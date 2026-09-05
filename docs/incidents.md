# Incidents

Every rule in this repository exists because something broke. This is the list, so
that a factory rebuilt from the design alone does not rediscover all of it,
unattended, in production.

Read it if you are about to "simplify" something. Most of what looks like paranoia
here is a scar.

---

## Found while building this factory

### The merge that armed a revert in your checkout, every single time

**The worst one here, and it left no error behind.**

**What happened.** After an unattended merge, `git status` in the main checkout showed
the merged work as **staged deletions**. The index and the working tree still held the
commit *before* the merge, while `HEAD` had moved to the merge itself. Any `git commit`
in that state -- by a human, for any reason -- commits a revert of what just landed.

It did. A `git add -A && git commit` 74 seconds after a merge wiped a feature and 106
lines of its tests. The push succeeded. Nothing failed. I diagnosed it as my own bad
habit, wrote it up that way, and only found the real cause when the *same desync*
appeared after the next merge -- with my hands nowhere near it.

**The cause.**

```python
if current == BASE_BRANCH:  git("merge", "--ff-only", ...)   # safe, almost never taken
else:                       git("update-ref", "refs/heads/main", "origin/main")
```

`update-ref` moves a branch pointer and touches neither the index nor the working tree.
When nothing has that branch checked out, that is exactly right. **The main checkout
always has the base branch checked out.** And `merge.py` runs from a validation
worktree, where the current branch is the validation branch -- so the `else` was taken
on *every* merge, and the safe branch above essentially never ran.

**The rule.** Ask who has the branch checked out (`git worktree list --porcelain`,
which is the only source that knows about the checkout this process is not running
in), and fast-forward it **in its own checkout** so ref, index and files move together.
`git merge --ff-only` refuses when that tree has local changes, and refusing is the
right answer: leaving somebody's edits alone is strictly better than desynchronising
their repository behind their back. `update-ref` only when the branch is checked out
nowhere.

**Two things generalise.**

First: **a habit that triggers a trap is not the cause of the trap.** The first write-up
of this blamed `git add -A` and shipped a doctor check for a stale checkout. That check
is worth having, but it was treating a symptom, and it would have gone on treating it.

Second: unknown takes the safe path. If the worktree list cannot be read,
`worktree_holding` returns the main checkout rather than `""` -- because being wrong
that way prints a note, and being wrong the other way silently arms a revert.



These are from the build itself -- the first laps against a real repository, a real
GitHub, and a real workflow engine.

### The node that was blamed for a bug two nodes upstream

**What happened.** `gate-plan` failed with

```
'$preflight.output.target' references field 'target', but node 'preflight's output
is not a JSON object
```

`preflight` had run, done its job correctly, and exited 0. It also printed one
friendly line -- `PREFLIGHT_OK secrets_ignored=4` -- before its JSON payload, which
made the payload unparseable. The error surfaced on the *consumer*, so the first place
anyone looks is the file that is not broken.

**What made it worse.** The obvious fix -- rewrite every `print()` in `preflight.py` --
did not work, and the reason is the interesting half: **the polluting line was not in
the script.** It came from `guard.preflight()`, a library function doing exactly the
right thing, printing a marker the gate greps when the guard runs as a CLI. Two
callers, two correct-but-incompatible meanings for one stream.

**The rule.** `factory/nodeio.py`. Importing it redirects `sys.stdout` to `sys.stderr`
for the whole process; `emit()` writes to the real stdout captured at import. Every
library a node imports is then safe by construction, **including ones written later by
someone who has never read that file.** A convention you have to remember is a
convention that fails on the day you are busy.

### The fix that was applied twice and never landed

**What happened.** After fixing the above, the same failure reproduced exactly. The
files on disk looked right. `git status` said clean.

`cp -r src/. dst/` on Git Bash for Windows reports success and leaves the destination
untouched. Two rounds of a genuine fix were lost to a copy that said it had worked.

**The rule.** `bin/sync-to.py` compares content, copies only what differs, and prints
what it changed. A sync that did nothing says so. Never trust a copy you did not
verify -- especially the one that carries a fix into the place the fix is needed.

### The mutation that escaped, and the property that was too weak

**What happened.** The mutation set went 9/10. The escapee made the store return only
the *first* expense of a group (`LIMIT 1`), and it sailed through a holdout scenario
that recorded five expenses and asserted the balances summed to zero.

Because **one expense's balances sum to zero exactly as five do.** "Sums to zero" is a
property that holds for any *subset*, so a check that only asserts the property cannot
see work disappearing.

**The rule.** The holdout asserts the exact hand-computed figures, not the property.
Numbers derived independently, on paper, from the inputs -- which is what makes it a
specification rather than a mirror of the implementation. And the mutation runner is
what found it: no amount of reading those assertions would have.

### Every rung green, one rung never shown to fail

**What happened.** The first complete mutation set scored 10/10 with six defects caught
`by unit`, one `by holdout`, one at app start -- and **zero by the end-to-end rung.**
The number read as "the gate can fail". All it meant was "the unit suite can fail".

`ci.py` stops at the first red rung, so a defect that any earlier rung also catches
never exercises the later ones.

**The rule.** `defects.json` documents the intended rung per defect, the runner prints
which rung caught each one, and the file says plainly: if the column collapses onto
one rung, the set has stopped measuring what it was built to measure. A defect was
added aimed only at HTTP status semantics, which no unit test goes near.

### The gap that was stated instead of faked

Tally has a screen, and its mutation set has no *presentation* defect -- a colour that
stops tracking state, an element that stays visible, a width pinned rather than
derived.

That rung does not exist, because the harness drives HTTP and not a browser. A defect
aimed at it would report ESCAPED and be telling the truth.

**The rule.** The gap is written down in `defects.json` rather than papered over.
Adding a browser rung is the work that closes it; a defect nothing can catch is worse
than the gap, because it turns an honest zero into a false alarm.

### A database that could not be locked, and a test suite that could not be trusted

**What happened.** Twenty-seven passing tests, fifteen errors on teardown:
`PermissionError: the process cannot access the file because it is being used by
another process`. `sqlite3`'s connection context manager commits or rolls back and
does **not** close the handle. On Linux nothing notices.

**The rule.** Wrap it. And note what the failure looked like: a permission error naming
a temp path, which explains nothing about connection lifetimes.

### The suite that could not run where it had to run

**What happened.** The global Python had a broken plugin registered with pytest, so
`python -m pytest` failed at import -- in a repository with no pytest dependency of its
own.

The deeper problem was the one that decided the design: **the mutation runner copies
the project into a throwaway directory and runs the whole gate there with nothing but
the interpreter.** A suite that needs a project virtualenv cannot run in that copy --
and a mutation that could not run gets reported as a defect that escaped, which is the
most misleading number this system can produce.

**The rule.** Tally is stdlib-only and uses `unittest`, and `MISSION.md` makes that an
invariant with the reason attached rather than a preference.

---

### The empty answer that was read as a negative answer

**What happened.** The factory found its own regression, filed issue #6 about it,
triaged it critical, and dispatched an implement lap. Seventy-five seconds later the
next tick escalated that issue to `needs-human`:

```
ESCALATE gh:issue:6: left in 'in-progress' with no PR record and no run holding it;
                     an implement lap died before it opened one
```

The lap had not died. It ran for another twenty minutes and finished.

**The cause.** A detached run outlives the process that launched it, so the lock it
holds cannot be released by that process, and its PID proves nothing -- it exits
immediately. `release_settled_locks()` therefore asked the engine what was still
running and matched the answer against each lock. It matched on **branch name**, and
the engine populates no branch in that payload. Every entry came back blank, the
blanks were filtered out as junk, and each lock was compared against an empty set.
`any()` over an empty set is `False`, so the conclusion was "no run holds this" -- for
every lock, one tick after it was taken, unconditionally.

The reconcile sweep then did its job perfectly on a false premise: an item
`in-progress`, no PR, no lock. That is exactly what a died lap looks like.

Worth noticing what this cost *before* it was understood: it also produced
`could not move gh:pr:5 to 'validating'` earlier in the same build -- two validations
racing for one PR, because the lock meant to stop that had already been dropped. One
bug, two symptoms, and neither of them looked like a locking problem.

**The rule.** **An empty answer is not a negative answer.** This is the same
"empty is not pass" the gate is built around, and it is easier to violate in the
machinery than in the harness, because nothing here has a marker to count. Every
unknown must keep the lock:

- The run list came back unreadable, empty, or with no usable ids → **keep**.
- This run id is not in the reported window → **keep**. Absence from a truncated list
  is not evidence a run ended.
- The status is one this engine has never been seen to emit → **keep**. Unknown means
  still running.
- Only a run id the engine positively reports as `completed` / `failed` / `cancelled`
  releases anything.

The costs are not symmetric, and that is the whole argument: a lock held too long
stalls one target until the age reaper frees it, and a lock dropped too early runs two
writers over one worktree and escalates work that was going fine.

**And match on an identifier both sides agree on.** The branch was never a shared
identifier; it was a name this code invented and hoped the engine would echo back. The
run id is printed by the dispatch and recorded on the lock, so the question asked is
about the same object the answer is about.

### The hold that was only a sentence

**What happened.** A lap ran cleanly and unattended: issue triaged, PR opened, gate
green, merged, issue closed. Reading the PR afterwards, the gate's own comment said:

```
## Factory Gate: PASS, merge HELD
Auto-merge is held because: ratchet slack (unit_tests+10); 63 recorded assumption(s).
```

It was merged forty-five seconds after that comment was posted.

**The cause.** The hold was a sentence. `gate.py` computed `automerge = False`,
composed a careful explanation, posted it -- and then set the PR's state to `passed`,
because that is what a PR that passed every check is called. The dispatcher reads
states, not prose. `passed` is exactly what it merges.

**The most subtle gate in the system was defeated by the most obvious one**, and the
result looked like a clean unattended lap. Nothing errored, nothing escalated, and the
only trace was a comment nobody had to read.

Worth noting how it survived earlier testing: every previous hold happened while a
human was driving the workflows by hand. The hold was never once put in front of a
running dispatcher until the factory was left alone.

**The rule.** A hold is a **state**, not a message. `held` has its own label, is
reachable only from `validating`, and leaves only through `open` -- which no node may
do, so a human raising the floor or accepting the assumptions is the single way
forward. `next_action` has no branch for it, so the factory carries on with other work
and the PR waits, which is the whole intent: a hold does not stop the factory, it stops
that merge.

And it gets its own line in `factory status`. A hold is the one outcome that is
neither a failure nor an escalation -- nothing is wrong -- which makes it the easiest
thing in the system to never look at.

### Two laps for one issue, stopped by a lock that happened to still be held

**What happened.** PR #13 was held on ratchet slack. The very next dispatcher tick
answered:

```
NEXT implement gh:issue:12 (highest-priority accepted issue (medium))
```

That is the issue PR #13 was for. A second implement lap started, on a second branch,
for work that was already built and waiting on a person.

**Why it only nearly happened.** The validation's lock was still held, so the duplicate
could not take a slot on the tick I was watching -- and then it did, one tick later,
once that lock cleared. The run was live for 58 seconds before I cancelled it. The
thing that "prevented" it the first time was a lock timing coincidence, which is luck
rather than a mechanism.

**The cause.** `next_action` selects an issue on its label alone, and `accepted` is
reachable while a PR for that issue is open -- a person accepting an issue somebody
already built, or an issue walked back from `in-progress` after its PR opened. The
reconcile sweep already asks exactly this question about `in-progress` issues, using
`linked_issue`. The selection path did not ask it at all, so the two disagreed.

**The rule.** An issue that a live pull request already answers is not work. Same
question, same helper, both places -- and once that PR is merged or rejected, the issue
is selectable again, which the self-test checks in both directions, because a filter
that is too broad strands the issue instead of duplicating it.

### The staleness check that called an unanswered question "current"

**What happened.** The check added *the same afternoon* to catch a stale checkout read:

```python
if rc_fetch == 0 and rc_count == 0 and behind > 0:  ...stale...
else:                                               ...OK, "level with origin/main"...
```

A failed fetch -- offline, no remote, a branch that does not exist -- took the `else`.
It printed **"checkout is current"** about a tree it had not compared to anything.

**Why this one is worth its own entry.** It is the exact failure this entire system is
built to prevent, written by someone who had spent the day fixing instances of it, into
the check whose whole purpose is catching it. "Did the comparison say we are behind?"
and "did the comparison happen?" are different questions, and collapsing them is the
default shape of an `if/else`.

**The rule.** Three branches, never two: **behind**, **level**, and **could not tell**.
Any check with a network call, a subprocess, or a parse in it has a third answer, and
the language will happily let you write only two.

### The default branch that was assumed, in a product whose pitch is "install it into your repo"

**What happened.** `main` was a string literal in a dozen places across the merge and
the deploy poller -- `base = "origin/main"`, and a merge that refused any pull request
whose base was not literally `"main"`.

On a repository using `master`, or `develop`, or a release branch, this installs
cleanly, the doctor goes green, laps run, pull requests open -- and every merge is
refused, forever, for a reason that reads like a configuration mistake. That is the
worst shape a bug can take in something sold as "install it into your repo".

**The rule.** `config.BASE_BRANCH`, read from `origin/HEAD` -- what the remote itself
says its default is, which is the only answer that is not a guess -- with
`FACTORY_BASE_BRANCH` to override and `main`/`master` probes as fallbacks. `bin/audit.py`
fails on a bare `'main'` literal anywhere in the machinery.

### The setting that could never reach an existing install

**What happened.** `BASE_BRANCH` was added to `config.py`, four modules were synced to
use it, and the sync reported success. The doctor then died:

```
AttributeError: module 'config' has no attribute 'BASE_BRANCH'
```

**The cause, and it is structural.** `factory/config.py` is on the sync's NEVER list
*because* it is the one file you edit -- every project-specific value lives there and
overwriting it would throw them away. The consequence is not obvious until it bites: a
new setting can never reach an existing install, and the synced code that reads it
raises at runtime, on whatever path happens to touch it first.

**The rule.** The sync diffs the template's config against the target's and prints the
settings that are missing, with the code that defines them, for the operator to paste.
The file stays theirs; it is no longer allowed to be silently incomplete.

**And report what a setting DEPENDS on.** The first version listed
`BASE_BRANCH = _base_branch()` and nothing else -- an instruction to paste a line into a
file with no `_base_branch` in it, turning an AttributeError into a NameError. A
setting is not portable without whatever computes it.

### The armed factory that never re-tested anything

**What happened.** `factory arm` on Windows printed `ARMED`, created the scheduled
task, and the doctor reported `trigger armed: scheduled`. Everything agreed the factory
was running.

The **regression was never scheduled.** The cron backend installs two entries -- the
dispatcher every thirty minutes and the weekly re-test of what already merged. The Task
Scheduler backend installed one, and returned success.

**So a Windows factory could be fully armed, fully green, and never once re-test its own
merged code** -- the component whose entire job is noticing that something that used to
work stopped working simply was not there. And nothing reports a job that was never
created, so the only symptom is bugs not being found, which is indistinguishable from
there being none.

This is the "a fully built factory with nothing scheduled audits identically to a
running one" failure that the trigger check exists to prevent -- reproduced one level
down, inside the thing that installs the trigger.

**The rule.** Both backends install both jobs, `remove()` deletes both, and
`bin/audit.py` checks the parity, because a missing scheduled job leaves no trace to
find at runtime.

**A note on how the check itself first failed.** It grepped the raw function text for
the regression, and a build with the call *deleted* still passed -- the comment above
the call explained what it was for, and the grep matched the explanation. A check that
its own rationale satisfies is a check that cannot fail. It now reads the code with
comments and docstrings stripped, which is the same repair the lock-liveness check
needed, for the same reason.

### The race that was finally run, by accident

**What happened.** Two dispatcher loops ran against the same repository for
forty-five minutes, because an earlier one was never stopped. Every unit of work was
dispatched exactly once -- triage, validation and the merge by one loop, the
implementation by the other, no duplicates and no overlap.

**Why it is worth writing down.** The O_EXCL lock exists for precisely this, and
nothing had ever tested it. `acquire()` uses `O_CREAT | O_EXCL` rather than
`if not exists: write` because the latter is a time-of-check-to-time-of-use race that
is entirely reachable -- a tick that outlives the cron interval, or a human running the
dispatcher while the schedule fires. That argument was sound and completely
unevidenced until two loops collided by mistake.

**The lesson is about the evidence, not the lock.** Every other guarantee in this
system was tested by deliberately breaking something. This one was tested by an
accident, which is the only reason it has any evidence at all -- and it is worth asking,
of every remaining "this cannot happen because", whether anything has ever made it try.

### The closing keyword that was formatted as code

**What happened.** A lap finished perfectly: PR merged, issue labelled `factory:done`.
The issue was still **open**.

**The cause.** The PR body said `` `Fixes #10` `` -- with backticks. GitHub does not
treat a linkage keyword inside a code span as a closing reference, so it did nothing.
The previous PR had written `Fixes #8.` in plain prose and closed correctly.

Nothing errored. And the label made it worse rather than better: `factory:done` on an
open issue reads as finished on every board a person would look at.

**The rule.** A step that must happen does not depend on somebody else's markdown
parser agreeing with an agent's formatting. `set_state(issue, "done")` closes the issue
itself. `Fixes #N` stays in the body because it makes the PR readable; it is no longer
what does the work.

**The general shape**, and it is the third time it appears in this list: a load-bearing
step delegated to prose. The judge's verdict thrown away over an enum, the hold written
as a comment, and now the close written as a keyword -- each time the mechanism was a
sentence, and each time the failure looked like success.

### The reaper whose docstring explained why it was safe, in a system where that was false

**What happened.** With the lock-release path fixed, an implement lap was escalated as
dead again -- at 5 minutes 39 seconds, while it was running, and it ran on to open a
pull request. This time the run list was answering correctly: it said `running`. The
lock was gone anyway.

**The cause.** The *other* reaper. `reap_locks()` frees a lock when "the owning process
is gone AND the lock is older than GRACE", and its docstring said:

> A live long lap is never touched, because its PID is alive -- that is the check that
> matters, and age is only the fallback for when the PID cannot be read.

**That sentence is false for every dispatch this system makes.** Dispatch is detached:
`archon workflow run` hands the work to a child and returns in seconds, so the recorded
pid is dead almost immediately while the run has another twenty minutes to go. The pid
test never protects anything. `GRACE` was the only thing standing between a live lap
and the reaper, and almost every implement lap is longer than five minutes.

**The rule.** The pid on the lock is the *dispatching* process, not the run. It is
meaningful for exactly one case: a lock that names **no run**, meaning the dispatch
died before it could record one -- there the pid is the only owner there ever was. A
lock that names a run is freed by evidence about *that run*, or by the long stale cap,
which is the backstop for an engine that can no longer be asked.

**The general one is about the comment, not the code.** A docstring asserting the
property that makes something safe is worth more than most tests -- right up until the
system changes underneath it and nothing re-reads it. This one described a foreground
dispatch. The code had been detached for a long time.

### The machinery that had a harness for everything except itself

**What happened.** The bug above lived in the file whose entire job is deciding what is
alive. It had no test. Every check in this repository pointed at the product: the
harness proves the software works, the mutation set proves the harness can fail, the
doctor proves the factory was given what it needs. Nothing proved the **factory's own
parts behave as written** -- and a dispatcher that mis-decides which laps are alive
produces a repository that looks exactly like a quiet one.

**The rule.** `factory/_selftest.py`, run by the doctor on every audit. Fast, offline,
no GitHub. It pins the invariants that were once wrong in a way that read as normal
operation: an empty run list keeps a lock, an unrecognised status keeps a lock,
`needs-human` is terminal, `passed` does not lead back to `validating`, an empty log
yields no counts, and every state that is not defined by absence has a label.

It was mutation-tested the same day it was written -- the original defect was injected
into a throwaway copy and the self-test went red, which is the only reason to believe
any of it can fail at all.

One check earns its place for a different reason: `import config` and
`from factory import config` produce **two module objects with separate state**. The
first draft of the self-test used the second form, so it configured a copy of the thing
it believed it was testing. Every call succeeded and three assertions came back false
for a reason that had nothing to do with the code under test.

### The transition table that governed only the callers that asked

**What happened.** A validation claimed a pull request that had been escalated to
`needs-human` three minutes earlier, moved it to `validating`, and ran. The table says
`needs-human` reaches nothing, and it is the one guarantee written as absolute:
*a node may never move an item out of needs-human.*

**Two causes, and both had to be fixed.**

The enforcement lived in `state.py`'s **command line wrapper**. Eleven callers import
`set_state` and call the function directly -- the gate, the merge, the dispatcher -- and
every one of them was governed by nothing. The guarantee was opt-in and read as
absolute.

And the check that did run compared against labels the node had read **at the start of
its work**. The escalation happened after that read. No table can fix a decision made
from data that predates the write.

**The rule.** Enforce at the write. `set_state` fetches at the moment it writes, which
is the latest anything can know, and refuses there -- so the wrapper and the eleven
importers get the same answer. `force=True` exists for exactly one thing: parking at
`needs-human`, which must be allowed from anywhere, because an escalation a table
lookup can block is not an escalation.

**And the dispatcher must not dispatch what it just escalated.** GitHub does not
promise you read your own write. A tick escalated a PR at 19:21:55 and re-dispatched it
at 19:22:03, eight seconds later, straight back into the state a human had just been
told to look at. `escalate()` now returns every target it parked -- the linked issue
included -- and the tick excludes them.

### The judge that was thrown away for a word

**What happened.** `error_max_structured_output_retries` -- five attempts, fifty-two
seconds of judging, and the run died. `apply` binds to `$judge.output`, a binding never
falls back to `if_skipped` for a *failed* producer, so the one node whose job is to
report an infrastructure failure could not run. A live worktree left behind, a human
paged, and no record of what the judge actually said.

**The rule.** **Constrain only what is branched on.** `factory/gate.py` reads exactly
one field to decide anything: `verdict`. `severity` and `category` reach the PR comment
through `f.get('severity', '?')` and change nothing. Every enum on a field nobody
branches on is a fresh chance *per finding* to fail the whole run for a synonym -- eight
of them on a five-finding review. The vocabulary belongs in the prompt, where being off
it costs a slightly worse comment instead of a dead validation.

`verdict` keeps its enum, because a value outside that list has no defined behaviour
and must fail loudly. `issues_to_fix` and `rules_cited` stopped being required: a clean
approval has no findings and invokes no rule, and demanding an empty array as proof of
that is a fifth way to fail.

### Aimed at a rung, landed on another

**What happened.** The mutation set reported 10/10 with defects "aimed at" four rungs.
Two of them were being caught by the **unit** suite: the app-start defect broke a
module-level import that every unit test hits, and the e2e defect flipped a 409 to a
400 that `tests/` asserts directly. Both target rungs were still rungs nobody had ever
seen go red, and the score said nothing was wrong.

**The rule.** The runner reports **which rung caught each defect**, and that column is
the point. Aiming is not landing. A defect aimed at e2e must be invisible to the unit
suite or it measures the unit suite twice.

**And re-aiming found a weak assertion**, which is the part worth keeping. The new e2e
defect replaced the balances table with a bare div and **escaped**: step 1 asked whether
`<table` appeared anywhere in the page, two other tables were still in the markup, and
the check passed while the one screen the product exists for showed nothing.

That is the holdout's "balances sum to zero" failure exactly, one level up: **a property
that holds for a subset is not a check on the whole.** It appears wherever a check is
written as "something like this exists" rather than "this specific thing is here", and
the only thing that finds it is a defect aimed at that rung.

### The run that died between two nodes and left the issue reading "being worked on"

**What happened.** An implement lap ran cleanly through prime, plan, implement, commit
and guard, then stopped. Not failed -- stopped. The last line in the log is
`selfcheck` starting and its script discovery completing. No error, no exit code, no
stack. The process was gone, the Archon run row still said `running`, and the issue sat
at `factory:in-progress` for eighty-six minutes on a node whose declared timeout is
thirty.

**What made it survivable.** The work was not lost: implement had committed, so the
branch carried the change. And `state.py next` reported
`stalled-issue gh:issue:1 in 'in-progress' with no PR` rather than moving on to
unrelated work. That one line is the difference between this and the three months the
predecessor factory spent finding no work behind a label nobody lifted.

**What the silence was NOT.** `selfcheck` runs the full gate -- static, unit, the two
agent-driven rungs, and the whole mutation set, each mutation re-running the gate -- under
`capture_output=True`. It therefore emits NOTHING to the run log for its entire duration,
which on this repo is tens of minutes. So "no output for an hour" is not evidence of death
and must not be read as such; what proved this one dead was that no `bun` process existed.
Worth knowing before diagnosing the next one, and worth weighing against the node's declared
30-minute timeout, which is plausibly tighter than the work it is timing.

**What is still unproven.** The cause. The run was launched by hand under `nohup`
rather than through `dispatch.py`, so no lock existed and no reaper applied, and a
detached child not surviving its parent shell is the likeliest reading. It is also true
that it died at the exact moment the one `runtime: uv` node started, and that uv creates
its virtualenv on first use. Both remain guesses, and they are written down as guesses.

**The rule.** A label that means "being worked on" needs something that notices when
nothing is working on it. `next` reporting a stalled issue is that something, and it is
worth more than any amount of care about not crashing -- because the crash will happen
and the report is what turns a three-month outage into a five-minute one.

### The consumer that would have failed naming itself, eight nodes after the mistake

**What happened.** A new `bash:` node emitted `{"errors":"true","docs":"auto"}` and a
node further down read `$review-scope.output.errors`. The workflow validated clean and
`archon validate workflows` said `ok`. The cross-file audit did not:

```
[FAIL] factory-implement: $review-scope.output.errors is read, but 'review-scope'
       declares no output_format -- the consumer will fail, naming itself rather
       than the producer
```

**Why it mattered here.** The consumer sits after prime, plan and implement. The failure
would have arrived roughly thirty minutes and one premium model into the lap, and its
message would have accused the reader rather than the writer.

**The rule.** The audit is not a linter, it is the only thing that reads ACROSS files.
Schema validation proves each file is well-formed; it cannot know that a producer never
promised a field. Run the audit after editing the pack, not only after editing the
machinery -- this one was clean an hour earlier, before the node existed.

### The cancel that reported no live owner for a run that was alive

**What happened.** `archon workflow cancel <id>` answered:

> No live detached CLI owner is reachable for run &lt;id&gt;. The run was not changed.
> (ENOENT) If you have verified that its process is gone, use
> `archon workflow abandon <id>` to release its persisted state.

The process was NOT gone. The run was executing normally under a shell the operator had
started. Taking the message at its word and running `abandon` killed a healthy lap at
its third node, and the log then read `Failed: Cancelled by user` -- which was true, and
gave no hint that the user had been told the opposite a second earlier.

**The rule.** "I could not reach it" is not "it is dead", and a message that offers the
destructive remedy in the same breath invites the confusion. Before abandoning, read the
run's own log and check for a live process; an abandon is not reversible and the thing
it destroys is usually the expensive half of a lap.

### The import the merge had always needed, on the only line no failing lap reaches

**What happened.** The first fully green validation this repo ever produced died one
statement before the merge it had just earned:

```
MARKERS_OK checked=4
RATCHET_OK keys=3 e2e_journeys=3/3 holdout_scenarios=3/3 unit_tests=49/45
GATE_PASS pr=gh:pr:2 markers green, mutations 9/9
NameError: name 'os' is not defined
```

`gate.py` used `os.environ` exactly once, on the line handing the observed counts to
`merge.py`, and never imported `os`.

**Why it survived every earlier lap.** That line runs only when the markers, the ratchet
AND the verdict are all green. Every lap before this one stopped earlier, so the merge
path had never executed. A bug reachable only on total success is invisible to exactly
the testing that finds bugs.

**Why the static rung could not see it.** For a Python project the shipped `static` is
`python -m compileall`, which proves a file PARSES. A NameError is a runtime event.

**The rule.** `_selftest.py` now parses every factory module and asserts that each stdlib
module it references by attribute is also imported there. Narrow on purpose -- it is not a
type checker, it exists to make this one silence impossible to repeat. Proven in both
directions: removing the import turns the self-test red naming the file.

### The ratchet that did not move on the merge that opened the gap

**What happened.** The lap merged with the gate observing `unit_tests=49` against a floor
of 45. `floor.json` and `merge.py` both promise the gap is closed "in the same breath as
the merge". After the merge, main's floor still read 45, and nothing in the run mentioned
a raise.

**Where it probably goes.** `raise_floor` runs in the checkout the validate workflow owns
-- a throwaway worktree on `factory/validate-pr-*` -- while the merge itself happens on
GitHub. A floor commit written there has nowhere to go.

**Why it matters.** The slack is not cosmetic: it is exactly the number of assertions that
can be deleted with the gate still green. Four here, and it grows every time the harness
improves, which is the failure mode `floor.json` names in its own header.

**Unfixed, deliberately.** Where the raise should land is a design decision -- a commit on
main from the machinery, a follow-up issue, or a human line in the PR record -- and picking
one blind would be guessing. The PR record already prints the exact raise to apply.

### The fix loop that threw away a ten-minute fix as "changed nothing"

**What happened.** The first `factory-fix` run this factory ever executed died at the
land step: *"the fix node changed nothing. A finding is not addressed by an empty
diff."* The fix had been written correctly and committed.

**The cause.** `land-fix.py` asked `git status --porcelain` and read a clean tree as an
empty fix. That question stopped being the right one when the build step became
`archon-implement`, which commits as it goes. It is the SAME defect that was found and
fixed in `commit.py` in the same change -- and missed one file over, because the fix was
applied where the commit happens rather than where the emptiness is asserted.

**Then it died again**, on the corrected path, with `NameError: name 'note' is not
defined`. `land-fix.py` has `die()` and prints to stderr; it never had `note()`.

**Both were invisible to the checks that existed**, including the ones written that
morning for exactly this class. The undefined-name self-test watched stdlib MODULE names
only, and it scanned `factory/` while the workflow scripts live under
`.archon/workflows/factory/**`. A check aimed at the wrong directory passes for the same
reason a check aimed at nothing passes.

**The rules.** A script that decides on `--porcelain` and can stop must also consult
`rev-list` -- the question is whether the BRANCH moved, not whether the tree is dirty.
And the name check now covers the factory's own output helpers as well as modules, over
both roots. Both are enforced in `_selftest.py` and both were verified by reintroducing
the exact failure.

**The general shape:** three bugs, one root. Each was a check asking a question that used
to be equivalent to the one that mattered, and quietly stopped being.

### Three assumptions announced as twenty-five, and the same bug announced as zero

**What happened.** `gate-plan.py` reported `ASSUMPTIONS_RECORDED 25` for a plan carrying
three. `gate.py` reported `0 recorded assumption(s)` for a plan carrying one.

**The causes are opposite halves of the same contract.** `gate-plan` counted non-blank
LINES, so it over-reported by the length of each WHY paragraph -- the identical bug
`gate.py` documents having already fixed, still present one file over. `gate.py` counted
`KEY=value` keys, an implicit contract with the plan prompt the factory used to own; the
planner is Archon's now and recorded its assumption as prose, so it counted none.

**Why it matters more than a number.** The count is the first thing a person reads on a
hold. Twenty-five reads as a wall nobody can review; zero reads as nothing to review, on
a pull request being held precisely because there IS something to review. Both directions
end in a rubber stamp.

**The rule.** One counter, `gate.assumption_keys`, used by both, with a fallback so a
non-empty file is never zero. The planner is also told the `KEY=value` shape, and the
next lap came back with three properly keyed assumptions and a hold that named all three.

## Inherited from the factory this one was built from

These were paid for by an earlier experiment. They are not hypothetical either.

### The factory that was starved, not broken

Three months of silence. The last merge was in May, main had not moved, and it read as
"it died". It had not: the dispatcher ran every thirty minutes the whole time and
logged `nothing to dispatch` on each tick.

A benchmark run had labelled all seventeen open issues `in-progress` as a deliberate
lockout, and the lockout was never lifted. The factory then correctly found zero work,
forever.

**The rule.** Before concluding an autonomous system is broken, check whether its
*input queue* was closed rather than its machinery. A dispatcher logging "nothing to
do" on a cadence is a healthy dispatcher with an empty queue, and it looks identical
to a corpse from the outside. Anything that deliberately mutates shared state as a
lockout needs an owner and an expiry.

### The correct rejection that reached the filer as two characters

Triage rejected an out-of-scope issue perfectly: right verdict, right label, closed
not-planned, and a written rejection citing two rules by number with an appeal path.

What reached the filer was `@-`.

The reasoning had been assembled in a shell pipeline instead of going through the
comment helper, and on Windows the pipeline collapsed. Every state transition was
right. The call exited 0. The run reported success. The only thing lost was the entire
explanation -- the part a human was ever going to read.

**The rule.** Every human-facing write goes through one helper, and the helper reads it
back. `exit 0` from the tool that posted a verdict proves the API call succeeded, not
that it carried anything.

### The lap where every node succeeded and the branch was empty

Every node reported OK. The guard correctly saw two changed files and thirty lines.
The branch ended up with nothing on it: the implement node had edited the worktree, and
the worktree was discarded when the run finished.

Driving the same lap by hand had hidden it completely, because **a human commits
without being told to.**

**The rule.** The commit is a node, and it asserts on the artifact rather than the exit
code. An empty diff fails the lap loudly, with any tool denials attached -- because a
denial is the usual cause and correlating them an hour later in a log is not a process.

### The fix loop that could not be reached

One wrong arrow in the transition table -- `failed → validating` instead of
`failed → open` -- made the entire fix loop unreachable. The fix committed, the illegal
transition returned non-zero, the workflow died on that line, and no escalation ran.
The PR sat in `validating`, which the dispatcher does not look at, so it answered
`idle` from then on.

**The rule.** A factory wedged that way is indistinguishable from a factory with
nothing to do, which is the failure mode this whole system exists to avoid. `open` is
the right target on its own: a fixed PR is a PR waiting to be validated.

### The cap that silently did not apply

The runner passed `--max-turns` to every node. The agent accepted it, ignored it, and
exited 0. Every comment in the codebase claimed a cap was in force. No cap ever
applied, nothing errored, and the only reason it surfaced was somebody running
`--help`.

**The rule.** **A guard that silently does not apply is worse than no guard**, because
you stop watching the thing it was guarding. Verify a flag before writing it into a
workflow, and prefer a spend ceiling to a turn ceiling regardless.

### The lock that outlived its owner

The dispatch lock is released when the run finishes. It is not released when the
process is *killed* -- a reboot, a sleeping machine, a closed terminal, the OOM killer.
The lock file survived, counted toward capacity forever, and every later tick logged
`at capacity (1/1), nothing dispatched` and exited 0.

**The rule.** Reap dead locks *before* counting capacity, and use two tests: the owning
process is gone AND the lock has had time to be real. PIDs get reused, and a liveness
check that gets it wrong kills a running lap.

### The stop button that only worked while the network did

"Remove a label to stop" cannot distinguish a missing label from an API call that
failed to list it, so a network blip reads as "carry on".

**The rule.** The stop button is a label you ADD, any error reading it counts as
stopped, and there is a local kill file too -- because the local one works with the
network down, which is when you most want it.

### The three-dot diff

`git diff main` compares two *tips*, so a branch cut before main moved reports main's
later commits as this branch's work -- and main's later commits routinely touch the
protected paths. Every such branch was auto-rejected for files it never went near: a
false positive in the most severe gate there is, firing more often the longer a branch
lives.

**The rule.** `main...HEAD`, always. Compare against the merge base.

### The escalation that took the success path

A triage run correctly decided `needs-human`, wrote the ledger line, and stopped --
because the notifier was only reached from the *failure* path, and a correct
escalation is not a failure.

Measured: seven probe issues, two correct `needs-human` decisions, **zero
notifications.** The stop list worked perfectly and nobody was told.

**The rule.** Every route into `needs-human` notifies. There are more of them than you
think: the runner, the gate, the fix cap, the dispatcher's stall sweep, and triage
itself.

## The escalation that fed the queue it was supposed to leave

**Symptom.** An unattended loop dispatched `factory-validate` at one pull request
**68 times in three and a half hours**, every tick, each run failing in the same way.
Nothing else in the queue was ever reached.

**What was actually right.** The guard was correct: the PR changed 515 lines against a
500-line cap, so it was rejected on its merits. The gate set the PR to `rejected`,
commented, escalated the issue, and notified. All of that worked.

**The bug.** `PR_STATES` did not contain `needs-human`. `_state_from_labels` skips any
state that is an ISSUE state but not a PR state, so a pull request carrying
`factory:needs-human` fell through the entire table and returned the default: `open`.
`next_action` selects the oldest `open` PR as "awaiting the independent validator".

So escalation wrote the label, and the very next read handed the same PR back to the
dispatcher as fresh work. **The one state that means STOP was the one state that could
not be read back**, and the machine could not see the brake it was pressing.

`TRANSITIONS` had listed `needs-human` as a legal PR destination from the start. Two
tables disagreed, and every test only ever consulted the one that was right.

**Why nothing caught it.** Ten self-test invariants interrogate `TRANSITIONS`. Not one
asked whether a state written as a label reads back as itself. Writing and reading were
each tested against the table; they were never tested against **each other**.

**The fix.** `needs-human` and `held` added to `PR_STATES`, plus a round-trip invariant:
every state, written as its own label, must read back as itself for every kind that
declares it, and every destination reachable from a PR-only state must be declared in
`PR_STATES`.

**The second lesson, which is the sharper one.** The first version of that new check
**went vacuous instead of red**. It asked which kinds a state was declared for and
round-tripped those, so when `needs-human` was missing from `PR_STATES` the PR case was
simply never generated: 118 checks passing rather than 119 failing. A check that
evaporates in exactly the condition it exists to detect is worse than no check, because
it reports success. This is "empty is not pass" reappearing one level up, inside the
harness, and it is why the invariant is now STATED rather than derived from the list it
is auditing. Every new check must be run against the broken code before it is trusted.


## The safety system that fabricated its own evidence

**Found within an hour of shipping the watchdog, by the watchdog's own monitor.**

`factory/_selftest.py` proves the lock logic by driving `release_settled_locks()` with a
synthetic Archon payload. That function had just been instrumented to record a settle to
the ledger, and `ledger.LEDGER` was bound at import to the production path.

So **every `doctor` run appended invented history to the evidence the watchdog judges**:
run id `11111111-2222-3333-4444-555555555555`, alternating `completed`/`failed`, eleven
entries deep before it was noticed.

**Why it is worse than it sounds.** Fake evidence in a safety system turns the safety
system into the hazard. Those settles carried no cost, which is what surfaced it (the
`spend-blind` warning fired), but a slightly different fixture would have tripped
`all-failing` and halted a perfectly healthy factory. Nothing would have looked wrong:
the entries were well-formed, plausible, and written by the factory's own code.

**How it was caught.** Not by a test. The operator-side monitor reported a standing
`spend-blind` warning, and the ledger tail showed a run id no real dispatch could have.
The second layer earned itself on its first hour.

**The fix.** The ledger path resolves per call and is env-overridable; the self-test
redirects it to a temp file for the whole run; and three new invariants pin it: the
redirect must be in place, a recorded event must NOT reach the real ledger, and it MUST
reach the redirected one. That third one matters -- without it the check passes when the
write silently goes nowhere, which is the vacuous-check failure again.

**The general rule.** Instrumenting a function makes every existing caller of that
function a writer, tests included. When you add a side effect to shared machinery, the
question is not "is this correct" but "who else calls this, and do they mean it".


## The watchdog's first act was to halt a healthy factory

One hour after the runaway watchdog shipped, it fired for real:

    [HALT] all-failing: the last 5 settled runs all ended ['not_found'] with no
    completion. Nothing is getting through.

Three pull requests had merged during exactly that window. Nothing was wrong.

**The leak.** `not_found` had been added to `SETTLED_STATUSES` an hour earlier to fix
a different problem: `archon workflow runs --json` reports a window of 20 runs, so a
finished run ages out of it and its lock could not be released, wedging capacity to
zero for 180 minutes. Asking the engine about that run directly and treating
`not_found` as an answer is correct **for the lock question** -- nothing will ever
hold that lock again.

It is not an answer to the **progress** question. There, `not_found` is silence: the
run may have succeeded, failed, or never started. The status leaked from a question
where it means "no" to a question where it means "no idea", and the detector read
silence as failure.

**Why this is worse than a missed detection.** "Empty is not pass" exists because
assuming SUCCESS on no evidence hides defects. The mirror costs more: assuming
FAILURE on no evidence makes the safety system the outage. A watchdog that halts a
healthy factory is one people switch off, and a switched-off watchdog is worse than
one that was never built, because everyone believes it is watching.

**The fix.** `UNKNOWN_STATUSES = {"not_found"}`. All four progress detectors now
require positive evidence -- a settle that reported a real outcome, or a dispatch
whose run can be asked about. The regression is kept, and beside it the half that
must not be disarmed: real `failed` runs still trip the detector. Reintroducing the
exact bug is one of three mutations, all caught.

**What worked.** The halt MECHANISM was correct in every respect: STOP written,
notification sent, loop stopped, and the operator monitor reported it within seconds.
Only the detector's reasoning was wrong. That is the right way round for a first
live failure -- the plumbing is the part that is hard to fix under pressure.

**The generalisation.** A value that answers one question is not thereby an answer to
a neighbouring one. When a sentinel earns its meaning in a specific decision, check
every OTHER decision that reads the same field before adding it to a shared set.


## The autonomous factory that needed a human for everything

Cole watched a session and said the factory did not look like it was running through
GitHub at all. The wiring was fine -- issues in, `factory:*` labels as the state, PRs
out, every transition a `gh` call. What he was actually seeing is that a human touched
every single merge. In one session the hold rate was **100%**, and all three hold rules
turned out to be wrong, each in a different way.

**1. The assumption count was reporting ten times the real number.** The hold message
counted non-blank LINES of a file whose format is one `KEY=value` followed by an
indented WHY paragraph. Seven assumptions were reported as sixty-nine; eight as eighty.
On every pull request ever opened.

That number is the first thing a person reads on a hold. "80 recorded assumptions"
reads as a wall nobody can review; "8" reads as an afternoon. The hold was never too
strict -- it was describing itself as ten times its size, and a review that looks
impossible gets rubber-stamped, which is worse than no hold because it manufactures
assurance.

**2. Ratchet slack held the merge that would have closed it.** Slack is how many
assertions could be deleted with the gate still green, and it grew because only a human
could close it, so the gate held on it. Correct instinct, wrong remedy: it made the
SUCCESS case -- a change that ADDS tests -- the case that needs a human. Four PRs in
one session were held on slack alone and took four separate commits to release while
the factory sat idle.

The merge now closes the gap in the same breath as the merge that opened it. What makes
that safe is that the raise is **monotonic**: only up, only keys the floor already has,
never a ceiling. A pull request touching `floor.json` is still auto-rejected, so "the
floor never falls without a human" -- which IS the ratchet -- is untouched. Only the
direction that tightens was automated.

**3. The uncalibrated hold had never once fired.** It matched `NAME_UNCALIBRATED=<n>`
while the harness prints `FAILED=0 UNCALIBRATED=5`. A space, not an underscore. A
deliberate safety gate, dead since the day it was written.

And repairing the regex alone would have been **worse than leaving it dead**: margins
are uncalibrated by design, so "any exist" refuses every auto-merge forever -- not a
signal, a global off switch, with the dial reading 3 and behaving like 0. It is now a
CEILING, the mirror of the floors: check counts may not fall, unmeasured margins may not
rise. What it catches is a change that INTRODUCES a threshold nobody chose.

**The pattern worth taking away.** Every one of these was a hold that fired on the wrong
thing, and none of them looked wrong from inside the code. You cannot find them by
reading the hold rules; you find them by asking what fraction of work is being held and
why, which is a question only running the factory for a day can answer. **A gate's error
rate is a measurement, not a design property.** If the honest answer to "how often does
this hold?" is "always", the gate is an off switch whatever its docstring says.

A useful corollary: two of the three needed CALL-SITE checks to pin. Unit checks on the
counting function passed happily while the gate counted lines beside it, because the
tests could see the function and not its caller.


## The guard could not guard itself

**The most serious defect found in this build.** Every other protection in the factory
rests on the guard: it is what rejects a pull request that touches the ratchet, the
holdout, the harness, the workflows or the machinery. It ran from the branch it was
judging.

`run-gate.py` did `sys.path.insert(0, cwd/"factory")` and then `import guard`. In a
validate run, that cwd is the PULL REQUEST'S OWN CHECKOUT. So the code deciding whether
a PR may touch protected files was code that PR could edit.

**Demonstrated in a single commit.** A branch that set `UNIT_CHECKS` to 1 in the
protected ratchet AND changed `if violations:` to `if False:` in `factory/guard.py`
produced:

    GUARD_START range=origin/main...HEAD files=2 lines=4
      BLOCK  .factory/locks/floor.json
      BLOCK  factory/guard.py
    GUARD_FILES_CHECKED=2
    PROTECTED_OK
    exit 0

It SAW both violations. It named them. Then it waved itself through -- and
`PROTECTED_OK` is a required marker, so every check downstream was satisfied by a guard
that had been switched off by the diff it was inspecting.

**The fix.** The guard is materialised from the BASE ref into a temp directory and run
from there. `config._repo_root()` resolves from the CWD, so it still measures the diff
in the PR's worktree: trusted code, untrusted subject. The same branch now exits 1 with
`PROTECTED_VIOLATION=2`.

Being unable to read the trusted guard **fails closed**. Falling back to the branch's
own copy would be the original bug wearing a fallback's clothes.

**Why it survived so long.** The workflow already read GOVERNANCE from the base branch,
with a comment explaining that "a PR must not be able to weaken the rulebook it is about
to be judged against." The rulebook was protected and the code enforcing it was not, and
the second half is the one that matters: **a rule nothing enforces is a comment.** The
protection was written for documents and never extended to the enforcer, because the
enforcer did not look like a document.

**The general lesson.** Ask of every check: *whose copy is running?* A validator that
loads any part of itself from the artefact under test has no integrity, however good its
logic is. This one had excellent logic. It printed the violations before allowing them.


## Three dispatchers, one merge

The first fully autonomous merge worked: the gate passed the PR, the dispatcher merged
it, the code landed. The records said the opposite -- a merged pull request labelled
`needs-human`, beside its closed issue -- and the loop log showed three `MERGE`
dispatches inside three seconds.

Three copies of the loop were running. Stopping the wrapper that launches it did not
always take the bash child with it, so every restart left one behind. Two of the three
merges were refused because GitHub had already merged the PR, and each refusal
escalated a pull request that had in fact merged perfectly.

**The per-target lock is not a mutex between dispatchers.** It makes ONE dispatcher
safe against itself, which is what it was written for, and the merge path does not take
one at all. Nothing in the design ever said "only one of me may run", because until
somebody restarts the loop carelessly, only one ever does.

The loop is now a singleton: a pid file, a liveness check, and a trap that clears it on
any exit. A STALE pid file is cleared rather than obeyed, because the usual way one is
left behind is the machine dying -- exactly when the loop needs to come back on its own.
Both directions are verified: stale file cleared and started, live loop refused with the
holder's pid named.

**And the feature that never ran.** The same merge revealed that the floor auto-raise
had never been invoked. There are TWO paths to a merge: the gate merges after a green
validation and hands the observed counts over in an env var, and the DISPATCHER merges
whenever it finds a PR already in `passed` -- with no counts, because it never ran a
gate. The dispatcher path is the one that ran. The auto-raise was present, correct, and
simply not on that road, so the floor stayed at 66 while main moved to 70.

The lesson is narrow and worth keeping: **when a capability is invoked from one call
site, ask what the OTHER call sites do.** A feature tested on the path you thought about
is a feature that exists on that path only. The counts are now written to disk as well
as passed in the environment, so both producers reach the one consumer.


## Three checks that measured the wrong text

Not one incident but a pattern, and it is the most useful thing the clean-room install
turned up. Three separate checks were correct code aimed at the wrong bytes, and every
one of them read as working:

**The assumption count counted LINES.** The format is `KEY=value` followed by an
indented WHY paragraph, so seven assumptions were reported as sixty-nine and eight as
eighty. On every pull request ever opened. A hold that says "80 recorded assumptions"
is a hold nobody reviews.

**The out-of-scope count matched only `-` and `*` bullets.** Both real MISSION files
use a NUMBERED list, because the prose refers to "out-of-scope item 7". So a mission
with nine carefully argued exclusions reported "0 entries -- fewer than five is too
thin", which is the opposite of true.

**And when that was fixed it reported 2, not 9.** The check took
`mission.split("Out of scope")[-1]`, and "Out of scope" is ordinary English that
appears in prose: one MISSION said "Out of scope for this slice, but the feedback set
names a sound event" under Open questions, so the segment being counted was the tail of
THAT section. The fix is to anchor on the HEADING -- the only unambiguous marker of
where a section starts -- and end at the next heading of any level.

**What they share.** Each check ran, produced a number, and printed a confident
sentence. None of them was broken in a way a test would catch, because each was
perfectly correct about the text it was actually looking at. The bug was upstream of
the logic, in what got selected.

**What to do about it.** When a check parses a document, assert the SELECTION and not
just the verdict. "Nine entries" is a testable claim about a known file; "the warning
did not fire" is not. Two of the three were found only by writing a real MISSION into a
real install -- neither was reachable from this repository, because this repository has
no MISSION of its own to parse.


## Rollback had never been run, and was wrong four ways

The path you reach for when something is already broken. It had never executed once, in
any repository, and running it a single time in the clean room found four separate
faults -- each of which would have surfaced during an actual outage.

**1. It left the repository armed.** `git checkout <previous> -- .` rewrites the working
tree and STAGES that, and the function returned there. HEAD never moved, so the repo was
left holding a staged revert of everything since -- twelve files on the real run, among
them MISSION.md and factory/config.py. The next commit by anyone would have committed
that revert. This is the `update-ref` incident merge.py already carries a long warning
about, reproduced in a second file and armed at the worst possible moment.

**2. It produced a hybrid, not the previous tree.** `git checkout <sha> -- .` restores
what exists in `<sha>` and cannot remove what was ADDED since, so a rollback to a commit
predating a file leaves that file at its current content. Measured: rolling back to the
`factory init` commit produced a release still containing the feature being rolled
back, reported as `ROLLED_BACK`.

**3. It failed opaquely when the target was too old.** Removing files added since can
remove the deploy script itself, and the failure read as `python: can't open file ...`
-- a broken deploy rather than a target that predates it.

**4. The pointer did not move, so the NEXT deploy was a silent no-op.** `record()`
appended to the history log while `deployed.json` was written only on the forward path.
A rollback therefore left the pointer naming the sha it had just rolled AWAY from, and
the next deploy read that pointer, decided it was already current, and did nothing. So
after a rollback you could merge the fix, run the deploy, be told everything was
current, and still be serving the rolled-back build. **The mechanism that exists to
prevent redundant work prevented the one deploy that mattered.**

**The shape of the fourth one is worth keeping**: two writers of one fact. The history
and the pointer are the same claim recorded twice, and only one path updated both.
`record()` now owns both.

**The general lesson.** Every one of these is invisible to reading. The code is short
and each line is defensible; the faults are in what the sequence LEAVES BEHIND, which
only running it can show. A recovery path that has never been executed is not a recovery
path, it is a plan -- and the day you find out is the day you needed it.


## The alarm reported success for messages the server rejected

Written hours after the incident above, while removing exactly this pattern from
everything else.

`.factory/notify.sh` announced `NOTIFIED via ntfy` whenever curl exited 0. `curl -sS`
returns 0 on a 4xx or 5xx -- it only reports transport failures, not HTTP ones -- so a
message the server REJECTED was reported as delivered. Measured: `curl -sS` against a
URL that errors exits 0; `curl -sS --fail` exits 22.

The cost is asymmetric in the worst direction. This is the escalation channel: the one
component whose entire job is to tell a human something went wrong. A false success here
does not degrade the system, it removes the last thing standing between a stopped
factory and nobody knowing.

**It was verified the wrong way, too.** The first check ran the script, read
`NOTIFIED via ntfy`, and concluded the path worked. That is trusting the thing under
test to report on itself. The honest check polls the message back OFF ntfy and compares
it, which is what finally showed the delivery was not happening.

**The rule.** When a component reports its own success, the test must observe the
EFFECT somewhere else. `exit 0` is a claim, and for anything that crosses a network it
is a claim about the local end only.

## Both end-to-end rungs were deleted and every check stayed green

Found while moving the journeys from Python to markdown, by doing the move and then
running everything before writing the replacements.

`harness/e2e.py` and `.factory/holdout/run.py` were deleted outright. Then:

```
python template/factory/_selftest.py   ->  SELFTEST_PASSED checks=197
python bin/audit.py                    ->  No findings. 0 failing, 0 warnings
```

Both green, with the gate's end-to-end rung and its entire independence line gone.

**Why neither saw it.** The self-test pins the machinery that decides a verdict: the
lock, the state machine, the ratchet, the markers. The auditor pins cross-file
invariants about that machinery. Both of them check the thing that reads the evidence.
Neither checked that anything still PRODUCES the evidence, so the rungs were outside
the reach of every instrument aimed at the gate.

It would not have surfaced at the next gate run either, because a rung that does not
exist prints `HOLDOUT_ABSENT` and continues. That branch is correct on a fresh install
and indistinguishable from this.

**The fix** is `check_agent_rungs_wired` in `bin/audit.py`, and it makes three separate
claims rather than one: the files exist, `ci.py` actually calls them, and the holdout
spec is inside the directory every builder node is denied. The third is the one worth
having. Moving those scenarios under `harness/` would leave every other check green
while quietly ending the independence the auto-merge rests on. All six mutations of it
were watched going red before it was believed.

**The rule.** An instrument aimed at "does the checker work" does not cover "is the
checker still plugged in". Assert the presence and the wiring of every rung
separately, because absence is the one failure that produces no output to inspect.

## Two guards that made each other invisible

The agent-driven rungs validate the report before counting it, and two of those
checks reject an empty report: `if not groups` and `if assertions == 0`. A mutation
removing either one left the self-test green, and both showed up as ESCAPED.

Neither guard was broken. The tests were: they asked whether `_validate` raised, and
with either guard removed the other one still raised. Redundant protection reads as
tested protection when the check is only "did it refuse".

Two different fixes, because the two cases are not the same:

- `if not groups` is genuinely reachable, so the test now asserts WHICH guard fired by
  matching the message. Delete the guard and the rejection still happens, with a
  different reason, and the check goes red.
- `if assertions == 0` is provably unreachable, since every group is already required
  to carry at least one assertion. It is kept as a backstop for whoever loosens the
  guard above it, and it is now marked NOT_APPLICABLE in the mutation set by name. A
  set that quietly drops the defects it cannot catch is a set whose score means
  nothing.

**And one more thing the same run showed.** The mutation for "observed may simply echo
expected" first used a partial anchor, which left an unbalanced parenthesis. It
reported CAUGHT, because the interpreter refused the file before the check it was
aimed at ever ran. A mutation that breaks the syntax measures the parser. Anchor on the
whole line.

## The journey agent shipped with a permission mode that could not run curl

The first live run of the agent-driven end-to-end rung, in a clean room, against a
working app.

`AGENT_CLIS` defaulted to `claude -p --permission-mode acceptEdits`. That mode accepts
file edits and still refuses `Bash`, so the agent could not issue a single request. It
reported 13 of 13 assertions failed, and every one of them named exactly why:

```
observed: No request was ever sent. Every HTTP client invocation available to this
agent was refused by the permission layer before execution. Bash 'curl -s -i
http://127.0.0.1:58484/health' -> 'This command requires approval'; same with the
absolute path, with PowerShell's Invoke-RestMethod, and with python -c. Non-network
commands in the same session ran fine, so the shell works and curl is installed.
This is an e2e driver/environment failure, NOT evidence about the application's
behavior - the app's state is unknown and unverified.
```

**Two separate results, and the good one is easy to miss.** The agent could not check
anything and did not claim it passed. That is the single behaviour this whole rung
rests on, and it held on its first contact with a hostile environment.

The bad one is that the default we shipped was the hostile environment. `--allowedTools
Bash,Read,Write` alongside `acceptEdits` was measured working; `bypassPermissions` also
works and grants more than the rung needs.

**And the naming was wrong even though the verdict was right.** The gate printed
`GATE_FAILED: e2e`, which reads as a broken product, when the product was fine. So a
result may now carry `blocked: true`, and the harness RE-PROBES the app itself before
believing it: app alive means the agent's environment is at fault and it is named
`e2e-harness`; app dead means the product is at fault and it stays a failed assertion.
Both branches fail the gate, so there is nothing to be gained by claiming to be
blocked. Only the name moves, and it moves on evidence the harness gathered itself.

## The ratchet would have climbed to the luckiest run

Found by planting a defect in a clean room and watching the agent-driven rung catch it.

The defect capped `/stats` total at four. All 30 unit tests passed, because none of
them uses five tasks. The journey agent ran the two journeys as written, and then went
further on its own:

```
observed: DEFECT. stats.total saturates at 4. At 3 tasks GET /stats -> total 3
(correct). Added 2 more: GET /tasks returned 5 task objects but GET /stats ->
{"total": 4, ...} -- total undercounts by 1. Added a 6th: still 4. The journey as
written only ever holds 1 task, so steps 3 and 5 pass and hide this.
```

That is the whole argument for prose over a script, in one paragraph. A scripted
end-to-end with those exact steps would have passed.

**And it broke the ratchet.** Three clean runs of the same unchanged code:

| run | journeys | e2e assertions | scenarios | holdout assertions |
|---|---|---|---|---|
| 1 | 2 | 12 | 2 | 14 |
| 2 | 2 | 13 | 2 | 11 |
| 3 | 2 | 13 | 2 | 11 |

The group counts do not move. The assertion counts do, by up to three, on code that
did not change. A floor raised to 14 fails the very next run at 11.
`merge.raise_floor` raises
each floor to what the gate just observed, so an assertion floor climbs to whichever
run was most thorough and then fails every ordinary one after it. A helpful extra check
turns into a factory that stops merging, two laps later, for a reason nobody would
connect to it.

**The fix is to ratchet what a human controls.** The floors for these two rungs count
JOURNEYS and SCENARIOS, which are headings in a file on the protected list, so the
number is stable and deleting one is already an auto-reject. Assertion counts are still
printed on every run: a signal to read, not a number to hold a merge against.

**The general shape.** A ratchet needs a monotone input. Anything a model chooses per
run is not monotone, and pinning a floor to it converts variance into a failure that
arrives later, somewhere else, with no obvious cause.

## A JavaScript repo was checked by `python -m compileall`

Found by handing the README's own prompt to an agent and letting it install into a
Node repo it had never seen.

`detect()` sets `static` and `unit` from the language it finds. For a Node package with
no `typecheck` script and no `tsconfig.json` it finds neither, so both were left at the
values the template ships: `python -m compileall -q .` and `python -m pytest -q`.

`python -m compileall -q .` in a JavaScript repo compiles zero files and exits 0. The
gate printed `STATIC_OK` on a repository with no static checking of any kind. That is
exactly the "empty is not pass" failure the rest of this system is built to prevent,
shipped as a default and green from the first run.

A default from another language is worse than no default. `init` now clears them and
says so, and `ci.py` reports the rung as `STATIC_SKIPPED`, which is a fact in the log
rather than a pass.

The same run left `driver` at `http` for a library with no server, and
`library.import_check` at `python -c "import app"` in a Node repo. The import check is
set per language now; the driver is question 4 of the setup interview, because whether
software is reached over HTTP, as a command, or as an import is not reliably readable.

**And the fix shipped a bug of its own**, worth recording because it is a shape that
recurs: the loop was written `for step in ("static", "unit")`, and `step()` is the
module-level printer. The loop variable shadowed it for the whole function, so `init`
died with `UnboundLocalError` a hundred lines earlier, on a line nothing had touched.
Only running it found that. Reading the diff would not have.

## The import check that could not fail

Found by an agent doing a fresh install from the README's own prompt, into a Node
library. It reported the shipped `library.import_check` was a no-op, and it was right
about more than it knew.

Commands are split with `shlex.split(cmd, posix=False)`, which is correct on Windows
because it leaves backslashes in paths alone. It also leaves the QUOTES attached to
every token, and the code stripped them from `argv[0]` only. So the shipped default:

```
"import_check": "python -c \"import app\""
```

reached Python as three tokens, `python`, `-c`, `"import app"`. Python evaluated the
string literal `"import app"`, which is a valid expression that does nothing, and
exited 0.

Measured:

```
python -c "import definitely_not_a_module_xyz"   ->  exit 0
node   -e "require('./nope')"                    ->  exit 0
```

**`APP_STARTED driver=library` was unconditional.** That marker is one of the two the
gate requires as proof the software ran at all, and for every library-driver project it
had always been true regardless of whether anything imported. A missing module, a
syntax error in the entry point, a broken package.json main: all of them passed.

The docstring on the function had the whole argument written out, for `argv[0]`, and
the same reasoning was never carried to the rest of the line.

**Two things it cost, both of them mine.** Twenty minutes before this I had added a
Node import check with the identical shape, having just fixed a different "empty is not
pass" default in the same function. And the check I wrote to prove the fix worked would
have passed either way until I ran it against a module that does not exist.

`unquote()` now strips one layer of matching quotes from every token, in both
`appproc._argv` and `ci.resolve`, which is what posix mode would have done without
giving up the backslashes. Four self-test checks and two mutations hold it.

**The rule.** When a check can only report success, it is not a check. The way to find
out which kind you have is to point it at something that must fail and watch.

## The check that confused housekeeping with evidence

Added in the morning, fired on its first real run, and was wrong.

The agent-driven rungs let the agent restart the app when a journey cannot be true
otherwise. Nothing checked that it came back, so a report could describe an app on a
port the gate never saw, with a process left holding it. The check added for that
failed the rung whenever an all-green report was followed by a silent port.

It fired on a clean run: `every assertion passed, but the app is not answering ...
GATE_FAILED: e2e-harness`. The report was fine. The agent had simply stopped the
process last, which is a legitimate way for a restart-flavoured journey to end.

**The assertions and their observed values are the evidence. Whether a port is open
afterwards is housekeeping.** Conflating the two turned a correct run into a red gate,
which is the same class of error as the opposite one and costs the same trust.

So the rung puts the app back rather than complaining about it, and only a failure to
RESTART is a failure. It is still named `e2e-harness` rather than `e2e`, because a
harness that cannot get the app back is not a broken product.

Confirmed on the next run, which reproduced the same end state and handled it:

```
AGENT_RUNNING rung=e2e cmd=claude
APP_DOWN_AFTER_RUNG restarting it for the next one
APP_STARTED port=54373
E2E_PASSED journeys=2 steps=12
AGENT_RUNNING rung=holdout cmd=claude
```

**Worth noting about the sequence.** The over-strict version was written to close a
real hole, in the same sitting, and it looked obviously correct in the diff. One run
against a real agent was what separated them.

## Four orphaned processes holding four ports

Found by looking at the process table after a morning of gate runs, not by anything
failing.

`HttpApp.__exit__` terminated `self.proc`, which frees the port only when the process
on it is the one this object spawned. A journey may restart the app, and the
replacement is untracked: the original dies, the replacement keeps the port, teardown
terminates a corpse, and everything reports clean.

Nothing broke yet because every run picks a fresh dynamic port. The bill arrives later,
as `[Errno 10048] address already in use` on a machine that has been running laps for a
week, from a factory that believes it tore everything down. The file's own module
docstring already said *"TEAR DOWN on every path including failure, or a leaked process
holds the port and poisons the next lap"*, and the code freed a process rather than a
port.

Teardown now sweeps whatever is listening, excluding the pid it already terminated.
Verified against a deliberate impostor: start the app, kill the tracked process, start
an untracked replacement on the same port, tear down, and watch the replacement die.

**Why this one is worth writing down.** It produced no failure, no log line and no
symptom. The only way it surfaced was reading the process table on the way past, and
the only reason to do that was having just written about leaked processes in another
incident.

## A thirty-second GitHub wobble wrote a needs-human entry and paged a human

Caught by the operator monitor, not by anything failing: `NEEDS_HUMAN ... the
dispatcher itself failed: gh issue list ... HTTP 503: Service Unavailable`.

One tick, one 503 from GitHub's GraphQL endpoint. The dispatcher recorded a
`DISPATCHER_FAULT`, appended to `needs-human.md`, and sent a notification. The very
next tick, sixty seconds later, succeeded. Exactly one fault in the whole log.

Every individual decision there was defensible. The dispatcher could not read the
queue, and this system's rule is that an unknown is never a pass, so it stopped
rather than guessing. What was wrong was the THRESHOLD: it treated a blip in
somebody else's service as an incident on the first occurrence.

**The cost is not the log line, it is the channel.** This runs unattended for days.
A notifier that fires on every upstream hiccup is a notifier people mute, and a
muted channel is precisely the failure the escalation path exists to prevent. It
also leaves a permanent record a human has to clear, for something that had already
fixed itself before anyone read it.

`state.gh()` now retries a transient failure up to three times with a short backoff,
and says `GH_RETRY` when it does rather than quietly taking four seconds. If GitHub
is genuinely down the attempts exhaust and it escalates, which is correct.

**The list is deliberately narrow, and the second direction is the one worth
testing.** 5xx, timeouts, connection resets and DNS failures are "ask again". A 404,
a 422, a bad token and a rejected merge are ANSWERS, and retrying an answer asks the
same question three times before reporting the same thing, slower. The merge
refusal is the one that would actually hurt: re-attempting a merge the base branch
has already rejected. All four cases are pinned in `_selftest.py` and two mutations
hold them.

**Worth noting where this came from.** Nothing broke. The factory behaved exactly as
designed, the monitor reported it exactly as designed, and the design was wrong. The
only reason it surfaced was an alert firing on a machine nobody was watching.
