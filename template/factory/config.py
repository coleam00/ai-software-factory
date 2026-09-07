"""THE CONFIGURATION SURFACE. This is the file you edit.

Everything project-specific in the factory reads from here. If you find yourself
editing another module to change a path, a command or a marker name, that is a bug
in this file and it should grow a setting instead. The point of concentrating it is
that six months from now you can see your whole factory's configuration on one
screen.

Every setting is overridable from the environment with the same name, so a single
run can be driven differently without editing anything:

    FACTORY_AUTONOMY=1 python factory/dispatch.py

Keep this file to assignments. No side effects, nothing that can fail, nothing that
prints -- it is imported by every other module and by workflow script nodes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# --- where we are -------------------------------------------------------------


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _repo_root() -> Path:
    """The git top level of THIS checkout -- which is the worktree when we are in one.

    Resolved once at import. Everything about the code under test reads from here:
    the diff, the guard's range, the harness. That is what you want -- a node
    validating a branch must validate the branch it is standing in.
    """
    top = _git("rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else Path(__file__).resolve().parent.parent


def _shared_root() -> Path:
    """The MAIN checkout, even when this process is running inside a worktree.

    THE BUG THIS EXISTS TO PREVENT, and it is silent. Archon runs each workflow in
    its own git worktree, so `--show-toplevel` is the worktree -- and every piece of
    factory state written relative to it (the findings the fix node must read, the
    needs-human ledger, the dispatch locks, recorded assumptions) is deleted with
    that worktree the moment the run ends.

    The specific casualty is the fix loop: the validator records what it objected
    to, the worktree goes, and the fix node -- a separate run, later -- finds
    nothing. It does not crash. It re-reads the diff and invents an objection, so
    every fix attempt becomes a guess at what the validator wanted.

    `--git-common-dir` points at the ONE real .git directory shared by every
    worktree, so its parent is the main checkout. In a normal (non-worktree)
    checkout it is just `.git` and this returns the same thing as ROOT.
    """
    common = _git("rev-parse", "--git-common-dir")
    if not common:
        return _repo_root()
    path = Path(common)
    if not path.is_absolute():
        path = (_repo_root() / path).resolve()
    return path.parent.resolve()


ROOT = _repo_root()
SHARED = _shared_root()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# --- the agent ----------------------------------------------------------------
# The workflow engine. Installed by `factory init` if it was not already here,
# the same way installing OpenClaw gets you Pi: you asked for the factory, the
# engine is an implementation detail you are allowed to ignore until you want it.
ARCHON_BIN = _env("FACTORY_ARCHON_BIN", "archon")

# The workflow pack this factory dispatches. Five workflows, one job each.
#
# `fix` is its own workflow rather than a loop inside `validate`, and that is a
# holdout decision, not a structural preference: a fix that runs in the same process
# as the judgement it is answering can inherit the judge's reasoning. A separate
# dispatch gets a separate process, a separate worktree and a separate context, and
# the findings reach it as a file on disk rather than as ambient memory.
WORKFLOW_TRIAGE = _env("FACTORY_WORKFLOW_TRIAGE", "factory-triage")
WORKFLOW_IMPLEMENT = _env("FACTORY_WORKFLOW_IMPLEMENT", "factory-implement")
WORKFLOW_VALIDATE = _env("FACTORY_WORKFLOW_VALIDATE", "factory-validate")
WORKFLOW_FIX = _env("FACTORY_WORKFLOW_FIX", "factory-fix")
WORKFLOW_REGRESS = _env("FACTORY_WORKFLOW_REGRESS", "factory-regress")

# Flood protection, FACTORY_RULES section 1. It lives here rather than in the node
# script because a node runs under the engine, which does not pass this process's
# environment through undeclared -- so `os.environ.get` in a workflow script reads a
# variable nobody can set, silently keeps the default forever, and `archon workflow
# list` prints a warning about it that nobody connects to a cap that does not apply.
ISSUE_CAP_PER_DAY = _env_int("FACTORY_ISSUE_CAP_PER_DAY", 3)

# Model tiers, not literal model ids -- Archon resolves a tier against whatever
# provider is configured, so a factory written with tiers survives a provider swap.
#
# TWO SLOTS DECIDE QUALITY: the one that PLANS and the one that IMPLEMENTS. A
# premium model in ONE of them buys most of the quality of both. Zero premium slots
# is what actually costs you.
MODEL_PLAN = _env("FACTORY_MODEL_PLAN", "large")
MODEL_BUILD = _env("FACTORY_MODEL_BUILD", "medium")
MODEL_JUDGE = _env("FACTORY_MODEL_JUDGE", "medium")
MODEL_SORT = _env("FACTORY_MODEL_SORT", "small")

# --- the validation harness ---------------------------------------------------
# THE MOST IMPORTANT SETTING IN THIS FILE.
#
# One command that runs your whole gate and prints the markers below. It is
# component 5 and it is the part this template deliberately does NOT give you:
# what "working" means for your app is the one thing nobody can write in advance.
#
# It must exit non-zero when the software is broken, and it must print a positive
# marker for every check that RAN.
VALIDATE_CMD = _env("FACTORY_VALIDATE_CMD", "python harness/ci.py")

# The cheap subset an implementing node may run on itself while it works. Keep it
# fast, and keep it a STRICT SUBSET -- never a check the full run does not have.
# Nothing downstream trusts what this said; the full gate re-runs everything.
VALIDATE_QUICK = _env("FACTORY_VALIDATE_QUICK", "python harness/ci.py --quick")

# EMPTY IS NOT PASS, expressed as data.
#
# Every marker named here must appear in the run log or the gate refuses to merge.
# A check that never ran produces no failures, and code that asks "did anything
# fail?" reads that as success -- so the gate never asks that question. It asks
# "did this specific thing report that it ran?"
#
# APP_STARTED and E2E_PASSED are not negotiable: they are the two gates that must
# be code in every factory. Add one marker per check family you build.
# WHICH MARKER MEANS WHAT, named in config rather than hardcoded in the checks.
#
# The doctor and the self-test used to require the literal strings `APP_STARTED` and
# `E2E_PASSED`, which are this TEMPLATE's vocabulary, not a universal one. A repo whose
# harness emits `PLAYTHROUGH_PASSED` was told its markers were "not negotiable" and
# refused a dial it had fully earned -- the product hardcoding its own example, exactly
# like assuming the default branch is called main.
#
# What is genuinely not negotiable is that SOMETHING proves the application ran and
# SOMETHING proves an end-to-end journey was asserted. The names are the repo's.
# WHERE THE TWO IRREPLACEABLE FILES LIVE. The template writes Python at these paths;
# a repository in another language puts them somewhere else, and the doctor asking for
# a particular filename told such a repo it had no end-to-end path while it was
# staring at one. What must exist is an end-to-end journey and a holdout the builder
# cannot read -- not two particular filenames.
E2E_FILE = ROOT / _env("FACTORY_E2E_FILE", "harness/END-TO-END.md")
HOLDOUT_FILE = ROOT / _env("FACTORY_HOLDOUT_FILE", ".factory/holdout/HOLDOUT.md")

# NO CONSOLE WINDOWS, and this is not cosmetic.
#
# Every helper this factory shells out to -- gh, archon, git, npm -- is an .exe or a
# .cmd, and on Windows each one allocates its own console window. A dispatcher on a
# ninety-second timer makes a dozen of those per tick, so an unattended factory becomes
# windows flashing over whatever its owner is trying to do, all day and all night.
#
# The entire promise of this thing is that it runs while nobody is watching. A factory
# that interrupts you every ninety seconds is a factory you switch off, which makes this
# a correctness problem about the product rather than a nicety.
#
# PATCHED HERE, ONCE, because every factory script imports this module and none of them
# should have to remember. CREATE_NO_WINDOW only, deliberately NOT DETACHED_PROCESS: a
# detached child stops dying with its parent, and a dispatch that outlives the tick that
# started it is a lock nobody releases.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

if NO_WINDOW:
    _subprocess_run = subprocess.run
    _subprocess_popen = subprocess.Popen

    def _quiet_run(*a, **kw):  # type: ignore[no-untyped-def]
        kw.setdefault("creationflags", NO_WINDOW)
        return _subprocess_run(*a, **kw)

    class _QuietPopen(_subprocess_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw.setdefault("creationflags", NO_WINDOW)
            super().__init__(*a, **kw)

    subprocess.run = _quiet_run  # type: ignore[assignment]
    subprocess.Popen = _QuietPopen  # type: ignore[assignment]

MARKER_APP_RAN = _env("FACTORY_MARKER_APP_RAN", "APP_STARTED")
MARKER_E2E = _env("FACTORY_MARKER_E2E", "E2E_PASSED")
# The markers that become mandatory at level 3, when nobody reads the diff.
MARKERS_LEVEL3 = _env("FACTORY_MARKERS_LEVEL3", "HOLDOUT_PASSED MUTATIONS_OK").split()

REQUIRED_MARKERS = _env(
    "FACTORY_REQUIRED_MARKERS", "PROTECTED_OK APP_STARTED E2E_PASSED GATE_OK"
).split()

# The ratchet. A floor on how many checks must RUN, kept in a file the factory is
# not allowed to edit, so lowering it is a deliberate human commit.
FLOOR_FILE = ROOT / _env("FACTORY_FLOOR_FILE", ".factory/locks/floor.json")

# SLACK BLOCKS THE DIAL. The gap between observed and floor is exactly the number
# of assertions that can be deleted with the gate still green, and it GROWS as the
# harness improves, because raising the floor is a protected edit the factory
# cannot make. Printing it as a note and carrying on is how the hole widens
# forever, so here it pins autonomy instead: any slack caps the run at level 2.
# SLACK NO LONGER CAPS AUTONOMY, because merge.py now CLOSES the slack instead.
#
# It cannot both hold the merge and be closed by the merge: the gate decides
# `automerge` before merge.py runs, and at that moment the floor has not been raised
# yet, so slack is always present on any PR that adds a check. Leaving this true after
# adding the auto-raise would deadlock every such PR forever -- held on a gap that only
# the merge it is blocking could close.
#
# The risk it was guarding is real and is now handled by mechanism rather than by
# stopping: slack is exactly how many assertions could be deleted with the gate still
# green, and it used to GROW because only a human could close it. It cannot grow now,
# because every merge closes it. If a raise ever fails (a push that did not land), the
# gap survives, the gate still PRINTS `RATCHET_SLACK=` on every run, and `doctor`
# reports it -- so it is visible without being a brake.
SLACK_CAPS_AUTONOMY = _env("FACTORY_SLACK_CAPS_AUTONOMY", "false").lower() == "true"

# --- the dial -----------------------------------------------------------------
# 0  workflows exist, run by hand            <- where every factory starts
# 1  accepted issue -> branch and PR open
# 2  + the validator runs and writes a verdict
# 3  + the validator AUTO-MERGES on green structural gates   <- THE TARGET
# 4  + it triages its own issues, and the scheduled regression files its own bugs
# 5  + it writes its own issues from the mission
#
# LEVEL 3 IS THE DESTINATION and 1 and 2 are the way there, not places to stop: at
# 2 a person still merges every PR, which is the bottleneck the factory was built
# to remove. Everything expensive here -- the holdout, the mutation set, the
# ratchet, the two gates that are code -- exists to earn 3.
#
# The SHIPPED value is 0, deliberately. A fresh install must not auto-merge before
# a single lap has been proven by hand, and `factory doctor` refuses 3 while
# there is no holdout, so the dial cannot outrun the evidence.
AUTONOMY = _env_int("FACTORY_AUTONOMY", 0)

# THE DIAL DECIDES WHICH MARKERS ARE MANDATORY, and this is not a convenience.
#
# The holdout and the mutation set are the entire argument for merging code nobody
# read: one is a wall of assertions the builder cannot see, the other is the only
# evidence any of the checks can fail at all. Below level 3 a person reads the diff,
# so neither is load-bearing and a repo part-way through the build should not be
# blocked on checks it has not written yet.
#
# At level 3 they become the load-bearing checks, so their markers become required.
# Without this, a holdout that silently stops running -- renamed, crashed on import,
# skipped by a bad path -- leaves a gate that still goes green, which is the exact
# "empty is not pass" failure the marker list exists to prevent, aimed at the one
# check that justifies the whole arrangement.
if AUTONOMY >= 3:
    for _m in MARKERS_LEVEL3:
        if _m not in REQUIRED_MARKERS:
            REQUIRED_MARKERS.append(_m)

MAX_PARALLEL = _env_int("FACTORY_MAX_PARALLEL", 1)
MAX_FIX_ATTEMPTS = _env_int("FACTORY_MAX_FIX_ATTEMPTS", 2)
TRIAGE_BATCH = _env_int("FACTORY_TRIAGE_BATCH", 10)

# How long a dispatch lock may outlive the run that took it. A lock is reaped early
# when its recorded PID is gone -- that is the common case: a reboot, a closed
# terminal, a killed run, none of which run a cleanup handler -- with GRACE minutes
# of slack so a run that has not yet settled is never reaped out from under itself.
# STALE is the fallback for when the PID cannot be checked at all.
LOCK_STALE_MINUTES = _env_int("FACTORY_LOCK_STALE_MINUTES", 180)
LOCK_GRACE_MINUTES = _env_int("FACTORY_LOCK_GRACE_MINUTES", 5)

# --- the stop button ----------------------------------------------------------
# Two of them, on purpose, because they fail in different places. The local file
# works with the network down; the remote label is reachable from a phone.
#
# THE REMOTE HALF FAILS CLOSED. "Remove a label to stop" is the obvious design and
# it is backwards: a missing label cannot be told apart from an API call that
# failed to list it, so a network blip reads as "carry on". This one is a label you
# ADD, and any error reading it counts as stopped.
# SHARED: a stop button that only works inside the worktree that is already running
# is not a stop button.
STOP_FILE = SHARED / _env("FACTORY_STOP_FILE", ".factory/STOP")
STOP_LABEL = _env("FACTORY_STOP_LABEL", "factory:stop")

# --- limits -------------------------------------------------------------------
# Crude, and it works. An unsupervised agent will otherwise ship a 3,000-line PR
# nobody can review, and "nobody can review it" is where a factory stops being
# auditable even in principle.
# THE SIZE CAP COUNTS PRODUCTION LINES, NOT TESTS.
#
# It exists so nothing ships that a person could not review even in principle. Tests
# are not that risk -- they are the evidence the rest of the diff is safe -- and
# counting them against the cap taxes the one thing this whole system is built to
# encourage. PR #14 was rejected at 515 lines of which 404 were tests: 141 lines of
# production code, blocked for being well tested. The rule was punishing the behaviour
# it exists to protect.
#
# TOTAL_CAP is the backstop, so "put it in tests/" is not a way around the cap. A diff
# over the total is unreviewable no matter what it is made of.
SIZE_CAP = _env_int("FACTORY_SIZE_CAP", 500)
TOTAL_CAP = _env_int("FACTORY_TOTAL_CAP", 1500)

# THE SCOPE LEASH, and it is a FILE count rather than a line count on purpose. The
# failure it catches is not size: a refactor node with no scope grows a six-file PR
# into eleven and introduces a bug in one of the five it was never asked to touch,
# while staying well under the line cap the whole way. Set to 0 to disable.
FILE_CAP = _env_int("FACTORY_FILE_CAP", 12)

# --- what THIS project protects -----------------------------------------------
# ADD ANYTHING WITH A BLAST RADIUS YOU CANNOT ABSORB. Auth modules, rate-limit
# constants and their enforcement path, payment code, migrations, Dockerfiles,
# deploy/ and infra/. Everything the factory protects in EVERY repo -- governance,
# the machinery, the harness, the locks, the holdout, secrets -- is already in
# `guard.PROTECTED` and is not your problem.
#
# THIS LIST AND FACTORY_RULES SECTION 5 ARE ONE FACT WRITTEN TWICE, so `doctor`
# checks they agree. Section 5 is what the planner and the judge read; this is what
# the gate can actually enforce, and a path named only in the prose is a rule the
# gate prints PROTECTED_OK straight through.
#
# IT LIVES HERE RATHER THAN IN guard.py, AND THAT MOVE WAS PAID FOR. It used to be a
# `PROTECTED +=` block inside `factory/guard.py`, which `bin/sync-to.py` overwrites as
# machinery -- so the first sync after an operator added a path deleted it, the guard
# went on printing PROTECTED_OK, and nothing anywhere went red. Reproduced on flagpole
# with `app/rollout.py`, a path a planner had found missing hours earlier. Anything an
# operator edits belongs in the one file the sync will not touch.
#
# Glob patterns, matched against repo-relative paths with fnmatch. `**` for a subtree.
PROTECTED_EXTRA: list[str] = [
    # "app/auth/**",
    # "app/rate_limit.py",
    # "deploy/**", "infra/**",
    # "Dockerfile", "docker-compose*.yml",
    # "migrations/**",
]

# Optional: a whole file category banned by extension rather than by intent, for the
# case where a mission says "no imported assets" or "no vendored binaries". Checked
# by extension because "just one placeholder" is how the exception becomes the rule.
BANNED_CATEGORIES: list[str] = [
    # "*.png", "*.jpg", "*.wav", "*.mp3", "*.ttf",
]

# WHERE THIS PROJECT'S TESTS LIVE, because the size cap counts production lines and
# not tests, and "a test" is spelled differently in every language. The shipped list
# covers `tests/` and the JS/TS suffixes; a Go repo needs `*_test.go`, a Rust one
# `**/tests/**`, a Java one `src/test/**`. Get this wrong and the cap taxes the one
# behaviour the whole system exists to encourage.
TEST_PATHS_EXTRA: list[str] = [
    # "*_test.go", "**/*_test.go",
    # "src/test/**",
]

# --- paths --------------------------------------------------------------------
# The holdout: assertions the builder is blocked from READING, not merely from
# editing. Enforced with the agent's own deny list, because a sentence in a prompt
# is not enforcement.
HOLDOUT_DIR = ROOT / _env("FACTORY_HOLDOUT_DIR", ".factory/holdout")

# Config files that must be git-ignored before any node that can commit runs. An
# empty `git check-ignore` result means your next run publishes your key.
SECRET_FILES = _env(
    "FACTORY_SECRET_FILES", ".env .env.local secrets.json credentials.json .factory/cron.env"
).split()

# SHARED, not ROOT. Every one of these must outlive the worktree that wrote it --
# see _shared_root() for the failure that taught this. The holdout and the floor
# stay on ROOT because they are checked-out FILES that the run under test must see
# at the revision under test; these are RUNTIME STATE that belongs to the factory.
RUNS_DIR = SHARED / ".factory/runs"
LOCKS_RUNTIME = SHARED / ".factory/locks-runtime"
NEEDS_HUMAN = SHARED / ".factory/needs-human.md"
FINDINGS_DIR = SHARED / ".factory/findings"
DECISIONS_FILE = SHARED / ".factory/decisions.md"
ASSUMPTIONS_DIR = SHARED / ".factory/assumptions"
TRIGGER_FILE = SHARED / ".factory/trigger.json"

# --- deployment ---------------------------------------------------------------
# THE LOOP IS NOT CLOSED UNTIL A STRANGER CAN SEE THE CHANGE. If merging does not
# put code in front of a user, you built a PR generator with extra steps.
#
# `deploy` REFUSES to move the pointer when HEALTH_CMD is empty, on purpose: a
# deploy with no health check is a deploy that cannot fail, and a step that cannot
# fail is a comment.
DEPLOY_CMD = _env("FACTORY_DEPLOY_CMD", "")
HEALTH_CMD = _env("FACTORY_HEALTH_CMD", "")
HEALTH_MARKERS = _env("FACTORY_HEALTH_MARKERS", "").split()

# --- the trigger --------------------------------------------------------------
# Slower than feels right: a fast loop multiplies the cost of a mistake before you
# have noticed the mistake.
INTERVAL_MINUTES = _env_int("FACTORY_INTERVAL_MINUTES", 30)
def _base_branch() -> str:
    """The branch everything merges into, DETECTED rather than assumed.

    `main` was hardcoded in a dozen places -- the merge refused any PR whose base was
    not literally "main", and the deploy poller read `origin/main` or refused. On a
    repository using `master`, or `develop`, or a release branch, this product
    installed cleanly, audited green, and could never merge anything.

    `origin/HEAD` is what the remote itself says its default is, which is the only
    answer that is not a guess. The fallbacks exist for a clone that never set it.
    """
    import subprocess as _sp

    override = _env("FACTORY_BASE_BRANCH", "")
    if override:
        return override
    try:
        p_ = _sp.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        if p_.returncode == 0 and p_.stdout.strip():
            return p_.stdout.strip().split("/", 1)[-1]
        for candidate in ("main", "master"):
            p_ = _sp.run(
                ["git", "rev-parse", "--verify", "--quiet", f"origin/{candidate}"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
            )
            if p_.returncode == 0:
                return candidate
    except (OSError, _sp.SubprocessError):
        pass
    return "main"


BASE_BRANCH = _base_branch()

REGRESS_CRON = _env("FACTORY_REGRESS_CRON", "0 6 * * 1")
TASK_NAME = _env("FACTORY_TASK_NAME", f"factory-{ROOT.name}")

# --- escalation ---------------------------------------------------------------
# THE ONLY THING THAT REACHES YOU. Everything else is written to disk and waits.
#
# DEFAULTS TO `.factory/notify.sh`, which init installs, because an unset channel
# means a needs-human escalation appends to .factory/needs-human.md and NOTHING ELSE
# HAPPENS -- on an unattended system that means you find out when you next remember to
# look, which is the "file nobody opens" failure sitting inside the escalation path.
#
# The shipped script writes that log first and unconditionally, then tries whatever is
# configured (FACTORY_NTFY_TOPIC, FACTORY_WEBHOOK_URL) and finally a desktop
# notification, and it says out loud when it could not deliver. Override this to point
# somewhere else; do not point it back at a file.
#
# The message arrives on STDIN; argv[1] is the target, for routing or a subject
# line only. Every worked example reads stdin. Writing your own and reaching for
# "$1" by reflex gets you a 3am alert whose entire body is `gh:pr:14`, which tells
# you something is wrong and not what.
#
#   NOTIFY_CMD = 'curl -s -d @- https://ntfy.sh/my-factory-topic'
#   NOTIFY_CMD = 'tee -a /var/log/factory-escalations.log'
#
# Keep it QUIET. If everything notifies you will mute it, and then nothing does.
NOTIFY_CMD = _env("FACTORY_NOTIFY_CMD", "bash .factory/notify.sh")

# --- state --------------------------------------------------------------------
# `github` wherever an origin remote exists. There is no second backend: two
# implementations of one state machine is two that drift, and the one nobody runs
# is always the one that is wrong.
LABEL_PREFIX = "factory:"
PRIORITIES = ["critical", "high", "medium", "low"]


def summary() -> str:
    """One screen, for `factory doctor` and for a human at 3am."""
    return "\n".join(
        [
            f"root            {ROOT}",
            f"autonomy        {AUTONOMY}",
            f"validate        {VALIDATE_CMD}",
            f"quick           {VALIDATE_QUICK}",
            f"markers         {' '.join(REQUIRED_MARKERS)}",
            f"size/file cap   {SIZE_CAP} lines / {FILE_CAP} files",
            f"parallel        {MAX_PARALLEL}",
            f"fix attempts    {MAX_FIX_ATTEMPTS}",
            f"holdout         {HOLDOUT_DIR}",
            f"stop file       {STOP_FILE}",
            f"notify          {NOTIFY_CMD or '(unset -- escalations wait in .factory/needs-human.md)'}",
            f"models          plan={MODEL_PLAN} build={MODEL_BUILD} judge={MODEL_JUDGE} sort={MODEL_SORT}",
        ]
    )


if __name__ == "__main__":
    print(summary())
