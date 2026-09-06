"""Protected paths, the size cap, and the scope leash. Structural, not prompted.

    python factory/guard.py [--base <ref>] [--head <ref>]

FACTORY_RULES 5, 6 and 8. This runs BEFORE any other evaluation, because a change
that can edit the rulebook must not be judged against the edited rulebook.

Exits:
    0  PROTECTED_OK
    1  PROTECTED_VIOLATION / SCOPE_VIOLATION / SIZE_VIOLATION -- auto-reject
    2  GUARD_ERROR -- the guard could not run, which is NOT a pass

Exit code 2 matters more than it looks. A guard that cannot determine the diff must
FAIL CLOSED. Failing open here means "we could not check, so we merged", which is
the same class of mistake as counting a skipped check as a passed one.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

# Kept in code rather than in a data file the factory could plausibly be asked to
# edit as part of some other task. `factory/*` is itself on this list, which is what
# stops a PR from supplying the guard that judges it.
PROTECTED = [
    # Governance -- the constitution. The agent cannot amend the rules it is judged by.
    "MISSION.md",
    "FACTORY_RULES.md",
    "FACTORY.md",
    "CLAUDE.md",
    "AGENTS.md",
    # The PRD the mission was compressed from.
    "docs/*.prd.md",
    # The locks: thresholds a human chose, and the ratchet floor. Protected so that
    # raising a floor is a deliberate human commit rather than something a builder
    # can do in the same PR that made the floor inconvenient.
    ".factory/locks/*",
    # The holdout: the assertions the builder does not get to weaken. Blocked from
    # READING too, by the deny list the workflows pass to every node.
    ".factory/holdout/*",
    ".factory/holdout/**",
    # The machinery, including this file.
    "factory/*",
    # The gate itself IS the definition of "works". A builder that can edit its own
    # judge can make any claim true. Legitimate coverage growth goes to the project's
    # own test directory instead.
    "harness/*",
    "harness/**",
    # The workflow pack. A node that can rewrite its own workflow can remove the
    # node that checks it.
    ".archon/workflows/factory/**",
    # CI, because a required check the factory can edit is not a required check.
    ".github/**",
    # Secrets. Being unable to EDIT one does not stop a broad `git add` from
    # publishing one that appears for the first time -- see preflight() below.
    # `.env*`, not `.env` plus `.env.*`. FACTORY_RULES section 5 claims `.env*` and
    # the two-pattern form does not cover `.envrc`, `.envlocal` or `.env-prod` -- all
    # of which hold exactly what the rule is about. Found by the doctor's rules-vs-guard
    # cross-check on its first run, in the list it was written to audit somebody else's
    # copy of.
    ".env*",
    "*.local",
    "*credential*",
    "*secret*",
    "*.pem",
]

# THE PROJECT'S OWN ADDITIONS COME FROM config.py, AND THAT IS NOT TIDINESS.
#
# They used to be a `PROTECTED +=` literal right here. `bin/sync-to.py` syncs
# `factory/` wholesale as machinery, so the first sync after an operator added a path
# DELETED it -- and the failure is entirely silent: the guard still runs, still finds
# no violation, still prints PROTECTED_OK, and the paths it was told to defend are
# simply not on the list any more. Reproduced on flagpole, on `app/rollout.py`, hours
# after a planner refused to build until that exact gap was closed.
#
# `factory/config.py` is on the sync's NEVER list. Anything an operator edits belongs
# there, and the rule generalises: a synced file must contain no list a human is
# invited to change.
#
# READ WITHOUT A DEFAULT, deliberately. An install whose config.py predates these
# settings raises AttributeError and the guard -- a required check -- fails closed and
# loudly. `sync-to.py` prints the settings to paste. A `getattr(config, ..., [])`
# would substitute an empty list and print PROTECTED_OK, which is the same silent hole
# this move exists to close, reintroduced by the fix for it.
PROTECTED += config.PROTECTED_EXTRA

BANNED_CATEGORIES: list[str] = config.BANNED_CATEGORIES

SIZE_EXEMPT = [".factory/runs/*", ".factory/runs/**", "*.lock", "uv.lock", "bun.lockb"]

# Counted separately from production code by the size cap. See config.SIZE_CAP.
# These patterns are about WHERE tests live, not what they contain: a file under
# tests/ does not ship to the running product, so it cannot carry the risk the cap is
# guarding against, and TOTAL_CAP still bounds the whole diff.
TEST_PATHS = ["tests/*", "tests/**", "*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js",
              "**/*.test.ts", "**/*.test.js", "**/*.spec.ts", "**/*.spec.js",
              *config.TEST_PATHS_EXTRA]


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return p.returncode, p.stdout.strip()


def base_ref(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for candidate in ("origin/HEAD", "origin/main", "main", "master"):
        rc, out = git("rev-parse", "--verify", "--quiet", candidate)
        if rc == 0 and out:
            return candidate
    return None


def matches(path: str, patterns: list[str]) -> bool:
    p = path.replace("\\", "/")
    return any(
        fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(os.path.basename(p), pat)
        for pat in patterns
    )


def preflight() -> int:
    """Before ANY node that can commit. Takes a second; prevents a publication.

    An empty `git check-ignore` result means your next run publishes your key. A
    broad `git add` inside a PR step sweeps up whatever was not ignored, and on a
    public repo that is publication, not a mistake you can take back. Rotating
    afterwards is the cleanup, not the fix.

    A node, not a line in a checklist a human reads.
    """
    missing = []
    for name in config.SECRET_FILES:
        rc, _ = git("check-ignore", "-q", name)
        if rc != 0:
            missing.append(name)
    if missing:
        print(
            "PREFLIGHT_FAILED: not gitignored: " + " ".join(missing) + "\n"
            "  A commit step would publish these. Add them to .gitignore and re-run. "
            "This refuses to start rather than warning, because the warning is read "
            "after the push.",
            file=sys.stderr,
        )
        return 1
    print(f"PREFLIGHT_OK secrets_ignored={len(config.SECRET_FILES)}")
    return 0


def main(argv: list[str]) -> int:
    if "--preflight" in argv:
        return preflight()

    base = base_ref(argv[argv.index("--base") + 1] if "--base" in argv else None)
    head = argv[argv.index("--head") + 1] if "--head" in argv else None

    if base is None:
        print(
            "GUARD_ERROR: no base branch found. The guard fails closed: a diff that "
            "cannot be computed is not a diff that was checked."
        )
        return 2

    # THREE DOTS, ALWAYS. `git diff main` compares the two TIPS, so a branch cut
    # before main moved reports main's later commits as though this branch had made
    # them -- and main's later commits routinely touch factory/ and the locks. Every
    # such branch is then auto-rejected for protected files it never went near: a
    # false positive in the most severe gate there is, firing more often the longer
    # a branch lives. `base...HEAD` compares against the MERGE BASE.
    rng = f"{base}...{head}" if head else f"{base}...HEAD"

    rc, out = git("diff", "--name-only", rng)
    if rc != 0:
        print(f"GUARD_ERROR: git diff {rng} failed. Failing closed.")
        return 2
    changed = [f for f in out.splitlines() if f.strip()]

    # A three-dot diff only sees COMMITTED work. In the workflow the guard runs
    # after the commit step, so that is the whole story -- but anything run by hand
    # on a dirty tree would otherwise be checked against nothing and print a
    # confident PROTECTED_OK.
    if not head:
        # `--untracked-files=all`, and it is not optional. The default collapses an
        # untracked DIRECTORY into a single entry -- `src/` rather than the forty
        # files inside it -- so a node that creates a whole new directory reports
        # one changed file, sails past the file-count cap, and has none of its
        # contents checked against the protected list individually.
        rc, dirty = git("status", "--porcelain", "--untracked-files=all")
        if rc != 0:
            print("GUARD_ERROR: git status failed. Failing closed.")
            return 2
        for row in dirty.splitlines():
            # porcelain is exactly two status columns, then the path. Slicing 3 eats
            # the first character of the filename when the second column is blank.
            path = row[2:].lstrip().strip('"')
            if " -> " in path:  # a rename: check the destination
                path = path.split(" -> ", 1)[1]
            if path and path not in changed:
                changed.append(path)

    rc, stat = git("diff", "--numstat", rng)
    lines = 0
    code_lines = 0
    if rc == 0:
        for row in stat.splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                if not matches(parts[2], SIZE_EXEMPT):
                    n = int(parts[0]) + int(parts[1])
                    lines += n
                    if not matches(parts[2], TEST_PATHS):
                        code_lines += n

    print(f"GUARD_START range={rng} files={len(changed)} lines={lines} code_lines={code_lines}")

    violations: list[tuple[str, str]] = []
    for f in changed:
        if matches(f, PROTECTED):
            violations.append((f, "protected file (FACTORY_RULES 5)"))
        elif matches(f, BANNED_CATEGORIES):
            violations.append((f, "banned file category (guard.BANNED_CATEGORIES)"))

    for f in changed:
        blocked = any(v[0] == f for v in violations)
        print(f"  {'BLOCK' if blocked else 'ok   '}  {f}")

    print(f"GUARD_FILES_CHECKED={len(changed)}")

    if violations:
        print(f"PROTECTED_VIOLATION={len(violations)}")
        for f, why in violations:
            print(f"  {f}: {why}")
        print(
            "Auto-reject, no fix attempt (FACTORY_RULES 6). Needing one of these "
            "touched means the scope was misunderstood, which is a triage bug rather "
            "than a code bug -- escalate to needs-human."
        )
        return 1

    scoped = [f for f in changed if not matches(f, SIZE_EXEMPT)]
    if config.FILE_CAP and len(scoped) > config.FILE_CAP:
        print(
            f"SCOPE_VIOLATION: {len(scoped)} files changed, cap is {config.FILE_CAP}. "
            "A node that can edit outside its own scope will grow the PR and introduce "
            "a bug in a file nobody asked it to touch. Split the work into a sub-issue."
        )
        return 1

    if code_lines > config.SIZE_CAP:
        print(
            f"SIZE_VIOLATION: {code_lines} production lines changed, cap is "
            f"{config.SIZE_CAP} (FACTORY_RULES 8). Tests are excluded and are not the "
            f"problem; this is {code_lines} lines of code. Split the work into a "
            "sub-issue rather than shipping something nobody could review even in "
            "principle."
        )
        return 1

    if config.TOTAL_CAP and lines > config.TOTAL_CAP:
        print(
            f"SIZE_VIOLATION: {lines} total lines changed, cap is {config.TOTAL_CAP}. "
            "Tests are exempt from the production-line cap, not from review: a diff "
            "this large is unreviewable whatever it is made of."
        )
        return 1

    print("PROTECTED_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
