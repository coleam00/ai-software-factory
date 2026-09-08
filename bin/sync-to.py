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

IT NEVER TOUCHES THE THINGS THAT ARE YOURS: MISSION.md, FACTORY_RULES.md,
harness/END-TO-END.md, the holdout, the ratchet floor and factory/config.py. Those are
the product and your settings, not the machinery, and overwriting them with a scaffold
is the one thing this must never do.

IT RETIRES WHAT THE TEMPLATE USED TO OWN, and that is the only thing here that removes
anything. A file is removed ONLY when its content is byte-for-byte a version this
template committed at some point; anything else is MOVED to `.factory/retired/` and
reported. No recursive delete, no glob: one named list, one file at a time, and
`rmdir` for the directories, which refuses any that still holds something.

AND IT NEVER OVERWRITES A PROMPT YOU REWROTE. The node prompts and the skills are
the personalisation layer; they are add-only here, installed when missing and left
alone when present. "Missing" and "edited" are different questions, so a new prompt
still reaches an existing install.
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
TEMPLATE = HOME / "template"

# The machinery, which is the same in every factory and therefore safe to overwrite.
SYNC = [
    "factory",
    "harness/ci.py",
    "harness/appproc.py",
    # THE EVIDENCE CHECKER, and its absence here was a real hole. It is the module that
    # decides whether an agent's journey report counts as a measurement, it is identical
    # in every factory, and it was the one harness module the sync could not reach -- so
    # a false positive in it (there was one: a passing concrete assertion whose observed
    # value equalled its expectation was refused as a fabrication) was permanent in every
    # install that already existed.
    "harness/agentcheck.py",
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


CRLF, NL = chr(13) + chr(10), chr(10)

# Text extensions, for the line-ending-insensitive compare below. Everything else is
# compared byte for byte, which is what you want for anything that is not source.
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh", ".bat", ".ps1"}


def same_content(src: Path, dest: Path) -> bool:
    """Is the destination already this file, ignoring how the lines end?

    A BYTE COMPARE IS THE WRONG QUESTION ON WINDOWS. git normalises line endings on
    checkout per repository, so the same committed file is CRLF in one clone and LF in
    another. `filecmp.cmp(shallow=False)` then calls every text file different, and the
    sync:

      - rewrites files nobody changed, flipping their endings back and forth, so every
        run reports work it did not do and every repo shows a diff it did not make;
      - reports every add-only file as diverged. That report exists to say "the template
        changed and this install never got it", and a report that fires on all of them
        every time is one people learn to scroll past. `pr-record.md` was reported as
        diverged in both factories while being byte-identical apart from 
.

    So text is compared as text. Binary keeps the byte compare, where a stray byte IS
    the difference.
    """
    if src.suffix.lower() not in TEXT_SUFFIXES:
        return filecmp.cmp(src, dest, shallow=False)
    try:
        a = src.read_text(encoding="utf-8", errors="replace")
        b = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return a.replace(CRLF, NL) == b.replace(CRLF, NL)


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


# WHAT THE TEMPLATE USED TO OWN AND NO LONGER SHIPS.
#
# The sync copies what differs and deletes nothing, which is right for a tool that must
# never remove a file somebody wrote -- and leaves an install carrying every file the
# template ever retired. That was tolerable while retirement meant a stale prompt. It is
# not tolerable now: `.archon/workflows/factory/` is a workflow pack the engine still
# loads, whose YAML references scripts and prompts the factory no longer dispatches, and
# whose `factory-*` names shadow nothing but confuse everything. An install left holding
# it has two packs and runs one.
#
# ONE DIRECTORY AND ONE FILE, both named exactly. Not a pattern, not a subtree walk with
# a delete at the end -- a list, so the blast radius of a bug here is bounded by what is
# written down rather than by what a glob happens to match.
RETIRED = [".archon/workflows/factory", "factory/nodeio.py"]

# Where a retired file that does not match anything this template shipped is moved to.
# Inside `.factory/`, which is already the factory's own drawer, and NOT deleted: an
# edited prompt is somebody's work even when the step it belonged to is gone.
RETIRED_BACKUP = ".factory/retired"


def shipped_versions(rel: str) -> set[str]:
    """Every version of `rel` this template has ever committed, as normalised text.

    OWNERSHIP IS A QUESTION ABOUT BYTES, NOT ABOUT PATHS. "Delete the pack" is the wrong
    instruction: an operator who rewrote a node prompt has a file at that path which is
    theirs, and the only honest way to tell it from a file this template put there is to
    ask whether its content is one this template ever produced. Git already records
    exactly that, so nothing here needs a manifest of its own to keep in sync.

    An unreadable history returns nothing, which makes every file look edited -- backed
    up rather than deleted. That is the safe direction for a wrong answer.
    """
    prefix = f"template/{rel}"
    try:
        commits = subprocess.run(
            ["git", "-C", str(HOME), "log", "--format=%H", "--", prefix],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if commits.returncode:
            return set()
        seen: set[str] = set()
        blobs: set[str] = set()
        for commit in commits.stdout.split():
            listing = subprocess.run(
                ["git", "-C", str(HOME), "ls-tree", "-r", commit, "--", prefix],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            for line in listing.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "blob":
                    blobs.add(fields[2])
        for blob in blobs:
            content = subprocess.run(
                ["git", "-C", str(HOME), "cat-file", "blob", blob],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if content.returncode == 0:
                seen.add(content.stdout.replace(CRLF, NL))
        return seen
    except (OSError, subprocess.SubprocessError):
        return set()


def retire(dest: Path, dry: bool) -> tuple[list[str], list[str]]:
    """Remove what this template used to own. Returns (deleted, backed up).

    NO RECURSIVE DELETE ANYWHERE IN HERE. Files are removed one at a time and only when
    their content matches something this template shipped; directories are removed with
    `rmdir`, which refuses a directory that still has anything in it. A bug in the
    ownership test therefore leaves files behind. It cannot take a tree with it.
    """
    deleted: list[str] = []
    backed_up: list[str] = []
    for entry in RETIRED:
        source = dest / entry
        if not source.exists():
            continue
        files = sorted(p for p in source.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts)             if source.is_dir() else [source]
        for path in files:
            rel = path.relative_to(dest).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace").replace(CRLF, NL)
            except OSError:
                text = None
            if text is not None and text in shipped_versions(rel):
                deleted.append(rel)
                if not dry:
                    path.unlink()
                continue
            backup = dest / RETIRED_BACKUP / rel
            backed_up.append(rel)
            if not dry:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(backup))
        if source.is_dir() and not dry:
            # Bottom-up, and `rmdir` only: anything still holding a file stays.
            for directory in sorted((p for p in source.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                source.rmdir()
            except OSError:
                pass
    return deleted, backed_up


def stale_references(dest: Path, kept: list[str]) -> list[str]:
    """Files this sync left alone that still point at something retirement removed.

    The add-only rule is what protects a prompt somebody rewrote, and its cost is that a
    file which was never edited also never gets the template's update. Usually harmless.
    Not harmless when the template's update was "this path no longer exists": the file
    then describes a step the factory does not run, accurately enough to be believed.
    """
    stale = []
    for rel in kept:
        path = dest / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(entry in text for entry in RETIRED):
            stale.append(rel)
    return stale


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    dry = "--dry-run" in argv
    dest = Path([a for a in argv if not a.startswith("-")][0]).resolve()
    if not (dest / "factory").is_dir():
        print(f"{dest} has no factory/ -- run `factory init` there first.", file=sys.stderr)
        return 1

    changed, skipped, protected = [], [], []
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
                # Checked BEFORE the content compare, so nothing is known about whether
                # this one differs -- it is skipped because of what it IS. Reported
                # separately for exactly that reason: the add-only list below can only
                # contain files that genuinely diverged, and collapsing the two into one
                # label makes a claim about these that was never tested.
                protected.append(rel)
                continue
            target = dest / rel
            if target.exists() and same_content(f, target):
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
    for rel in sorted(set(protected)):
        print(f"yours, never synced  {rel}")
    # THESE ONES GENUINELY DIFFER. An identical file is caught by the content compare
    # above and never reaches the add-only branch, so every line here is a real
    # divergence -- with two causes this cannot tell apart: you rewrote it, or the
    # template changed and add-only meant the change never arrived. The old label said
    # "kept (yours)", which asserts the first, and the second is the one that bites:
    # both factories built on this template were still running the pre-integration
    # `diagnose` prompt weeks after the workflow around it had changed.
    for rel in sorted(set(skipped)):
        print(f"differs, kept        {rel}")
    print(f"\n{len(changed)} file(s) {'would change' if dry else 'changed'}, "
          f"{len(set(skipped)) + len(set(protected))} left alone")

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
    deleted, backed_up = retire(dest, dry)
    if deleted:
        print()
        print("RETIRED -- this template shipped these, no longer does, and your copies are")
        print("byte-for-byte what it shipped, so they are " +
              ("removed" if not dry else "removable") + ". The factory dispatches the")
        print("upstream SDLC pack now; a second pack in .archon/workflows/ is one the")
        print("engine still loads and nothing runs:")
        print()
        for rel in deleted:
            print("    " + rel)
        print()
    if backed_up:
        print()
        print("RETIRED, BUT NOT YOURS TO LOSE -- these differ from every version this")
        print(f"template shipped, so you edited them. They are {'moved' if not dry else 'moved on a real run'}")
        print(f"to {RETIRED_BACKUP}/ rather than deleted, and out of .archon/ so the engine")
        print("stops loading a pack the factory no longer dispatches:")
        print()
        for rel in backed_up:
            print("    " + rel)
        print()
        print("Read them before you throw them away: whatever you changed there is a")
        print("statement about your process that the new path may still need.")
        print()

    stale = stale_references(dest, sorted(set(skipped)))
    if stale:
        print()
        print("KEPT, AND NOW POINTING AT NOTHING -- add-only files this sync left alone")
        print("that still name a path retirement removed. Nothing breaks; they simply")
        print("describe a step the factory no longer runs, accurately enough to believe:")
        print()
        for rel in stale:
            print("    " + rel)
        print()
        print("Delete each and re-run this sync to get the template's version, or rewrite")
        print("it against the workflow the factory actually dispatches now.")
        print()
    if not changed and not deleted and not backed_up:
        print("Nothing to do -- the machinery here already matches the template.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
