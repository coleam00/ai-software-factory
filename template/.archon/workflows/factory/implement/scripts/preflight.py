"""Everything that must be true before a single token is spent.

Four things, in order of how expensive they are to discover late:

 1. THE SECRET PRE-FLIGHT. `git check-ignore` over every credential-shaped path. An
    empty result means the commit step publishes your key, and on a public repo that
    is publication, not a mistake you can take back. Rotating afterwards is the
    cleanup, not the fix. This REFUSES to start rather than warning, because a
    warning is read after the push.
 2. The issue is actually buildable: it exists, it is `accepted`, and it is not
    parked at needs-human by something that happened since the dispatch.
 3. The issue is rendered to ONE file, ONCE, before any node runs. Every node opens
    the same path, so every node judges the same text -- re-fetching per node lets a
    mid-run edit change what the judge thinks was asked for.
 4. The state moves to `in-progress`, which is what stops a second dispatcher
    picking up the same issue while this one works.

Emits the identifiers every later node keys off, so nothing downstream re-derives
them and drifts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "factory"))

# nodeio FIRST, before anything that might print at import time: importing it
# redirects stdout to stderr for the whole process, so a library marker can never
# be mistaken for this node's value. emit() writes to the real stdout.
from nodeio import emit, note  # noqa: E402

import config  # noqa: E402
import guard  # noqa: E402
import state  # noqa: E402

target = (os.environ.get("INPUTS_TARGET") or "").strip()
artifacts = Path(os.environ.get("ARTIFACTS_DIR") or ".")
artifacts.mkdir(parents=True, exist_ok=True)


def die(msg: str) -> None:
    note(f"PREFLIGHT_FAILED: {msg}")
    sys.exit(1)


# --- 1. secrets ---------------------------------------------------------------
if guard.preflight() != 0:
    die("a credential-shaped path is not gitignored; see above")

# --- 1b. the holdout wall ----------------------------------------------------
# Before a single token is spent. The deny list on the AI nodes is honoured by some
# providers and ignored (with a warning) by others; removing the files from this
# worktree works on all of them. See factory/holdoutwall.py.
import holdoutwall  # noqa: E402

try:
    note(holdoutwall.wall())
except RuntimeError as e:
    die(f"could not wall off the holdout in this worktree: {e}")

# --- 2. the issue is buildable ------------------------------------------------
try:
    issue = state.fetch(target)
except Exception as e:  # noqa: BLE001
    die(f"could not read {target}: {e}")

if issue["_state"] == "needs-human":
    die(
        f"{target} is parked at needs-human. A node may never move an item out of "
        f"that state -- a human has to remove the label first."
    )
if issue["_state"] not in ("accepted", "in-progress"):
    die(
        f"{target} is '{issue['_state']}', expected 'accepted'. Something else moved it "
        f"between the dispatch and now; this run would build work the queue no longer "
        f"believes in."
    )

# --- 3. render the issue, once ------------------------------------------------
issue_md = artifacts / "issue.md"
issue_md.write_text(state.body_text(target), encoding="utf-8")

# Governance, alongside it, so every node reads the same copy and nobody has to
# remember to open three files.
for name in ("MISSION.md", "FACTORY_RULES.md", "CLAUDE.md", "AGENTS.md"):
    src = config.ROOT / name
    if src.exists():
        (artifacts / name).write_text(
            src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )
if config.DECISIONS_FILE.exists():
    (artifacts / "decisions.md").write_text(
        config.DECISIONS_FILE.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
    )

# --- identifiers ---------------------------------------------------------------
number = target.split(":")[-1]
title = (issue.get("title") or f"issue {number}").strip()
# Keyed on the NUMBER, not a slug of the title. Windows caps a path at 260
# characters and a worktree path plus a vendored file clears it easily; a slug also
# changes when somebody edits the title, and a branch that renames itself mid-lap is
# a branch the validator cannot find.
branch = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    cwd=str(config.ROOT), capture_output=True, text=True, encoding="utf-8", timeout=30,
).stdout.strip()

base = f"origin/{config.BASE_BRANCH}"
subprocess.run(["git", "fetch", "--quiet", "origin", config.BASE_BRANCH], cwd=str(config.ROOT), timeout=180)
if subprocess.run(
    ["git", "rev-parse", "--verify", "--quiet", base],
    cwd=str(config.ROOT), capture_output=True, timeout=30,
).returncode != 0:
    base = config.BASE_BRANCH

# --- 3b. DOES THIS BRANCH ALREADY CARRY A PREVIOUS ATTEMPT? ---------------------
#
# A lap that fails after committing leaves its branch behind, and the next lap for the
# same issue is dispatched onto that same branch name. The worktree then opens with the
# previous attempt's files already present -- and an implement node that looks around,
# sees the feature it was asked to build sitting there, and checks `HEAD` will conclude
# the work is already done and merged.
#
# That happened: a lap reported COMPLETE having changed nothing, citing a commit that
# existed only on its own unmerged branch. The base had no such file. Nothing failed;
# the issue simply never got built and the report was confident and wrong.
#
# So the fact is measured and handed to the node rather than left to be inferred from
# a directory listing.
prior = subprocess.run(
    ["git", "log", "--oneline", f"{base}..HEAD"],
    cwd=str(config.ROOT), capture_output=True, text=True, encoding="utf-8", timeout=30,
).stdout.strip()
prior_note = ""
if prior:
    n = len(prior.splitlines())
    prior_note = (
        f"THIS BRANCH ALREADY HAS {n} COMMIT(S) THAT ARE NOT ON {base}. They are a "
        f"PREVIOUS ATTEMPT at this same issue that did not land -- most likely it was "
        f"blocked by the guard or the gate. They are NOT merged and the issue is NOT "
        f"done. Continue that work: read it, keep what is right, fix what stopped it. "
        f"Do not report the issue as already complete on the strength of files you "
        f"find in this worktree.\n" + prior
    )
    note(prior_note)
    # WRITTEN WHERE THE PLANNER WILL READ IT. `note()` goes to the run log, which the
    # plan node never sees; a warning nobody reads is not a warning. The plan prompt
    # names this file.
    (artifacts / "PRIOR-ATTEMPT.md").write_text(
        "# A previous attempt is already on this branch\n\n" + prior_note + "\n",
        encoding="utf-8",
    )

# --- 4. claim it ---------------------------------------------------------------
if issue["_state"] != "in-progress":
    if state.main(["set", target, "state=in-progress"]) != 0:
        die(f"could not move {target} to in-progress")

emit(
        {
            "target": target,
            "number": number,
            "branch": branch,
            "title": re.sub(r"\s+", " ", title)[:120],
            "base": base,
            "prior_attempt": prior_note,
        }
)
