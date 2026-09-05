"""Commit the fix, push it, count the attempt, hand it back.

THE ORDER IS THE WHOLE FILE, and it is the order that survives each step failing:

 1. Assert the tree changed. An empty diff does not address a finding, and a fix node
    that was denied a tool exits 0 having changed nothing.
 2. Commit and push. Until this succeeds the validator has nothing new to look at.
 3. BUMP THE ATTEMPT COUNTER. If this is skipped, the cap is never reached and the PR
    ping-pongs until the budget is gone.
 4. ONE transition, to `open`.

Step 4 used to be two -- `validating` then `open` -- and the second was illegal.
Transition tables refuse illegal moves, the workflow died on that line, and no
escalation ran: no needs-human, no notification, nothing in the log but one line. The
PR stayed in `validating`, which the dispatcher does not look at, so it answered
`idle` from then on. A factory wedged that way is indistinguishable from a factory
with nothing to do.

`open` is the right target on its own: a fixed PR is a PR waiting to be validated.
The dispatcher picks it up and the validator sets `validating` itself, after the
tripwire, exactly as it does the first time round.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "factory"))

import config  # noqa: E402
import state  # noqa: E402

target = (os.environ.get("INPUTS_TARGET") or "").strip()
number = (os.environ.get("INPUTS_NUMBER") or "").strip()
branch = (os.environ.get("INPUTS_BRANCH") or "").strip()
attempt = (os.environ.get("INPUTS_ATTEMPT") or "1").strip()
artifacts = Path(os.environ.get("ARTIFACTS_DIR") or ".")


def die(msg: str) -> None:
    print(f"LAND_FIX_FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(config.ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


# --- 1. did anything change? ---------------------------------------------------
rc, dirty = git("status", "--porcelain", "--untracked-files=all")
if rc != 0:
    die(f"git status failed: {dirty}")
if not dirty.strip():
    # A CLEAN TREE IS NOT AN EMPTY FIX. The fix node is Archon's `archon-implement`,
    # which commits as it goes, so the ordinary success case arrives here with nothing
    # left to stage. Asking "is the tree dirty" answers a question that stopped being
    # the right one when the builder changed: what has to be true is that the BRANCH
    # carries something the remote does not.
    #
    # This is the same defect that was fixed in commit.py and missed here, and it cost
    # a ten-minute opus fix that was then thrown away as "changed nothing".
    rc_ahead, ahead = git("rev-list", "--count", f"origin/{branch}..HEAD")
    carried = rc_ahead == 0 and ahead.strip().isdigit() and int(ahead.strip()) > 0
    if not carried:
        report = artifacts / "fix-report.md"
        hint = f" It wrote {report}, so read that first." if report.exists() else ""
        die(
            "the fix node changed nothing. A finding is not addressed by an empty diff, "
            "and the usual cause is a denied tool or a finding the node decided it could "
            "not act on." + hint
        )
    note(f"FIX_ALREADY_COMMITTED {ahead.strip()} commit(s) ahead of origin/{branch}")

git("add", "-A")
# The builder is `archon-implement`, which commits as it goes, so by the time this runs
# the fix is usually ALREADY committed and there is nothing left to stage. That is
# success, not failure: what has to be true here is that the branch carries the fix and
# the remote is about to. Only a branch with nothing new to push is a dead fix.
rc_dirty, dirty = git("status", "--porcelain", "--untracked-files=all")
if rc_dirty == 0 and not dirty.strip():
    note("FIX_ALREADY_COMMITTED - the builder committed its own work; pushing it")
    rc, out = 0, ""
else:
    rc, out = git("commit", "-q", "-m", f"fix: address validator findings (attempt {attempt}) (#{number})")
if rc != 0:
    die(f"could not commit the fix: {out}")

rc, sha = git("rev-parse", "--short", "HEAD")
print(f"FIX_COMMITTED {sha}")

# --- 2. push -------------------------------------------------------------------
rc, out = git("push", "-q", "origin", f"HEAD:{branch}")
if rc != 0:
    die(f"could not push the fix to {branch}: {out}")
print(f"FIX_PUSHED {branch}")

# --- 3. count it ---------------------------------------------------------------
try:
    n = state.bump_attempt(target)
    print(f"ATTEMPTS={n}")
except Exception as e:  # noqa: BLE001
    die(
        f"the fix is committed and pushed but the attempt counter did not move ({e}). "
        f"Another fix would not be counted and the cap would never be reached, so this "
        f"PR could loop until the budget is gone. Fix the label by hand."
    )

# --- 4. hand it back -----------------------------------------------------------
report = artifacts / "fix-report.md"
body = ["**Factory fix**: attempt " + str(attempt), ""]
if report.exists():
    body.append(report.read_text(encoding="utf-8", errors="replace")[:5000])
else:
    body.append(f"Committed as `{sha}`. The fix node wrote no report.")
body += ["", "_Back to the independent validator. A fix is never self-certified._"]
try:
    state.comment(target, "\n".join(body))
except Exception as e:  # noqa: BLE001
    print(f"COMMENT_FAILED: {e}", file=sys.stderr)

if state.main(["set", target, "state=open"]) != 0:
    die(
        f"the fix is committed on {branch} but the PR could not be returned to 'open' "
        f"for re-validation. It would otherwise sit in a state the dispatcher does not "
        f"look at, which reads exactly like an idle factory."
    )

print(f"FIXED {target} -> needs-review (attempt {attempt}/{config.MAX_FIX_ATTEMPTS})")
