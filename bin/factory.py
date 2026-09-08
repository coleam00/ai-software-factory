#!/usr/bin/env python3
"""factory -- turn a repository into one that ships its own code.

    factory init            install the factory into this repo
    factory doctor          audit it; refuses a dial the evidence does not support
    factory tick            one dispatcher pass (what the schedule calls)
    factory run <wf> <tgt>  dispatch one workflow by hand
    factory accept <tgt>    agree with a held PR's recorded assumptions
    factory level [N]       read or set the autonomy dial
    factory arm | disarm    install or remove the schedule
    factory halt | resume   the stop button
    factory status          what is in flight, and what the dial is

THE ENGINE COMES WITH IT. `init` installs Archon if it is not already here, the same
way installing OpenClaw gets you Pi: you asked for a factory, and the workflow engine
underneath is an implementation detail you are allowed to ignore until you want it.
When you do want it, it is a normal Archon install with a normal workflow pack in
`.archon/workflows/`, and every workflow it dispatches is a YAML file you can read,
edit, and run by hand.

NOTHING HERE IS CLEVER. It copies files, fills in what it can detect, and refuses to
turn anything on that has not been shown to work.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.0"
HOME = Path(__file__).resolve().parent.parent
TEMPLATE = HOME / "template"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


# --- output -------------------------------------------------------------------


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(f"  {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  ! {msg}", flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"\n{msg}\n", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    try:
        # On Windows CreateProcess can skip an earlier .cmd launcher and find a
        # later .exe. Resolve PATH/PATHEXT once, then execute that exact file.
        cmd = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def repo_root() -> Path:
    rc, out = run(["git", "rev-parse", "--show-toplevel"])
    if rc != 0:
        die(
            "Not a git repository.\n"
            "An AI software factory keeps its state in git and on GitHub -- branches, labels, "
            "pull requests. There is nowhere for it to live here.\n\n"
            "  git init && git remote add origin <url>"
        )
    return Path(out.strip()).resolve()


# --- the engine ---------------------------------------------------------------


# THE WORKFLOWS THIS FACTORY DISPATCHES. Named here as well as in
# `template/factory/config.py` because this runs BEFORE the template is copied, and an
# install that links an engine without them produces a factory whose every dispatch
# fails at the first tick. `bin/audit.py` checks the two lists agree.
REQUIRED_WORKFLOWS = ("archon-admit", "archon-ship", "archon-accept", "archon-revise-pr",
                      "archon-regress", "archon-merge")

# Where the engine comes from when it is not already installed, at the EXACT revision
# this factory has been tested against.
#
# This is the tested integration revision of the focused SDLC pull requests. The PRs
# remain separate for upstream review. Pinning the commit makes this consumer usable
# while that review proceeds and prevents a moving branch from changing new installs.
#
# It is a constant rather than an environment variable on purpose: a pin somebody can
# override from the shell is a pin that gets overridden by a copied command line, and
# the whole reason it exists is that "which Archon" is not a per-run decision.
ARCHON_SOURCE = "https://github.com/coleam00/Archon"
ARCHON_REF = "b18a6f82b631eff4d46529499952e0f85a250c19"


ARCHON_BIN = os.environ.get("FACTORY_ARCHON_BIN") or "archon"


def archon_has_workflows(binary: str = "") -> tuple[bool, str]:
    """Whether an engine on PATH can run this factory. Named workflows, not a version.

    A VERSION STRING IS NOT THE QUESTION. The factory needs six workflows to exist, and
    which release first carried them is exactly the sort of fact that goes stale in a
    comment. Asking the engine what it has is the only answer that cannot drift.
    """
    rc, listed = run([binary or ARCHON_BIN, "workflow", "list"], timeout=180)
    if rc != 0:
        return False, f"`{binary} workflow list` exited {rc}"
    missing = [name for name in REQUIRED_WORKFLOWS if name not in listed]
    return (not missing), ("missing: " + " ".join(missing) if missing else "")


def _bun_global_bin() -> Path:
    """Where bun puts global binaries. `bun pm bin -g` knows, but on a home with no
    global installs yet it prints advice instead of a path, so fall back to the
    documented default."""
    rc, out = run(["bun", "pm", "bin", "-g"], timeout=60)
    for line in (out or "").splitlines():
        line = line.strip()
        if rc == 0 and line and os.path.isabs(line) and " " not in line:
            return Path(line)
    return Path(os.environ.get("BUN_INSTALL", "") or (Path.home() / ".bun")) / "bin"


def ensure_archon(auto: bool) -> bool:
    """Install Archon if it is not here. The OpenClaw/Pi step.

    A user who wanted a software factory did not ask to learn a workflow engine. They get
    one anyway -- it is what runs every node -- but they should not have to install it
    as a separate errand, and they should not discover it exists via an error message
    forty seconds into the first dispatch.

    Three outcomes, and the middle one is the point: an engine that can run this
    factory (accepted), an engine that cannot (refused, with what to do about it), and
    no engine at all (built from the pinned revision, then asked the same question).
    """
    if shutil.which(ARCHON_BIN):
        rc, out = run([ARCHON_BIN, "version"], timeout=120)
        step(f"engine        {out.strip().splitlines()[0] if out.strip() else 'archon (version unknown)'}")
        ok, why = archon_has_workflows()
        if ok:
            step(f"workflows     {' '.join(REQUIRED_WORKFLOWS)}")
            return True
        # REFUSED RATHER THAN SHADOWED. Linking a second `archon` behind one already on
        # PATH produces an install whose behaviour depends on lookup order, which is
        # the worst of both outcomes: it appears to work and dispatches with whichever
        # engine won. The operator has to decide which engine this factory uses.
        warn(f"the `archon` on PATH cannot run this factory ({why}).\n"
             f"    Update it to a build carrying the SDLC pack, or point the factory at\n"
             f"    another one with FACTORY_ARCHON_BIN=/path/to/archon.")
        return False

    if not re.fullmatch(r"[0-9a-f]{40}", ARCHON_REF):
        warn(f"cannot build the engine: ARCHON_REF is `{ARCHON_REF}`, not a commit.\n"
             f"    This build of the factory has not been pinned to a tested Archon yet.\n"
             f"    Install an engine carrying {' '.join(REQUIRED_WORKFLOWS)} yourself, or\n"
             f"    set ARCHON_REF in bin/factory.py to the revision you validated.")
        return False

    say()
    say("  Archon is not installed. It is the workflow engine this factory runs on --")
    say("  every node you will read about in .archon/workflows/ is executed by it.")
    say()

    # FROM SOURCE, NOT FROM NPM. Archon is not published to a registry (the `archon`
    # package on npm is an unrelated tool), so the first version of this ran
    # `bun add -g @coleam00/archon`, got a 404, and every viewer following the README
    # stopped there. Verified on a fresh Ubuntu VPS. It is also how the factory gets a
    # known revision: it dispatches six workflows from the SDLC pack and needs the one
    # that was tested, not whichever release is newest.
    if not shutil.which("git") or not shutil.which("bun"):
        warn(
            "Archon is built from source and needs git and bun on PATH.\n"
            "    Install bun (curl -fsSL https://bun.sh/install | bash), open a new\n"
            "    shell, and re-run. Or install Archon yourself:\n"
            "      https://github.com/coleam00/archon"
        )
        return False

    source = os.environ.get("FACTORY_ARCHON_SOURCE", ARCHON_SOURCE)
    ref = ARCHON_REF
    dest = Path(os.environ.get("FACTORY_ARCHON_DIR", "") or (Path.home() / "archon-src"))

    # NO TERMINAL MEANS YES. The common way this runs now is an agent on a laptop
    # driving the box over `ssh host 'python ... init'`, with no tty attached; there
    # `input()` raises EOFError and the install died on the one question it asked.
    # A caller with no terminal cannot answer, so asking is not an option; installing
    # the engine the README already told them about is the answer they came for.
    if not auto and sys.stdin.isatty():
        say(f"  Clone {source} at `{ref}` into {dest} and link it? [Y/n] ")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer and not answer.startswith("y"):
            warn("Skipped. The factory will not dispatch anything until it is installed.")
            return False
    elif not auto:
        step("no terminal   installing the engine without asking (non-interactive run)")

    if (dest / ".git").exists():
        step(f"engine        {dest} exists; fetching {ref}")
        rc, out = run(["git", "-C", str(dest), "fetch", "--quiet", "origin", ref], timeout=600)
        if rc == 0:
            rc, out = run(["git", "-C", str(dest), "checkout", "--quiet", "FETCH_HEAD"], timeout=120)
    else:
        step(f"cloning       {source} @ {ref}")
        rc, out = run(["git", "clone", "--quiet", "--no-checkout", source, str(dest)], timeout=900)
        if rc == 0:
            rc, out = run(["git", "-C", str(dest), "checkout", "--quiet", ref], timeout=120)
    if rc != 0:
        warn(f"could not fetch Archon at `{ref}`:\n{out[-800:]}")
        return False

    step("installing    bun install (in the clone)")
    rc, out = run(["bun", "install", "--frozen-lockfile", "--silent"], timeout=1800, cwd=dest)
    if rc != 0:
        warn(f"bun install failed:\n{out[-1200:]}")
        return False
    # THE BINARY IS A SYMLINK TO THE CLI ENTRY, which is how a working install looks
    # (`~/.bun/bin/archon -> .../@archon/cli/src/cli.ts`, shebang `#!/usr/bin/env bun`).
    # `bun link` alone only registers the package name and puts nothing on PATH --
    # verified in an isolated home on a VPS: linked, and `archon` still not found.
    entry = dest / "packages" / "cli" / "src" / "cli.ts"
    if not entry.exists():
        warn(f"the clone has no packages/cli/src/cli.ts; the layout changed and this "
             f"installer needs updating.")
        return False
    bin_dir = _bun_global_bin()
    bin_dir.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            shim = bin_dir / "archon.cmd"
            shim.write_text(f'@echo off\r\nbun "{entry}" %*\r\n', encoding="utf-8")
        else:
            link = bin_dir / "archon"
            if link.is_symlink() or link.exists():
                link.unlink()
            os.symlink(entry, link)
            entry.chmod(entry.stat().st_mode | 0o111)
    except OSError as e:
        warn(f"could not put `archon` in {bin_dir}: {e}")
        return False
    step(f"linking       {bin_dir / 'archon'} -> {entry}")

    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        warn(
            f"{bin_dir} is not on your PATH. It is for the rest of this install; add it to\n"
            f"    your shell profile (bun's installer does this) so the factory finds it later."
        )
    if not shutil.which("archon"):
        warn(f"`archon` is still not on PATH after linking into {bin_dir}.")
        return False
    rc, out = run(["archon", "version"], timeout=120)
    step(f"engine        {out.strip().splitlines()[0] if out.strip() else 'archon installed'}")
    # THE PIN IS A CLAIM UNTIL THE BUILT ENGINE ANSWERS. A ref that has moved, a clone
    # that checked out something else, a pack that did not build: each of them ends
    # with `archon` on PATH and a factory that cannot dispatch, and the install would
    # otherwise report success.
    ok, why = archon_has_workflows("archon")
    if not ok:
        warn(f"the engine built from {ARCHON_REF[:12]} does not carry this factory's "
             f"workflows ({why}). The pin is wrong.")
        return False
    step(f"workflows     {' '.join(REQUIRED_WORKFLOWS)}")
    return True


# --- detection ----------------------------------------------------------------


def detect(root: Path) -> dict:
    """Look at the repo and propose the settings. Proposing a default is not a
    shortcut, it is a better question: "I am going to run these commands -- anything
    else?" gets a more useful answer than "what are your test commands?", takes ten
    seconds, and cannot be answered wrong by someone who has not built one of these.
    """
    found: dict = {"static": "", "unit": "", "unit_count_pattern": "", "driver": "http",
                   "language": "unknown", "start": "", "test_dir": ""}

    files = {p.name for p in root.iterdir() if p.is_file()}

    if "pyproject.toml" in files:
        found["language"] = "python"
        text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        uv = shutil.which("uv") is not None
        prefix = "uv run " if uv else "python -m "
        if "ruff" in text:
            found["static"] = f"{'uv run ' if uv else ''}ruff check ."
        else:
            found["static"] = "python -m compileall -q ."
        if "pytest" in text:
            found["unit"] = f"{'uv run ' if uv else 'python -m '}pytest -q"
            found["unit_count_pattern"] = r"(\d+) passed"
        else:
            found["unit"] = "python -m unittest discover -s tests"
            found["unit_count_pattern"] = r"Ran (\d+) test"
    elif "package.json" in files:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace") or "{}")
        scripts = pkg.get("scripts", {})
        bun = (root / "bun.lockb").exists() or (root / "bun.lock").exists()
        runner = "bun" if bun and shutil.which("bun") else "npm"
        found["language"] = "node"
        if "typecheck" in scripts:
            found["static"] = f"{runner} run typecheck"
        elif (root / "tsconfig.json").exists():
            found["static"] = f"{'bun x' if runner == 'bun' else 'npx'} tsc --noEmit"
        if "test" in scripts:
            found["unit"] = f"{runner} run test"
            found["unit_count_pattern"] = r"(\d+) pass"
    elif "go.mod" in files:
        found.update(language="go", static="go vet ./...", unit="go test ./...",
                     unit_count_pattern=r"ok\s+\S+\s+([\d.]+)s")
    elif "Cargo.toml" in files:
        found.update(language="rust", static="cargo clippy -- -D warnings",
                     unit="cargo test", unit_count_pattern=r"(\d+) passed")

    for candidate in ("tests", "test", "spec", "__tests__"):
        if (root / candidate).is_dir():
            found["test_dir"] = candidate
            break

    ci = root / ".github" / "workflows"
    if ci.is_dir():
        found["ci"] = sorted(p.name for p in ci.glob("*.y*ml"))

    return found


# Headless invocations for the CLIs people actually have. The PROMPT ARRIVES ON
# STDIN in every one of them, which is the only thing agentcheck.py assumes -- so a
# CLI that is not on this list still works, it just has to be typed in by hand.
# Only the `claude` invocation has been run end to end here. The others are the
# documented headless form for each tool and are offered as a starting point.
AGENT_CLIS = [
    # --allowedTools IS LOad-BEARING. `--permission-mode acceptEdits` alone accepts
    # file edits and still refuses Bash, so the journey agent could not run curl,
    # python, node or anything else that speaks HTTP. Measured: it reported 13 of 13
    # assertions failed with "This command requires approval", which is the RIGHT
    # behaviour from the agent and the wrong default from us.
    ("claude", "claude -p --allowedTools Bash,Read,Write --permission-mode acceptEdits"),
    ("codex", "codex exec -"),
    ("pi", "pi --mode json --print"),
    ("goose", "goose run --instructions -"),
    ("amp", "amp -x"),
]


def detect_agent() -> str:
    """Which coding agent is on this machine, if any.

    Detected rather than asked. A question whose answer is sitting on PATH is a
    question nobody should be typing, and the setup interview is short on purpose.
    """
    for exe, cmd in AGENT_CLIS:
        if shutil.which(exe):
            return cmd
    return ""


def is_greenfield(root: Path) -> bool:
    """Source files that are not scaffolding, or not.

    Decided by LOOKING, not by asking -- the refusals below are written for a
    brownfield repo and misfire badly on a greenfield one, where they are trivially
    true and carry no signal at all.
    """
    skip = {".git", ".github", ".claude", ".archon", ".factory", "docs", "node_modules", ".venv"}
    code = 0
    for p in root.rglob("*"):
        if p.is_dir() or any(part in skip for part in p.parts):
            continue
        if p.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".cs"}:
            code += 1
        if code > 3:
            return False
    return True


# --- init ---------------------------------------------------------------------

COPY_PLAN = [
    ("factory", "factory"),
    ("harness", "harness"),
    # The interactive half of the same loop. Each skill names the upstream workflow the
    # factory dispatches and the arguments it supplies, so running a step by hand runs
    # the same reasoning the unattended lap runs. None of them restates a node prompt:
    # those live in the pack now, and a copy here would first show its drift as an
    # unattended run doing something you thought you had already changed.
    (".claude/skills", ".claude/skills"),
    (".factory/holdout/HOLDOUT.md", ".factory/holdout/HOLDOUT.md"),
    (".factory/locks/floor.json", ".factory/locks/floor.json"),
    # THE THINGS THAT ACTUALLY RUN IT. Everything above is machinery; without these
    # three an install has every part and no engine, and `doctor` says "nothing
    # scheduled -- the factory only runs when you run it". That is precisely what
    # happened: the example repository had all of the above installed and its loop,
    # monitor and notifier had to be written by hand afterwards, which is not an
    # install, it is a parts list.
    (".factory/loop.sh", ".factory/loop.sh"),
    (".factory/monitor.py", ".factory/monitor.py"),
    (".factory/notify.sh", ".factory/notify.sh"),
]

# FACTORY.md is here because the docs keep telling you to write things in it -- the
# level you are at and the date you reached it, the date you last used the stop button
# on purpose, what would have to be true to go further, which rung each mutation was
# aimed at. A file the walkthrough treats as required and `init` does not create is a
# file everybody skips, and what gets skipped with it is the record of WHY the dial is
# where it is. Six months on, that record is the difference between a decision and a
# setting nobody has touched.
# Directories the repository owns, where install means "add what is missing"
# rather than "take the whole directory or leave it alone".
MERGE_DIRS = {".claude/skills"}

GOVERNANCE = ["MISSION.md", "FACTORY_RULES.md", "FACTORY.md"]

GITIGNORE_LINES = (
    (TEMPLATE / "gitignore-additions.txt").read_text(encoding="utf-8").splitlines()
    if (TEMPLATE / "gitignore-additions.txt").exists()
    else []
)


def cmd_init(args: argparse.Namespace) -> int:
    root = repo_root()
    say()
    say(f"  factory {VERSION}")
    say(f"  installing into {root}")
    say()

    # --- the engine -----------------------------------------------------------
    have_engine = ensure_archon(args.yes)

    # --- what is here already -------------------------------------------------
    green = is_greenfield(root)
    found = detect(root)
    step(f"repo          {'greenfield' if green else 'brownfield'}, {found['language']}")
    if found["unit"]:
        step(f"tests         {found['unit']}")
    if found["static"]:
        step(f"static        {found['static']}")

    rc, remote = run(["git", "remote", "get-url", "origin"], cwd=root)
    if rc != 0:
        warn(
            "No `origin` remote. The labels on GitHub ARE this factory's state machine "
            "-- without a remote there is nowhere for the queue to live."
        )
    else:
        step(f"remote        {remote.strip()}")

    if not shutil.which("gh"):
        warn("`gh` is not installed. The state machine, the gate and the merge all use it.")
    else:
        rc, _ = run(["gh", "auth", "status"], timeout=120)
        if rc != 0:
            warn("`gh` is installed but not authenticated -- run `gh auth login`.")

    # --- copy -----------------------------------------------------------------
    say()
    copied, skipped = [], []
    for src_rel, dst_rel in COPY_PLAN:
        src, dst = TEMPLATE / src_rel, root / dst_rel
        if not src.exists():
            continue

        # MERGE, DO NOT SKIP, where the destination is a shared directory.
        #
        # `.claude/skills/` belongs to the repository, not to this tool. Treating it
        # as one unit means a repo that already has a single skill of its own gets
        # NONE of the factory's -- silently, with a line saying "kept", which reads
        # like the right thing happened. Almost every repo worth installing this into
        # already has that directory.
        #
        # Per-entry, so an existing `factory-plan` you have edited is still yours.
        if src.is_dir() and dst_rel in MERGE_DIRS and dst.exists() and not args.force:
            added = []
            for entry in sorted(src.iterdir()):
                target = dst / entry.name
                if target.exists():
                    skipped.append(f"{dst_rel}/{entry.name}")
                    continue
                if entry.is_dir():
                    shutil.copytree(entry, target)
                else:
                    shutil.copy2(entry, target)
                added.append(entry.name)
            if added:
                copied.append(f"{dst_rel}/ ({len(added)}: " + " ".join(added) + ")")
            continue

        if dst.exists() and not args.force:
            skipped.append(dst_rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        copied.append(dst_rel)

    for name in GOVERNANCE:
        dst = root / name
        if dst.exists() and not args.force:
            skipped.append(name)
            continue
        shutil.copy2(TEMPLATE / name, dst)
        copied.append(name)

    for d in (".factory/runs", ".factory/locks-runtime", ".factory/findings",
              ".factory/assumptions", ".factory/followups"):
        (root / d).mkdir(parents=True, exist_ok=True)

    decisions = root / ".factory" / "decisions.md"
    if not decisions.exists():
        decisions.write_text(DECISIONS_SEED, encoding="utf-8")
        copied.append(".factory/decisions.md")

    for path in copied:
        step(f"+ {path}")
    for path in skipped:
        step(f"= {path} (kept -- use --force to overwrite)")

    # --- wire the detected commands in ----------------------------------------
    hc = root / "harness" / "harness.config.json"
    if hc.exists():
        cfg = json.loads(hc.read_text(encoding="utf-8"))
        # A DEFAULT FROM ANOTHER LANGUAGE IS WORSE THAN NO DEFAULT. The template
        # ships Python commands, and a Node repo where detection finds no typecheck
        # script kept `python -m compileall -q .` -- which compiles zero Python files
        # in a JavaScript repo and exits 0. That prints STATIC_OK on a repo with no
        # static checking at all, which is the "empty is not pass" failure this whole
        # gate exists to prevent, shipped as a default.
        #
        # Blanked instead. `ci.py` reports an empty step as <STEP>_SKIPPED, loudly.
        PY_DEFAULTS = {"python -m compileall -q .", "python -m pytest -q"}
        if found["language"] not in ("python", "unknown"):
            # `rung`, not `step` -- `step()` is the module-level printer and a loop
            # variable of that name shadows it for the whole function, which surfaced
            # 100 lines earlier as UnboundLocalError on a line nothing had touched.
            for rung in ("static", "unit"):
                if not found[rung] and cfg.get(rung) in PY_DEFAULTS:
                    cfg[rung] = ""
                    warn(
                        f"no {rung} command found for a {found['language']} repo, and the "
                        f"shipped default is Python. Cleared it: the rung now says "
                        f"{rung.upper()}_SKIPPED rather than passing on nothing. Set it in "
                        f"harness/harness.config.json."
                    )
            # The library driver's import check is language-specific too, and the
            # shipped one is `python -c "import app"`.
            probe = {
                "node": 'node -e "require(\'./\')"',
                "go": "go build ./...",
                "rust": "cargo build",
            }.get(found["language"])
            if probe:
                cfg.setdefault("library", {})["import_check"] = probe

        agent = detect_agent()
        if agent and not cfg.get("agent", {}).get("cmd"):
            cfg.setdefault("agent", {})["cmd"] = agent
            step(f"agent         {agent} (drives the journeys)")
        elif not agent:
            warn(
                "No coding-agent CLI found on PATH. The end-to-end and holdout rungs "
                "are driven by one, so set `agent.cmd` in harness/harness.config.json "
                "before the gate can run."
            )
        if found["static"]:
            cfg["static"] = found["static"]
        if found["unit"]:
            cfg["unit"] = found["unit"]
        if found["unit_count_pattern"]:
            cfg["unit_count_pattern"] = found["unit_count_pattern"]
        hc.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        step("~ harness/harness.config.json (wired to what was found above)")

    # --- gitignore ------------------------------------------------------------
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8", errors="replace") if gi.exists() else ""
    additions = [ln for ln in GITIGNORE_LINES if ln and not ln.startswith("#") and ln not in existing]
    if additions or "factory" not in existing:
        with gi.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(GITIGNORE_LINES) + "\n")
        step("~ .gitignore")

    # --- line endings ---------------------------------------------------------
    # Pinned in the repo so it does not depend on anyone's git config. Without it,
    # a checkout on Windows rewrites every script to CRLF, and on the Linux box that
    # runs the factory each one fails with "bad interpreter" -- which reads as "the
    # file is missing".
    ga = root / ".gitattributes"
    if not ga.exists():
        ga.write_text("* text=auto eol=lf\n*.sh text eol=lf\n*.py text eol=lf\n", encoding="utf-8")
        step("+ .gitattributes (pins LF; scripts otherwise break on a Linux runner)")
    run(["git", "config", "core.longpaths", "true"], cwd=root)

    # --- labels ---------------------------------------------------------------
    if shutil.which("gh") and rc == 0:
        say()
        step("creating the label vocabulary (these labels ARE the state machine)")
        rc2, out = run([sys.executable, "factory/state.py", "init-labels"], cwd=root, timeout=300)
        for line in out.strip().splitlines()[-20:]:
            step(f"  {line}")

    # --- what now -------------------------------------------------------------
    say()
    # Read the dial back rather than assuming a fresh install: a repo that already
    # carries a committed factory/config.py (a clone of a working factory) installs
    # at whatever level that file says, and telling its owner "0" is a lie.
    dial = 0
    try:
        m = re.search(r'AUTONOMY = _env_int\("FACTORY_AUTONOMY", (\d+)\)',
                      (root / "factory" / "config.py").read_text(encoding="utf-8"))
        dial = int(m.group(1)) if m else 0
    except OSError:
        pass
    if dial == 0:
        say("  Installed. The dial is at 0, so nothing dispatches until you raise it.")
    else:
        say(f"  Installed. The dial is at {dial} (from the committed factory/config.py); "
            f"run the doctor before arming it here.")
    say()
    say("  Three files are yours. Nothing can write them for you:")
    say()
    say("    MISSION.md                what this is, and what it must never become.")
    say("    harness/END-TO-END.md     the journeys a real user takes.")
    say("    .factory/holdout/HOLDOUT.md   the same product, composed, where the")
    say("                              builder is blocked from reading it.")
    say()
    say("  An agent can write all three with you. Ask it to run the factory-setup skill.")
    say()
    say("  Then, in order:")
    say("    factory doctor                      # it will fail. That is it working.")
    say("    factory run implement gh:issue:1    # one lap, by hand")
    say("    factory level 1                     # once a lap has completed")
    say()
    if not have_engine:
        warn("Archon is still missing -- nothing will dispatch until it is installed.")
    return 0


DECISIONS_SEED = """# Open decisions

<!--
  THE POINT OF THIS FILE IS THAT A DECISION IS ASKED ONCE.

  Without it, one unmade product decision is re-discovered by every issue that
  touches it and reported as a fresh escalation each time. The human sees four
  interruptions and concludes the factory refuses too much work -- when it actually
  refused one thing, four times.

  READ ORDER, for every node about to stop:
    1. Is the decision already ANSWERED below? Then it is not open. Use it, cite the ID.
    2. Is it OPEN below? Then do not re-ask it. Reference the ID and plan around it.
    3. Neither? Only then is it new -- and even then, most product values are decided
       and recorded in ASSUMPTIONS rather than escalated. See FACTORY_RULES §7.

  A human answers by moving an entry to Answered and writing the value. That single
  edit unblocks everything listed against it.
-->

## Open

<!-- One per decision. Blocks: list every issue waiting on it, so the cost is visible. -->

## Answered

<!-- Never delete one. A decision with its date is why the code looks the way it
     does, and it is the first thing anybody re-litigating it needs to read. -->
"""


# --- everything else ----------------------------------------------------------


def factory_py(root: Path, name: str, *argv: str) -> int:
    script = root / "factory" / name
    if not script.exists():
        die(f"{script} is missing. Run `factory init` in this repo first.")
    return subprocess.run([sys.executable, str(script), *argv], cwd=str(root)).returncode


def cmd_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    extra = ["--level", str(args.level)] if args.level is not None else []
    return factory_py(root, "doctor.py", *extra)


def cmd_tick(args: argparse.Namespace) -> int:
    root = repo_root()
    return factory_py(root, "dispatch.py", *(["--dry-run"] if args.dry_run else []))


def cmd_run(args: argparse.Namespace) -> int:
    """One unit of work by hand, through the same path the dispatcher uses.

    NOT A RAW `archon workflow run`, and that changed here. Building the command line
    by hand gave a run with no trusted gate profile, no work order, no lock and nothing
    watching for its result -- so a hand-run validation produced a receipt the factory
    never read and a hand-run delivery opened a pull request nothing knew about. It goes
    through `sdlc.launch` so a manual lap is a lap, not a demonstration.

    The DIAL is not enforced for a person typing a command; the STOP button is, and so
    is `merge`, whose authority comes from an acceptance receipt rather than from
    whoever typed it.
    """
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import sdlc  # type: ignore  # noqa: E402

    try:
        launched = sdlc.launch(args.workflow, args.target or "", manual=True,
                               detach=args.detach)
    except Exception as error:  # noqa: BLE001
        die(f"{args.workflow} {args.target or ''}: {error}")
    if not launched:
        warn("nothing dispatched: that target is already in flight, or the flood cap "
             "holds it until tomorrow.")
        return 1
    if args.detach:
        say("  dispatched. `factory tick` applies the result once the run settles.")
    return 0


def cmd_level(args: argparse.Namespace) -> int:
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import config  # type: ignore  # noqa: E402

    if args.value is None:
        say()
        say(f"  autonomy dial: {config.AUTONOMY}")
        say()
        for n, what in enumerate(LADDER):
            mark = ">" if n == config.AUTONOMY else " "
            say(f"  {mark} {n}  {what}")
        say()
        say("  Raising it is a deliberate act: `factory level <n>`.")
        say("  The doctor refuses a level the evidence does not support.")
        return 0

    want = args.value
    if not 0 <= want <= 5:
        die("The dial runs 0 to 5.")

    say()
    say(f"  checking whether this repo has earned level {want}...")
    say()
    rc = factory_py(root, "doctor.py", "--level", str(want))
    if rc != 0:
        say()
        die(
            f"Refused. The dial stays at {config.AUTONOMY}.\n"
            f"A dial that outruns its evidence is the failure this whole system exists "
            f"to prevent."
        )

    # Written to the config file, not just the environment: a level set for one
    # invocation is a level that quietly reverts on the next scheduled tick.
    cfg = root / "factory" / "config.py"
    text = cfg.read_text(encoding="utf-8")
    new = re.sub(
        r'AUTONOMY = _env_int\("FACTORY_AUTONOMY", \d+\)',
        f'AUTONOMY = _env_int("FACTORY_AUTONOMY", {want})',
        text,
    )
    if new == text:
        die("Could not find the AUTONOMY line in factory/config.py -- set it by hand.")
    cfg.write_text(new, encoding="utf-8")

    say()
    say(f"  Dial raised to {want}: {LADDER[want]}")
    if want >= 3:
        say()
        say("  Level 3 is the one that matters: code now merges without a human")
        say("  reading it. Watch one full cycle before you stop watching.")
    say()
    say("  Commit factory/config.py -- it is a protected file, so this is a human commit.")
    return 0


LADDER = [
    "workflows exist, run by hand",
    "an accepted issue becomes a branch and an open PR",
    "+ the validator runs and writes a verdict",
    "+ the validator AUTO-MERGES when every structural gate is green   <- the target",
    "+ it triages its own issues, and the scheduled regression may file its own bugs",
]


def cmd_arm(args: argparse.Namespace) -> int:
    root = repo_root()
    return factory_py(root, "trigger.py", "--install")


def cmd_disarm(args: argparse.Namespace) -> int:
    root = repo_root()
    return factory_py(root, "trigger.py", "--remove")


def cmd_halt(args: argparse.Namespace) -> int:
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import config  # type: ignore  # noqa: E402

    config.STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    reason = args.reason or "halted by hand"
    config.STOP_FILE.write_text(
        f"{reason}\nat {datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8"
    )
    say()
    say(f"  STOPPED. {config.STOP_FILE} is present; nothing will dispatch.")
    say("  The remote half is an open issue labelled `factory:stop` -- reachable from")
    say("  a phone, and it fails closed: any error reading it also counts as stopped.")
    say()
    say("  `factory resume` to lift it.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import config  # type: ignore  # noqa: E402

    if config.STOP_FILE.exists():
        config.STOP_FILE.unlink()
        say(f"  removed {config.STOP_FILE}")
    else:
        say("  no local stop file")
    say("  If an open issue still carries `factory:stop`, remove that label too.")
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    """Agree with the calls the factory made, and send the PR back to be revalidated.

    THE HALF OF THE HOLD THAT WAS MISSING. The gate holds a merge on recorded
    assumptions, and the assumptions live in a file it re-reads every run -- so
    without this, a held PR holds again on the next validation, and again, forever. A
    hold nobody can clear is not a hold, it is a stall with good manners.

    Accepting ARCHIVES rather than deletes. What the factory chose, and the fact that
    a person agreed on a date, is the record of how a decision got made -- and it is
    the thing you will want when the same question comes back in three months.

    It does NOT merge. It sends the PR back to `open`, so the merge still happens
    through a full validation of the tree as it stands. Agreeing with a judgement is
    not the same as skipping the gate that acts on it.
    """
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import config  # type: ignore  # noqa: E402
    import state  # type: ignore  # noqa: E402

    target = args.target
    try:
        item = state.fetch(target)
    except Exception as e:  # noqa: BLE001
        die(f"cannot read {target}: {e}")
        return 1

    if item["_state"] != "held":
        warn(f"{target} is '{item['_state']}', not 'held'. Nothing to accept.")
        return 1

    issue = state.linked_issue(target) if item["_kind"] == "pr" else None
    archived = []
    dest_dir = config.ASSUMPTIONS_DIR / "accepted"
    for key in (target, issue):
        if not key:
            continue
        src = config.ASSUMPTIONS_DIR / f"{key.replace(':', '-')}.txt"
        if not src.exists() or not src.stat().st_size:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = dest_dir / f"{src.stem}-{stamp}.txt"
        header = (
            "# ACCEPTED " + datetime.now(timezone.utc).isoformat() + " for " + target
            + ((" (note: " + args.note + ")") if args.note else "")
            + "\n# A person read these and agreed. Archived, not deleted: what was chosen"
            + "\n# and the fact that somebody signed off is the record of how it was decided."
            + "\n\n"
        )
        dest.write_text(header + src.read_text(encoding="utf-8", errors="replace"),
                        encoding="utf-8")
        src.unlink()
        archived.append(dest.name)

    if not archived:
        step("no recorded assumptions to accept -- the hold was on something else")

    try:
        state.set_state(target, "open")
    except Exception as e:  # noqa: BLE001
        die(f"accepted the assumptions but could not move {target} back to 'open': {e}")
        return 1

    say()
    for name in archived:
        step(f"archived      .factory/assumptions/accepted/{name}")
    step(f"{target} is back to 'open'")
    say()
    say("  It is NOT merged. The next validation runs against the tree as it stands,")
    say("  and merges only if that run is green -- agreeing with a judgement is not")
    say("  the same as skipping the gate that acts on it.")
    say()
    say("  If the hold also named ratchet slack, raise the floor in "
        ".factory/locks/floor.json")
    say("  and commit it, or the next run holds again for that reason.")
    say()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = repo_root()
    sys.path.insert(0, str(root / "factory"))
    import config  # type: ignore  # noqa: E402
    import state  # type: ignore  # noqa: E402

    say()
    say(f"  autonomy      {config.AUTONOMY}  ({LADDER[config.AUTONOMY]})")
    stopped, why = state.stop_requested()
    say(f"  stop button   {'STOPPED -- ' + why if stopped else 'clear'}")
    say(f"  trigger       {'armed' if config.TRIGGER_FILE.exists() else 'not armed'}")

    locks = sorted(config.LOCKS_RUNTIME.glob("*.lock")) if config.LOCKS_RUNTIME.exists() else []
    say(f"  in flight     {len(locks)}/{config.MAX_PARALLEL}" + (
        "  " + " ".join(p.stem for p in locks) if locks else ""))

    # WHERE A DISPATCH ACTUALLY LIVES, and whether one of them is stuck. The journals
    # are outside every checkout so they survive the worktree a run used, which also
    # means nothing in the repository points at them. A result whose effects did not
    # all land is retried every tick and says so on stderr; on a cron loop that is a
    # line in a file, so it gets a line here too.
    try:
        import runtime  # type: ignore  # noqa: E402

        home = runtime.root()
        say(f"  runtime       {home}")
        stuck = []
        for journal in sorted((home / "runs").glob("*/record.json")):
            try:
                record = runtime.read(journal)
            except (OSError, ValueError):
                continue
            if record.get("status") != "applied" and record.get("error"):
                stuck.append((record, journal))
        if stuck:
            say()
            say(f"  NOT APPLIED ({len(stuck)}) -- a run finished and its result did not land:")
            for record, journal in stuck[:5]:
                say(f"    {record.get('action')} {record.get('target') or '-'}: "
                    f"{str(record.get('error'))[:140]}")
                say(f"      {journal}")
    except Exception as e:  # noqa: BLE001
        say(f"  runtime       unavailable: {e}")

    try:
        action, target, reason = state.next_action()
        # SAY THE SAME THING THE DISPATCHER WOULD. `next_action` reads labels and
        # knows nothing about locks, so mid-lap it reports the honest label state --
        # "in-progress with no PR" -- which reads as a stall on a screen that just
        # said a run holds that exact target two lines above. The dispatcher already
        # skips it for this reason; the status must not contradict itself.
        held = {p.stem for p in locks}
        target_key = target.replace(":", "-")
        if any(lk.endswith(target_key) for lk in held):
            say(f"  next          waiting -- {target} is mid-lap ({reason})")
        else:
            say(f"  next          {action} {target}  ({reason})")
    except Exception as e:  # noqa: BLE001
        say(f"  next          unavailable: {e}")

    # HELD PRS, named. A hold is the one outcome that is neither a failure nor an
    # escalation: nothing is wrong, the factory carries on, and a person has to agree
    # with a call it made. That makes it the easiest thing in the system to never
    # look at, so it gets its own line rather than living in a PR comment.
    try:
        held = [p_["_target"] for p_ in state._list("prs", "held")]
    except Exception:  # noqa: BLE001
        held = []
    if held:
        say()
        say(f"  held for you ({len(held)}) -- green, waiting for you to agree:")
        for tgt in held:
            say(f"    {tgt}  -- read the gate comment, then: factory accept {tgt}")

    if config.NEEDS_HUMAN.exists():
        lines = [ln for ln in config.NEEDS_HUMAN.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if lines:
            say()
            say(f"  needs a human ({len(lines)}), most recent last:")
            for ln in lines[-5:]:
                say(f"    {ln}")
    say()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="factory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"factory {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("accept", help="agree with a held PR's recorded assumptions")
    a.add_argument("target", help="e.g. gh:pr:11")
    a.add_argument("--note", default="", help="why you agreed, for the archive")
    a.set_defaults(fn=cmd_accept)

    i = sub.add_parser("init", help="install the factory into this repo")
    i.add_argument("--yes", "-y", action="store_true", help="do not ask before installing the engine")
    i.add_argument("--force", action="store_true", help="overwrite files that already exist")
    i.set_defaults(fn=cmd_init)

    d = sub.add_parser("doctor", help="audit the factory; refuses an unearned dial")
    d.add_argument("--level", type=int, default=None, help="can this repo run at level N?")
    d.set_defaults(fn=cmd_doctor)

    t = sub.add_parser("tick", help="one dispatcher pass")
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(fn=cmd_tick)

    r = sub.add_parser("run", help="dispatch one workflow by hand")
    r.add_argument("workflow", choices=["triage", "implement", "validate", "fix", "regress", "merge"])
    r.add_argument("target", nargs="?", default="")
    r.add_argument("--detach", action="store_true")
    r.set_defaults(fn=cmd_run)

    lv = sub.add_parser("level", help="read or set the autonomy dial")
    lv.add_argument("value", nargs="?", type=int, default=None)
    lv.set_defaults(fn=cmd_level)

    sub.add_parser("arm", help="install the schedule").set_defaults(fn=cmd_arm)
    sub.add_parser("disarm", help="remove the schedule").set_defaults(fn=cmd_disarm)

    h = sub.add_parser("halt", help="the stop button")
    h.add_argument("reason", nargs="?", default="")
    h.set_defaults(fn=cmd_halt)

    sub.add_parser("resume", help="lift the stop button").set_defaults(fn=cmd_resume)
    sub.add_parser("status", help="what is in flight").set_defaults(fn=cmd_status)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
