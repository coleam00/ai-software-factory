"""Install the consumer and preserve customized files during migration."""
from __future__ import annotations
import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
TEMPLATE = HOME / "template"
sys.path.insert(0, str(TEMPLATE / "factory"))
import consumer

PERSONAL = {"factory/config.py", "harness/harness.config.json", "harness/runtime.inputs.json",
            "harness/END-TO-END.md", "harness/mutations/defects.json", "MISSION.md",
            "FACTORY.md", "FACTORY_RULES.md", ".factory/holdout/HOLDOUT.md",
            ".factory/locks/floor.json"}
RETIRED = [".archon/workflows/factory", "factory/nodeio.py", ".factory/notify.sh"] + [
    f".claude/skills/factory-{name}" for name in
    ("setup", "triage", "plan", "implement", "review", "judge", "fix", "e2e", "holdout")]


def within(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Install path resolves outside application: {path}")
    return path


def backup(root: Path, path: Path, dry: bool) -> None:
    within(root, path)
    dest = within(root, root / ".factory/retired" / path.relative_to(root))
    original = dest
    n = 1
    while dest.exists():
        dest = original.with_name(original.name + f".{n}")
        n += 1
    print(f"preserve {path.relative_to(root)} -> {dest.relative_to(root)}")
    if not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def retired_files(root: Path) -> list[Path]:
    result = []
    for rel in RETIRED:
        path = within(root, root / rel)
        if path.is_dir():
            result.extend(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            result.append(path)
    return result


def sync(root: Path, dry: bool = False) -> None:
    root = root.resolve()
    retired = retired_files(root)
    # Report references before removing generated execution surfaces. Originals are
    # all preserved, including custom prompts whose ownership cannot be proved.
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if name not in
                      {".git", "node_modules", ".venv", "retired", "__pycache__"}]
        for filename in files:
            path = Path(directory) / filename
            if path.suffix not in {".md", ".py", ".json", ".sh"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(rel in text for rel in RETIRED):
                print(f"migration reference: {path.relative_to(root)}")
    for path in retired:
        backup(root, path, dry)
        if not dry:
            path.unlink()
    for src in sorted(TEMPLATE.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts or src.suffix in {".pyc", ".pyo"}:
            continue
        rel = src.relative_to(TEMPLATE).as_posix()
        if rel == "gitignore-additions.txt":
            continue
        dest = within(root, root / rel)
        if dest.exists():
            if rel in PERSONAL or dest.read_bytes() == src.read_bytes():
                continue
            backup(root, dest, dry)
        print(f"install {rel}")
        if not dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    ignore = within(root, root / ".gitignore")
    previous = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    additions = [line for line in (TEMPLATE / "gitignore-additions.txt").read_text().splitlines()
                 if line and not line.startswith("#") and line not in previous.splitlines()]
    if additions and not dry:
        with ignore.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(additions) + "\n")
    print("User config and scenarios preserved. Legacy config.py is not imported.")
    print("Remove old cron/Task Scheduler entries and stop old loop processes explicitly.")


def install_source(repository: str, revision: str, cache: Path, bun: str) -> dict:
    import re
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("--revision must be a full lowercase commit SHA")
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    dest = within(cache, cache / revision)
    settings = {"source": str(dest), "revision": revision, "bun": bun,
                "repository": repository, "kind": "integration"}
    if not dest.exists():
        # An independent clone, never a checkout switch in an operator's source.
        consumer.checked(["git", "clone", "--no-hardlinks", "--no-checkout", repository, str(dest)], cache, 900)
        consumer.checked(["git", "checkout", "--detach", revision], dest)
    actual = consumer.checked(["git", "rev-parse", "HEAD"], dest).strip()
    if actual != revision:
        raise ValueError(f"Existing cache has wrong SHA ({actual}); it will not be repointed")
    dirty = consumer.checked(["git", "status", "--porcelain", "--untracked-files=all"], dest)
    if dirty.strip():
        raise ValueError("Existing cache is dirty; it will not be overwritten")
    if not (dest / "node_modules").is_dir():
        consumer.checked([bun, "install", "--frozen-lockfile"], dest, 1800)
    consumer.doctor(settings)
    return settings


def configure(root: Path, settings: dict) -> None:
    path = within(consumer.shared_root(root), consumer.shared_root(root) / consumer.SETTINGS)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
