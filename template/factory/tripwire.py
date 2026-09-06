"""Fail loudly if a builder artifact reached the validator. FACTORY_RULES 9.

    python factory/tripwire.py <validator-working-dir>

This should be impossible. The validator runs in its own worktree with its own
context and its own artifacts directory, so the plan and the implementation report
are simply not there.

It is checked anyway, because the failure it guards against is SILENT BY
CONSTRUCTION. If separation ever breaks -- a shared worktree, a stray copy, a broad
`git add` that swept up `.claude/plans/`, a future change that reuses a directory --
the validator keeps producing confident verdicts and every one of them is
contaminated. Nothing goes red. The verdicts just quietly start agreeing with the
builder.

An independence property that nobody checks is an independence property nobody has.

=============================================================================
WHAT COUNTS AS AN ARTIFACT, and why the obvious answer is wrong
=============================================================================

The first version of this matched on filename alone -- `**/plan.md`,
`**/investigation.md` -- and it fired on the very first real validation, against a
`plan.md` that was one of the workflow's own checked-in PROMPTS.

A prompt is byte-identical in every worktree including main, and it says nothing
whatsoever about how this particular branch was written. Blocking on it does not
protect the holdout; it stops the factory. (Those prompt files are gone -- the planner
is Archon's `archon-plan` now -- but the lesson is about the MATCH, not about that one
file, and any repository with a `plan.md` of its own re-creates it exactly.)

The distinction that actually matters is not the NAME, it is the PROVENANCE:

    tracked in git      part of the repository. Every checkout has it. Not evidence.
    untracked, matching a builder path   produced by a RUN. That is the leak.

So this flags a file only when it is BOTH shaped like a builder artifact AND not
tracked. A false positive here is not harmless -- it wedges every validation, which
looks exactly like a factory with nothing to do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Shapes a builder artifact takes. Matched against untracked files only.
FORBIDDEN = [
    ".claude/plans/**",
    ".claude/reports/**",
    ".claude/execution-reports/**",
    ".claude/code-reviews/**",
    ".factory/runs/**/plan.md",
    ".factory/runs/**/priming.md",
    ".factory/runs/**/report.md",
    ".factory/runs/**/implementation.md",
    ".factory/runs/**/ASSUMPTIONS",
    "**/implementation-report*.md",
    "**/investigation.md",
    "plan.md",
    "priming.md",
    "implementation.md",
]

# Never an artifact, whatever it is called: this is the machinery, it is tracked, and
# it is identical in every checkout. Listed explicitly as well as covered by the
# tracked-file test, because a repo with a broken git is still a repo whose workflow
# prompts are not evidence.
NEVER = (".archon/", "factory/", "harness/", ".github/")


def tracked(root: Path) -> set[str]:
    """Every path git knows about here. Empty on failure, which fails CLOSED --
    an unknown provenance is treated as untracked, so the check stays strict."""
    p = subprocess.run(
        ["git", "ls-files"], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    if p.returncode != 0:
        return set()
    return {line.strip().replace("\\", "/") for line in p.stdout.splitlines() if line.strip()}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = Path(argv[0]).resolve()
    if not root.is_dir():
        print(f"TRIPWIRE_ERROR: {root} is not a directory. Failing closed.")
        return 2

    known = tracked(root)
    found: list[str] = []
    for pattern in FORBIDDEN:
        for hit in root.glob(pattern):
            if not hit.is_file():
                continue
            rel = str(hit.relative_to(root)).replace("\\", "/")
            if rel.startswith(NEVER):
                continue
            if rel in known:
                # Part of the repository, present in every checkout, byte-identical
                # on main. It cannot reveal how THIS branch was written.
                continue
            found.append(rel)

    print(f"TRIPWIRE_PATTERNS_CHECKED={len(FORBIDDEN)} tracked_files={len(known)}")
    if found:
        print(f"TRIPWIRE_TRIPPED={len(found)}")
        for f in sorted(set(found)):
            print(f"  untracked builder artifact in the validator's tree: {f}")
        print(
            "The validator can see how the code was written. Its verdict is no longer "
            "independent evidence and must not be used to merge. This is a workflow "
            "bug, not a code bug (FACTORY_RULES 9)."
        )
        return 1
    print("TRIPWIRE_CLEAR")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
