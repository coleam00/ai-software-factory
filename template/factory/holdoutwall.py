"""The holdout is walled off from the builder's CHECKOUT, not just from its tools.

    import holdoutwall; note(holdoutwall.wall())

WHY A TOOL POLICY IS NOT A WALL. Every builder node carries a deny list for
`.factory/holdout/**`, and on a provider that honours tool restrictions that deny is
real. Codex does not honour one: the engine validates the workflow, prints a warning
that the restriction "will be ignored", and runs the node with every tool it has. So
on that provider the builder could open the scenarios it is about to be judged on,
with every check still green -- which is the one thing the holdout exists to prevent.

The mechanism that works on every provider is to not have the files there. This runs
in the builder's worktree before any model is spent and removes `.factory/holdout/`
from the working tree with a sparse checkout. The files stay in the branch, the commit
step's `git add -A` does not see them as deleted (skip-worktree), the pull request
carries them untouched, and the validator -- a different run in a different worktree,
never sparse -- still has them to judge against.

It refuses to run in the operator's main checkout: that is where the human edits the
scenarios, and sparsing it would hide them from the one person allowed to see them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import config

HOLDOUT_REL = ".factory/holdout"


def _git(*args: str, root: Path) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def wall(root: Path | None = None, shared: Path | None = None) -> str:
    """Remove the holdout from THIS checkout. Returns the marker line to log.

    Raises RuntimeError when the wall could not be proven: the files are still
    readable, or git now reports them as deleted (which the commit step would stage).
    """
    root = (root or config.ROOT).resolve()
    shared = (shared or config.SHARED).resolve()
    hold = root / HOLDOUT_REL

    rc, tracked = _git("ls-files", "--", HOLDOUT_REL, root=root)
    tracked_files = [line for line in tracked.splitlines() if line.strip()] if rc == 0 else []
    if not tracked_files:
        return f"HOLDOUT_WALL absent ({HOLDOUT_REL} has no tracked files in this checkout)"
    if root == shared:
        return "HOLDOUT_WALL skipped (this is the main checkout, where a human edits the scenarios)"

    rc, out = _git("sparse-checkout", "set", "--no-cone", "/*", f"!/{HOLDOUT_REL}/", root=root)
    if rc != 0:
        raise RuntimeError(f"git sparse-checkout refused: {out[-300:]}")

    # Untracked residue (a previous run's .results) is not a scenario, but it is not
    # evidence either. Clear it so the directory is simply gone.
    if hold.exists():
        shutil.rmtree(hold, ignore_errors=True)

    still = [f for f in tracked_files if (root / f).exists()]
    rc, status = _git("status", "--porcelain", "--", HOLDOUT_REL, root=root)
    if still or (rc == 0 and status.strip()):
        raise RuntimeError(
            f"holdout still present or reported as changed: files={still[:3]} status={status[:200]!r}"
        )
    return f"HOLDOUT_WALL sparse-checkout removed {len(tracked_files)} file(s) under {HOLDOUT_REL} from this worktree"
