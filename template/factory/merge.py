"""The merge, and the ratchet bookkeeping that follows a merge that landed.

    python factory/merge.py gh:pr:NUMBER

THE MERGE ITSELF IS NOT HERE. `archon-merge` owns it: it rereads the operator policy
and the stop path in the instant before it mutates, pins the accepted head with
`--match-head-commit`, and reads the merge commit's two parents back to prove they are
the base and head somebody accepted. This module supplies the authorization (through
`sdlc.merge_policy`) and does what has to happen locally AFTERWARDS.

REMOTE SUCCESS AND LOCAL BOOKKEEPING ARE KEPT APART, and that separation is the whole
reason this file still exists. The merge is on GitHub and cannot be undone; a floor
that could not be raised because the operator's checkout is dirty is a chore, not a
lost merge. `sdlc.apply_merge` records the merge commit before calling in here, so a
failure below is retried on the next tick against a merge that is already recorded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def worktree_holding(branch: str) -> str:
    """The path of the working tree that has `branch` checked out, or "".

    Asked so that nothing ever moves a ref out from under a checkout. `git worktree
    list --porcelain` reports every attached tree including the main one, which is the
    only source that knows about the checkout this process is not running in.
    """
    rc, out = git("worktree", "list", "--porcelain")
    if rc != 0:
        # Unknown, so assume it IS checked out somewhere: the cost of being wrong that
        # way is a checkout left behind, which prints a note. The other way silently
        # arms a revert.
        return str(config.SHARED)
    path = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.strip() == f"branch refs/heads/{branch}":
            return path
    return ""


def raise_floor(holder: str, observed: dict) -> str:
    """Close the ratchet slack this merge just created. MONOTONIC: it never lowers.

    WHY THIS IS SAFE, and why it is not a hole in the protected list. The guard still
    rejects any PULL REQUEST that touches `.factory/locks/floor.json`, so a builder
    still cannot delete an assertion and lower the floor to match -- which is the
    attack the protection exists to stop. This runs afterwards, in the machinery, and
    can only move a number UP. "The floor never falls without a human" is the ratchet,
    and it is exactly preserved.

    WHY IT HAS TO BE AUTOMATIC. Slack is the gap between what the harness asserts and
    what the floor requires, and it is precisely the number of assertions that could be
    deleted with the gate still green. Holding every merge until a human closed it was
    the right instinct and the wrong remedy: it made the SUCCESS case -- a pull request
    that adds tests -- require a person, so on a good day the factory stopped
    completely. Four pull requests in one session were each held on slack alone.

    Raising it here closes the gap in the same breath as the merge that opened it,
    which is both faster than a human and strictly more honest: the floor now describes
    what the base branch actually has, at the moment it comes to have it.

    THE COUNTS COME FROM THE GATE RUN ON THE MERGED CONTENT, which the caller proved by
    comparing trees before calling. Raising to numbers measured somewhere else is how a
    base branch ends up claiming coverage it does not have.
    """
    floor_path = Path(holder) / ".factory" / "locks" / "floor.json"
    if not floor_path.exists():
        return ""
    try:
        data = json.loads(floor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    raised = []
    for key, value in data.items():
        # `_MAX` keys are CEILINGS, not floors: UNCALIBRATED_MAX says how many margins
        # nobody has set may exist, and raising that would loosen the check rather than
        # tighten it. This path may only ever tighten, so it does not touch them. They
        # come down as margins get calibrated, in a human commit.
        if key.startswith("_") or key.endswith("_MAX") or not isinstance(value, int):
            continue
        got = observed.get(key)
        # NEVER invent a key, and NEVER move one down. Both would be the factory
        # editing its own judge rather than tightening it.
        if not isinstance(got, int) or got <= value:
            continue
        data[key] = got
        raised.append(f"{key} {value}->{got}")
    if not raised:
        return ""

    floor_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    msg = ("ratchet: close the slack this merge opened\n\n"
           + "\n".join("  " + r for r in raised)
           + "\n\nRaised automatically by factory/merge.py, from the counts the gate\n"
             "observed on the tree that just landed. Monotonic: this path can only\n"
             "raise. Lowering a floor is still a human commit, and a pull request that\n"
             "touches this file is still auto-rejected, so the ratchet is unchanged.\n")
    rc, _ = git("-C", holder, "add", ".factory/locks/floor.json")
    if rc != 0:
        return ""
    rc, out = git("-C", holder, "commit", "-m", msg)
    if rc != 0:
        print(f"RATCHET_RAISE_FAILED could not commit: {out.strip()[:200]}")
        return ""
    rc, out = git("-C", holder, "push", "origin", config.BASE_BRANCH)
    if rc != 0:
        print(f"RATCHET_RAISE_UNPUSHED committed locally but push failed: {out.strip()[:200]}")
    return ", ".join(raised)


def bookkeeping(result: dict, measurements: dict) -> None:
    """Bring the operator's base checkout up to the merge, then close the slack.

    Every refusal here RAISES, because the caller records this as an effect that has
    not been applied yet and retries it on the next tick. Returning quietly would mark
    it done: the merge would be on the remote, the floor would silently not have moved,
    and the gap the ratchet exists to close would be open with nothing red anywhere.
    """
    rc, out = git("fetch", "--quiet", "origin", config.BASE_BRANCH)
    if rc:
        raise RuntimeError(f"could not fetch origin/{config.BASE_BRANCH}: {out[:300]}")
    holder = worktree_holding(config.BASE_BRANCH)
    if not holder:
        raise RuntimeError(
            f"no checkout has {config.BASE_BRANCH} checked out, so the ratchet has "
            f"nowhere to commit. Check out {config.BASE_BRANCH} somewhere and it retries.")
    rc, dirty = git("-C", holder, "status", "--porcelain")
    if rc or dirty:
        raise RuntimeError(
            f"{holder} has uncommitted changes. The remote merge succeeded; the ratchet "
            f"raise waits until that checkout is clean.")
    rc, out = git("-C", holder, "merge", "--ff-only", f"origin/{config.BASE_BRANCH}")
    if rc:
        raise RuntimeError(f"{holder} could not fast-forward to the merge: {out[:300]}")

    # THE TREE THAT LANDED MUST BE THE TREE THAT WAS MEASURED. `archon-merge` performs
    # an ordinary merge commit whose second parent is the accepted head, so a base that
    # has not moved since produces exactly that tree. If anything else landed in
    # between, these counts describe a different tree and raising a floor to them makes
    # the base claim coverage nobody ran.
    rc_head, actual = git("-C", holder, "rev-parse", "HEAD^{tree}")
    rc_want, accepted = git("rev-parse", result["head_sha"] + "^{tree}")
    if rc_head or rc_want:
        raise RuntimeError("could not resolve the merged and accepted trees to compare them")
    if actual != accepted:
        raise RuntimeError(
            f"{config.BASE_BRANCH} has advanced past the tree the gate measured, so these "
            f"counts no longer describe it. Raise the floor by hand from a fresh gate run.")
    counts = measurements.get("counts") or {}
    raised = raise_floor(holder, counts)
    print(f"RATCHET_RAISED {raised}" if raised else "RATCHET_UNCHANGED no floor moved")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in {"--help", "-h"}:
        print(__doc__)
        return 0 if argv and argv[0] in {"--help", "-h"} else 2
    import sdlc
    return 0 if sdlc.launch("merge", argv[0], detach=False) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
