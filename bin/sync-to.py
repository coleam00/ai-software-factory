#!/usr/bin/env python3
"""Push template changes into a repo that already ran `factory init`.

    python bin/sync-to.py ../tally
    python bin/sync-to.py ../tally --dry-run

WHY THIS IS A SCRIPT AND NOT `cp -r`. The runner is COPIED into a repo and never
linked, so a fix here reaches nothing already built. That is a known cost. What is
not obvious is that the obvious command for paying it silently does not work: on Git
Bash for Windows, `cp -r src/. dst/` reports success and leaves the destination
untouched. Two rounds of a real fix were lost to that -- the same failure reproduced
after a fix that had, as far as anything visible said, been applied.

So this compares CONTENT, copies only what differs, and prints exactly what it
changed. A sync that did nothing says so.

IT NEVER TOUCHES THE THREE THINGS THAT ARE YOURS: MISSION.md, harness/END-TO-END.md and
.factory/holdout/. Those are the product, not the machinery, and overwriting them
with a scaffold is the one thing this must never do.

AND IT NEVER OVERWRITES A PROMPT YOU REWROTE. The node prompts and the skills are
the personalisation layer; they are add-only here, installed when missing and left
alone when present. "Missing" and "edited" are different questions, so a new prompt
still reaches an existing install.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
TEMPLATE = HOME / "template"

# The machinery, which is the same in every factory and therefore safe to overwrite.
SYNC = [
    "factory",
    ".archon/workflows/factory",
    "harness/ci.py",
    "harness/appproc.py",
    "harness/mutations/run.py",
    ".claude/skills",
]

# ADD-ONLY. Installed when missing, NEVER overwritten.
#
# These are the personalisation layer. The README's promise is that the node prompts
# are yours to rewrite, and a sync that quietly replaces a prompt you rewrote breaks
# exactly that promise -- silently, and first visible as an unattended run doing the
# thing you thought you had changed. A new prompt still reaches an existing install,
# because "missing" and "edited" are different questions.
ADD_ONLY_PREFIXES = (".claude/skills/",)
ADD_ONLY_CONTAINS = ("/commands/",)

# NEVER. These are the product.
NEVER = {
    "MISSION.md", "FACTORY_RULES.md", "CLAUDE.md", "AGENTS.md", "FACTORY.md",
    "harness/END-TO-END.md", "harness/harness.config.json", "harness/mutations/defects.json",
    ".factory/holdout/HOLDOUT.md", ".factory/locks/floor.json", ".factory/decisions.md",
    "factory/config.py",  # every project-specific setting lives here
}


def missing_settings(dest: Path) -> list[tuple[str, str]]:
    """Settings the template has and this install does not.

    `factory/config.py` is on the NEVER list because it is the one file you edit --
    every project-specific value lives there, and overwriting it would throw those
    away. The consequence, which is not obvious until it bites: a new setting can
    never reach an existing install, and the synced code that reads it raises
    AttributeError at runtime, on whatever path happens to touch it first.

    Measured here: `BASE_BRANCH` was added, four modules were synced to use it, and
    the doctor died with `module 'config' has no attribute 'BASE_BRANCH'`. The sync
    reported success. So the sync now says what to add, and the operator pastes it in
    -- which keeps the file theirs while refusing to leave it silently incomplete.
    """
    import ast as _ast

    def names(path: Path) -> dict:
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return {}
        out = {}
        for node in tree.body:
            if isinstance(node, _ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, _ast.Name) and tgt.id.isupper():
                        out[tgt.id] = _ast.unparse(node)
            # ANNOTATED ASSIGNMENTS ARE ASSIGNMENTS. `X: list[str] = []` parses as
            # AnnAssign, not Assign, so a walker that matches only Assign silently
            # skips every setting that carries a type -- and skipping is indis-
            # tinguishable from "already present" downstream, which is precisely
            # the silence this function exists to break.
            #
            # Measured: PROTECTED_EXTRA, BANNED_CATEGORIES and TEST_PATHS_EXTRA were
            # added to the template, synced into a live repo, and reported as nothing
            # missing. The guard then died with AttributeError on the next run, which
            # is the failure named in this function's own docstring, reproduced by the
            # function meant to prevent it.
            elif isinstance(node, _ast.AnnAssign):
                tgt = node.target
                if isinstance(tgt, _ast.Name) and tgt.id.isupper():
                    out[tgt.id] = _ast.unparse(node)
            # THE HELPERS TOO. The first version reported only the constant, so the
            # instruction was to paste `BASE_BRANCH = _base_branch()` into a file with
            # no `_base_branch` in it -- a fix that produces a NameError instead of an
            # AttributeError. A setting is not portable without what computes it.
            elif isinstance(node, _ast.FunctionDef) and node.name.startswith("_"):
                out[node.name] = _ast.unparse(node)
        return out

    theirs = names(dest / "factory" / "config.py")
    ours = names(TEMPLATE / "factory" / "config.py")
    return [(k, v) for k, v in ours.items() if k not in theirs]


def orphaned_prompts(dest: Path) -> list[str]:
    """Node prompts this install still has that no workflow references any more.

    THE SYNC DELETES NOTHING, deliberately -- it copies what differs and leaves the rest
    alone, because the alternative is a tool that removes a file somebody wrote. The
    consequence only appears when the TEMPLATE removes one: four prompts were deleted
    when plan, implement, fix and review became Archon's sdlc pack, and an existing
    install keeps all four plus the `.claude/skills` entries pointing at them. Nothing
    breaks -- the skill and the prompt are still consistent with each other -- and that
    is exactly the problem. The by-hand path now describes a process the unattended
    factory no longer runs, and it describes it accurately enough to be believed.

    `/commands/` is add-only for a good reason (the prompts are the personalisation
    layer and overwriting a rewritten one is the one thing this must never do), so the
    answer is the same as for a missing setting: report it, and let the operator decide.
    """
    pack = dest / ".archon" / "workflows" / "factory"
    if not pack.is_dir():
        return []
    referenced = set()
    for wf in pack.rglob("*.yaml"):
        if wf.parent.name == "fixtures":
            continue
        for line in wf.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("command:"):
                referenced.add(stripped.split(":", 1)[1].strip())
    orphans = []
    for prompt in sorted(pack.rglob("commands/*.md")):
        if prompt.stem not in referenced:
            orphans.append(prompt.relative_to(dest).as_posix())
    return orphans


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    dry = "--dry-run" in argv
    dest = Path([a for a in argv if not a.startswith("-")][0]).resolve()
    if not (dest / "factory").is_dir():
        print(f"{dest} has no factory/ -- run `factory init` there first.", file=sys.stderr)
        return 1

    changed, skipped = [], []
    for entry in SYNC:
        src = TEMPLATE / entry
        if not src.exists():
            continue
        # Compiled bytecode is not source and must never cross repositories: it is
        # regenerated on first import, it is gitignored on one side and was tracked on
        # the other, and a .pyc that arrives with a future mtime is a module that
        # silently does not match the .py sitting next to it.
        files = ([p for p in src.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix not in (".pyc", ".pyo")]
                 if src.is_dir() else [src])
        for f in files:
            rel = f.relative_to(TEMPLATE).as_posix()
            if rel in NEVER:
                skipped.append(rel)
                continue
            target = dest / rel
            if target.exists() and filecmp.cmp(f, target, shallow=False):
                continue
            if target.exists() and (
                rel.startswith(ADD_ONLY_PREFIXES) or any(c in rel for c in ADD_ONLY_CONTAINS)
            ):
                skipped.append(rel)
                continue
            changed.append(rel)
            if not dry:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)

    for rel in changed:
        print(("would update " if dry else "updated ") + rel)
    # "kept" only ever prints for a file that DIFFERS -- an identical one is skipped by
    # the content compare above and never reaches here. So every line below is a real
    # divergence, and it has two possible causes the sync cannot tell apart: you rewrote
    # it, or the template changed and add-only meant the change never arrived. Saying
    # "kept (yours)" asserts the first, and the second is the one that bites: both
    # factories built on this template were still running the pre-integration `diagnose`
    # prompt weeks after the workflow around it changed.
    for rel in sorted(set(skipped)):
        print(f"differs, kept  {rel}")
    print(f"\n{len(changed)} file(s) {'would change' if dry else 'changed'}, "
          f"{len(set(skipped))} left alone")

    gaps = missing_settings(dest)
    if gaps:
        print()
        print("SETTINGS THIS INSTALL IS MISSING -- the synced code reads them and will")
        print("raise AttributeError without them. factory/config.py is yours, so paste")
        print("these in rather than having them overwritten:")
        print()
        for name, line in gaps:
            for ln in line.splitlines():
                print("    " + ln)
            print()
        print()
    orphans = orphaned_prompts(dest)
    if orphans:
        print()
        print("PROMPTS NO WORKFLOW REFERENCES ANY MORE -- left in place, because a prompt")
        print("you rewrote is yours and this never deletes one. But the by-hand skill that")
        print("points at each still describes a step the factory no longer runs that way:")
        print()
        for rel in orphans:
            print("    " + rel)
        print()
        print("Delete them and re-read .claude/skills/, or keep them as your own variant.")
        print()
    if not changed:
        print("Nothing to do -- the machinery here already matches the template.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
