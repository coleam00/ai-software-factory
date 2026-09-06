"""Commit the implement node's work. Without this, the whole lap is theatre.

THE FAILURE THIS EXISTS FOR. The implement node edits files in the worktree, nothing
records them, and the worktree is discarded when the run ends: every node reports OK,
the guard correctly sees two changed files and thirty lines, and the branch ends up
empty. Driving the lap by hand hides it completely, because a human commits without
being told to.

IT ASSERTS ON THE ARTIFACT, NOT THE EXIT CODE. A node that was denied a tool exits 0
having changed nothing; a node that hit its budget exits 0 having changed nothing; a
node that decided the work was already done exits 0 having changed nothing. "The run
succeeded" is true in all three cases and useless in all three. An empty diff is a
failed lap, and it says so with the denials attached, because a denial is the usual
cause and correlating them an hour later in a log is not a process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "factory"))

# nodeio FIRST, before anything that might print at import time: importing it
# redirects stdout to stderr for the whole process, so a library marker can never
# be mistaken for this node's value. emit() writes to the real stdout.
from nodeio import emit, note  # noqa: E402

import config  # noqa: E402

number = (os.environ.get("INPUTS_NUMBER") or "0").strip()
title = (os.environ.get("INPUTS_TITLE") or f"factory: issue {number}").strip()
base = (os.environ.get("INPUTS_BASE") or "origin/main").strip()
artifacts = Path(os.environ.get("ARTIFACTS_DIR") or ".")


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(config.ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


rc, dirty = git("status", "--porcelain", "--untracked-files=all")
if rc != 0:
    note(f"COMMIT_FAILED: git status failed: {dirty}")
    sys.exit(1)

changed = [ln for ln in dirty.splitlines() if ln.strip()]

# THE QUESTION IS "IS THERE ANYTHING TO VALIDATE", NOT "ARE THERE UNSAVED EDITS".
# Those were the same question while the builder was a single command node that edited
# and left. They stopped being the same the moment the build step became Archon's
# `archon-implement`, which commits as it goes: the tree is clean BECAUSE the work
# landed, and a check that reads a clean tree as "nothing happened" fails the lap for
# succeeding. So a clean tree with commits ahead of base is a pass, and the fatal case
# is the one that always mattered -- a branch identical to its base.
rc_ahead, ahead = git("rev-list", "--count", f"{base}..HEAD")
already_committed = rc_ahead == 0 and ahead.strip().isdigit() and int(ahead.strip()) > 0

if not changed and already_committed:
    rc, sha = git("rev-parse", "--short", "HEAD")
    rc2, stat = git("diff", "--stat", f"{base}...HEAD")
    files = len([ln for ln in stat.splitlines() if "|" in ln])
    note(f"COMMITTED_BY_BUILDER {sha} commits={ahead.strip()} files={files}")
    note(stat[-1500:])
    emit({"sha": sha, "files": files})
    sys.exit(0)

if not changed:
    hint = ""
    report = artifacts / "implementation.md"
    if report.exists():
        hint = (
            "\n  The implement node did write a report, so it believed it had done "
            "something. Read " + str(report) + " -- the usual causes are a denied tool "
            "and a plan task the node could not perform."
        )
    note(
        "COMMIT_FAILED: the implement node produced no file changes at all -- there is "
        "nothing to validate." + hint,
    )
    sys.exit(1)

rc, out = git("add", "-A")
if rc != 0:
    note(f"COMMIT_FAILED: git add failed: {out}")
    sys.exit(1)

subject = f"{title[:60]} (#{number})"
rc, out = git("commit", "-q", "-m", subject)
if rc != 0:
    note(f"COMMIT_FAILED: {out}")
    sys.exit(1)

rc, sha = git("rev-parse", "--short", "HEAD")
rc2, stat = git("diff", "--stat", f"{base}...HEAD")

note(f"COMMITTED {sha} files={len(changed)}")
note(stat[-1500:])
emit({"sha": sha, "files": len(changed)})
