#!/usr/bin/env python3
"""Does installing it, and upgrading an install, actually work?

    python bin/_test_install.py

`factory/_selftest.py` pins the runtime and `bin/audit.py` pins the cross-file
invariants. Both of them read the template where it sits. Neither has ever answered the
two questions an operator actually asks:

    can a repository that has never seen this end up with a working factory?
    can a repository that already has one be brought forward without losing its work?

Both were regressions waiting to happen and both are the expensive kind, because the
first symptom is somebody else's repository. A copy plan that stops shipping a module
produces an install that imports fine here and dies on its first tick there. A sync that
retires the wrong file deletes a prompt somebody wrote.

So this installs into a scratch git repository and upgrades another one, and asserts
against what is on disk afterwards. No network: `gh` is never reached because the
scratch repository has no `origin`, and the engine is pointed at a name that does not
exist so the installer takes its refusal path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
TEMPLATE = HOME / "template"
NL = chr(10)

FAILURES: list[str] = []
CHECKS = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(what + ((" -- " + detail) if detail else ""))


def run(argv: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900,
                          env={**os.environ, **(env or {})})


def scratch_repo(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet"], cwd=where)
    run(["git", "config", "user.email", "test@example.invalid"], cwd=where)
    run(["git", "config", "user.name", "install test"], cwd=where)
    (where / "app.py").write_text("def main():" + NL + "    return 0" + NL, encoding="utf-8")
    (where / "pyproject.toml").write_text("[project]" + NL + 'name = "scratch"' + NL,
                                          encoding="utf-8")
    return where


# THE ENGINE IS NOT INSTALLED, as far as this test is concerned. Pointing the installer
# at a name that cannot be on PATH takes its refusal branch deterministically, on a
# machine that has an engine and on a machine that does not -- and the refusal branch is
# the one worth pinning, because an install that quietly proceeds without an engine is
# an install that reports success and dispatches nothing.
NO_ENGINE = {"FACTORY_ARCHON_BIN": "archon-that-cannot-exist"}


def fresh_install_checks(tmp: Path) -> None:
    """A repository that has never seen this ends up able to run its own self-test."""
    root = scratch_repo(tmp / "fresh")
    result = run([sys.executable, str(HOME / "bin" / "factory.py"), "init", "--yes"],
                 cwd=root, env=NO_ENGINE)
    check("init exits cleanly on a repository with no factory", result.returncode == 0,
          result.stdout[-800:] + result.stderr[-800:])
    check("and says the engine is missing rather than implying one",
          "Archon is still missing" in result.stdout,
          "an install that reports success without an engine dispatches nothing, and "
          "looks exactly like a factory with nothing to do")

    for rel in ("factory/config.py", "factory/dispatch.py", "factory/sdlc.py",
                "factory/fixed_gate.py", "factory/runtime.py", "factory/state.py",
                "factory/gate.py", "factory/guard.py", "factory/merge.py",
                "factory/tripwire.py", "factory/_selftest.py", "harness/ci.py",
                "harness/agentcheck.py", ".factory/locks/floor.json",
                ".factory/holdout/HOLDOUT.md", ".factory/loop.sh", ".factory/monitor.py",
                ".factory/notify.sh", "MISSION.md", "FACTORY_RULES.md", "FACTORY.md",
                ".claude/skills/factory-setup/SKILL.md"):
        check("init installs " + rel, (root / rel).exists(),
              "the runtime imports it, or the walkthrough tells somebody to read it")

    check("init does NOT install a workflow pack of its own",
          not (root / ".archon" / "workflows" / "factory").exists(),
          "the factory dispatches the upstream SDLC pack; a second pack in "
          ".archon/workflows/ is one the engine loads and nothing runs")

    # EVERY MODULE THE RUNTIME IMPORTS, imported. A copy plan that stops shipping one
    # produces an install that is fine here and dies on its first tick there.
    imports = ";".join(f"import {m}" for m in sorted(
        p.stem for p in (root / "factory").glob("*.py") if not p.stem.startswith("_")
        and p.stem != "regress-trigger"))
    result = run([sys.executable, "-c",
                  f"import sys; sys.path.insert(0, r'{root / 'factory'}'); {imports}"],
                 cwd=root)
    check("every installed factory module imports", result.returncode == 0,
          result.stderr[-600:])

    result = run([sys.executable, "factory/_selftest.py", "--quiet"], cwd=root)
    check("the installed factory passes its own self-test", result.returncode == 0,
          result.stdout[-1200:] + result.stderr[-600:])

    result = run([sys.executable, "factory/doctor.py"], cwd=root)
    check("the doctor runs against a fresh install rather than crashing",
          result.returncode in (0, 1),
          f"exited {result.returncode}: " + (result.stdout + result.stderr)[-1200:])


def old_pack_files(rev: str) -> dict[str, str]:
    """Everything the template shipped under the retired paths at `rev`."""
    listing = run(["git", "ls-tree", "-r", "--name-only", rev, "--",
                   "template/.archon/workflows/factory", "template/factory/nodeio.py",
                   "template/.claude/skills"], cwd=HOME)
    out: dict[str, str] = {}
    for path in listing.stdout.split():
        blob = run(["git", "show", f"{rev}:{path}"], cwd=HOME)
        if blob.returncode == 0:
            out[path[len("template/"):]] = blob.stdout
    return out


def migration_checks(tmp: Path) -> None:
    """An install carrying the old pack is brought forward without losing anybody's work.

    THE TWO HALVES ARE DIFFERENT QUESTIONS. A file byte-identical to something this
    template shipped is this template's to retire. A file that differs is somebody's
    edit, and the only honest thing to do with it is put it somewhere they can find it
    and say so. Deleting it would be a tool removing work it did not write; leaving it
    in `.archon/workflows/` would leave the engine loading a pack the factory no longer
    dispatches.
    """
    root = scratch_repo(tmp / "upgrade")
    result = run([sys.executable, str(HOME / "bin" / "factory.py"), "init", "--yes"],
                 cwd=root, env=NO_ENGINE)
    if result.returncode != 0:
        check("the upgrade fixture installs", False, result.stdout[-600:])
        return

    previous = old_pack_files("HEAD~1")
    check("the previous revision still has a pack to retire", len(previous) > 20,
          f"found {len(previous)} files; this test cannot prove anything without them")
    for rel, text in previous.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    owned = ".archon/workflows/factory/triage/factory-triage.yaml"
    edited = ".archon/workflows/factory/validate/commands/judge.md"
    check("the fixture has a workflow to retire", (root / owned).exists())
    check("the fixture has a prompt to preserve", (root / edited).exists())
    mine = (root / edited).read_text(encoding="utf-8") + NL + "MY OWN RULE: never approve on a Friday." + NL
    (root / edited).write_text(mine, encoding="utf-8")

    # The things that are the operator's, marked so a mistake is visible rather than
    # inferred from a byte compare nobody reads.
    (root / "MISSION.md").write_text("MY MISSION" + NL, encoding="utf-8")
    (root / ".factory" / "holdout" / "HOLDOUT.md").write_text("MY HOLDOUT" + NL,
                                                              encoding="utf-8")
    (root / ".factory" / "locks" / "floor.json").write_text('{"unit_tests": 41}' + NL,
                                                            encoding="utf-8")
    (root / "harness" / "END-TO-END.md").write_text("MY JOURNEYS" + NL, encoding="utf-8")
    config_text = (root / "factory" / "config.py").read_text(encoding="utf-8")
    config_text = config_text.replace('PROTECTED_EXTRA: list[str] = [',
                                      'PROTECTED_EXTRA: list[str] = [' + NL
                                      + '    "app/billing/**",')
    (root / "factory" / "config.py").write_text(config_text, encoding="utf-8")

    dry = run([sys.executable, str(HOME / "bin" / "sync-to.py"), str(root), "--dry-run"],
              cwd=HOME)
    check("a dry run reports the retirement", "RETIRED" in dry.stdout,
          dry.stdout[-800:] + dry.stderr[-600:])
    check("and changes nothing", (root / owned).exists() and (root / edited).exists(),
          "a dry run that acts is worse than one that does not exist")

    result = run([sys.executable, str(HOME / "bin" / "sync-to.py"), str(root)], cwd=HOME)
    check("the sync exits cleanly", result.returncode == 0,
          result.stdout[-800:] + result.stderr[-800:])

    check("a workflow this template shipped is retired",
          not (root / owned).exists(),
          "its content is byte-for-byte what the template put there, so it is the "
          "template's to remove")
    check("and so is a module it no longer ships",
          not (root / "factory" / "nodeio.py").exists())
    check("the retired pack directory is gone",
          not (root / ".archon" / "workflows" / "factory").exists(),
          "an empty pack directory is still a directory the engine walks")
    check("nothing outside the named list was removed",
          (root / "factory" / "state.py").exists() and (root / "harness" / "ci.py").exists(),
          "the retirement list is named exactly so a bug in it cannot take a tree with it")

    backup = root / ".factory" / "retired" / edited
    check("an EDITED prompt is preserved rather than deleted", backup.exists(),
          "whatever they changed there is a statement about their process")
    if backup.exists():
        check("and preserved intact", backup.read_text(encoding="utf-8") == mine)
    check("and it is out of the pack the engine loads",
          not (root / edited).exists())
    check("the report says where it went", ".factory/retired" in result.stdout
          and edited in result.stdout,
          "a file moved without a report is a file somebody looks for and cannot find")

    check("MISSION.md is untouched",
          (root / "MISSION.md").read_text(encoding="utf-8") == "MY MISSION" + NL)
    check("the holdout is untouched",
          (root / ".factory" / "holdout" / "HOLDOUT.md").read_text(encoding="utf-8")
          == "MY HOLDOUT" + NL,
          "it is the file the whole auto-merge rests on")
    check("the ratchet floor is untouched",
          json.loads((root / ".factory" / "locks" / "floor.json").read_text(encoding="utf-8"))
          == {"unit_tests": 41},
          "overwriting it would lower a floor without a human, which is the ratchet gone")
    check("the journeys are untouched",
          (root / "harness" / "END-TO-END.md").read_text(encoding="utf-8") == "MY JOURNEYS" + NL)
    # THE ONE ENTRY ON THE NEVER LIST THE SYNC CAN ACTUALLY REACH. `factory/` is
    # synced wholesale as machinery, so `config.py` is inside it and is protected by
    # name; MISSION, the holdout, the floor and the journeys are not on any sync path
    # at all, and the checks about them below are a backstop against that list growing.
    check("project settings survive the sync",
          '"app/billing/**"' in (root / "factory" / "config.py").read_text(encoding="utf-8"),
          "a protected path deleted by a sync leaves the guard printing PROTECTED_OK "
          "over the paths it was told to defend")

    check("the machinery itself was brought forward",
          (root / "factory" / "sdlc.py").exists()
          and "archon-admit" in (root / "factory" / "config.py").read_text(encoding="utf-8"),
          "config.py is never overwritten, so the sync has to REPORT the settings it "
          "is missing -- and this install got them because init wrote it")

    check("a kept skill still pointing into the retired pack is reported",
          "KEPT, AND NOW POINTING AT NOTHING" in result.stdout,
          "add-only protects a prompt somebody rewrote, and its cost is that a file "
          "which was never edited also never gets the update -- here the update was "
          "'this path no longer exists'")

    again = run([sys.executable, str(HOME / "bin" / "sync-to.py"), str(root)], cwd=HOME)
    check("a second sync has nothing left to retire", "RETIRED" not in again.stdout,
          "retirement has to be idempotent; a report that fires forever is one people "
          "learn to scroll past")

    result = run([sys.executable, "factory/_selftest.py", "--quiet"], cwd=root)
    check("the upgraded install passes the self-test", result.returncode == 0,
          result.stdout[-1200:])


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # EACH GROUP SURVIVES THE OTHER'S CRASH. A break in the copy plan takes the
        # upgrade fixture's ground out from under it, and an exception there would
        # report nothing about the checks it never reached -- which from the outside
        # looks exactly like a test that was never run.
        for group in (fresh_install_checks, migration_checks):
            try:
                group(tmp)
            except Exception as error:  # noqa: BLE001
                check(group.__name__ + " ran to completion", False,
                      f"{type(error).__name__}: {error}")

    if FAILURES:
        print("Installing or upgrading this is broken:")
        for line in FAILURES:
            print("  FAIL  " + line)
        print(f"INSTALL_TESTS_FAILED checks={CHECKS} failed={len(FAILURES)}")
        return 1
    print("A fresh install works, and an existing one comes forward with its work intact.")
    print(f"INSTALL_TESTS_PASSED checks={CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
