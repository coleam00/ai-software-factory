"""Materialize the delivering Git revision into an owned ordinary resource root."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


MARKER = ".factory-resource.json"
FORBIDDEN = {".git", ".factory", ".archon", ".claude", ".env", MARKER,
             "holdout.md", "node_modules", ".venv", "__pycache__"}


def checked(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("Git could not verify the delivering revision")
    return result.stdout.strip()


def checked_bytes(argv: list[str], cwd: Path) -> bytes:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("Git could not verify the delivering revision")
    return result.stdout


def resource_digest(root: Path) -> str:
    result = hashlib.sha256()
    files = sorted(path for path in root.rglob("*")
                   if path.is_file() and path != root / MARKER)
    if not files:
        raise ValueError("delivering revision produced an empty resource")
    for path in files:
        name = path.relative_to(root).as_posix()
        content = path.read_bytes()
        result.update(name.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    return result.hexdigest()


def remove_tree(root: Path) -> None:
    def writable(function, path, exc):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    shutil.rmtree(root, onerror=writable)


def owned_destination(destination: Path) -> bool:
    marker = destination / MARKER
    if destination.is_symlink() or not marker.is_file() or marker.is_symlink():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (data.get("version") == 1 and data.get("kind") == "factory-runtime-resource"
            and data.get("resource_root") == str(destination))


def delivery(expected_revision: str | None = None) -> tuple[Path, str]:
    script_root = Path(__file__).resolve().parents[1]
    if script_root.name == "template" and (script_root.parent / ".git").exists():
        script_root = script_root.parent
    repository = Path(checked(["git", "rev-parse", "--show-toplevel"], script_root)).resolve()
    if script_root != repository:
        raise ValueError("resource helper must run from its delivering repository")
    workflow_repository = Path(
        checked(["git", "rev-parse", "--show-toplevel"], Path.cwd())
    ).resolve()
    if workflow_repository != repository:
        raise ValueError("resource helper and workflow must use the same delivering repository")
    actual_revision = checked(["git", "rev-parse", "HEAD"], repository)
    if expected_revision is not None and expected_revision != actual_revision:
        raise ValueError("expected revision does not match the delivering checkout")
    if len(actual_revision) != 40:
        raise ValueError("delivering checkout does not have a full commit revision")
    if checked(["git", "status", "--porcelain", "--untracked-files=no"], repository):
        raise ValueError("delivering checkout has modified tracked files")
    return repository, actual_revision


def configured_includes(config: Path) -> list[str]:
    data = json.loads(config.read_text(encoding="utf-8"))
    includes = data.get("include") if isinstance(data, dict) and data.get("version") == 1 else None
    if (not isinstance(includes, list) or not includes
            or not all(isinstance(value, str) for value in includes)):
        raise ValueError("runtime config requires version 1 and nonempty include paths")
    result = []
    for value in includes:
        path = PurePosixPath(value)
        if (not value or value != path.as_posix() or "\\" in value or path.is_absolute()
                or any(part in {"", ".", ".."} or part.lower() in FORBIDDEN
                       for part in path.parts)):
            raise ValueError("private or escaping include path refused")
        result.append(path.as_posix())
    return result


def committed_files(repository: Path, revision: str, includes: list[str]) -> None:
    for include in includes:
        entry = checked_bytes(["git", "ls-tree", "-z", revision, "--", include], repository)
        if not entry:
            raise ValueError("configured include is missing from the delivering revision")

    entries = checked_bytes(
        ["git", "ls-tree", "-rz", "--full-tree", revision, "--", *includes], repository
    )
    if not entries:
        raise ValueError("configured includes produced an empty resource")
    for raw in entries.split(b"\0"):
        if not raw:
            continue
        metadata, separator, name = raw.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[0] not in {b"100644", b"100755"}:
            raise ValueError("linked or unsupported committed source path refused")
        try:
            decoded = name.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("committed source path is not UTF-8") from error
        path = PurePosixPath(decoded)
        if ("\\" in decoded or path.is_absolute()
                or any(part in {"", ".", ".."} or part.lower() in FORBIDDEN
                       for part in path.parts)):
            raise ValueError("private or escaping committed source path refused")


def validate_destination(destination: Path, repository: Path) -> Path:
    if (not destination.is_absolute() or destination == Path(destination.anchor)
            or ".." in destination.parts):
        raise ValueError("destination must be an explicit absolute directory")
    cursor = destination
    while cursor != cursor.parent:
        if cursor.exists() and (cursor.is_symlink()
                or (hasattr(cursor, "is_junction") and cursor.is_junction())):
            raise ValueError("linked destinations are unsupported")
        cursor = cursor.parent
    resolved = destination.resolve()
    if resolved == repository or repository.is_relative_to(resolved):
        raise ValueError("destination cannot contain the delivering repository")
    return resolved


def prepare(destination: Path, config: Path, expected_revision: str | None = None) -> dict:
    repository, actual_revision = delivery(expected_revision)
    destination = validate_destination(destination, repository)
    includes = configured_includes(config)
    committed_files(repository, actual_revision, includes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise ValueError("linked destination parents are unsupported")
    if destination.exists() and not owned_destination(destination):
        raise ValueError("destination exists and is not an owned runtime resource")

    staging = Path(tempfile.mkdtemp(prefix=".factory-resource-", dir=destination.parent))
    archive = staging.with_suffix(".zip")
    backup = None
    try:
        result = subprocess.run(["git", "archive", "--format=zip", "--output", str(archive),
                                 actual_revision, "--", *includes], cwd=repository, timeout=120)
        if result.returncode:
            raise ValueError("Git could not materialize the delivering revision")
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                target = (staging / info.filename).resolve()
                if not target.is_relative_to(staging):
                    raise ValueError("Git archive contains an escaping path")
            bundle.extractall(staging)
        mapping = {
            "version": 1,
            "kind": "factory-runtime-resource",
            "source_revision": actual_revision,
            "source_tree": checked(["git", "rev-parse", f"{actual_revision}^{{tree}}"], repository),
            "resource_root": str(destination),
            "resource_digest": resource_digest(staging),
            "include": includes,
        }
        (staging / MARKER).write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            backup = Path(tempfile.mkdtemp(
                prefix=".factory-resource-backup-", dir=destination.parent
            ))
            backup.rmdir()
            destination.replace(backup)
        try:
            staging.replace(destination)
        except BaseException:
            if backup is not None and backup.exists() and not destination.exists():
                backup.replace(destination)
            raise
        if backup is not None:
            remove_tree(backup)
            backup = None
        return mapping
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            remove_tree(staging)
        if backup is not None and backup.exists() and destination.exists():
            remove_tree(backup)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    node = sub.add_parser("prepare")
    node.add_argument("--destination", required=True, type=Path)
    node.add_argument("--config", required=True, type=Path)
    node.add_argument("--expected-revision")
    start = sub.add_parser("start", help="start a bound root as this delivering revision")
    start.add_argument("--slot", required=True)
    start.add_argument("--root", required=True)
    start.add_argument("--connection-file", type=Path)
    start.add_argument("--mutation")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            print(json.dumps(prepare(args.destination, args.config, args.expected_revision)))
            return 0
        repository, revision = delivery()
        command = [sys.executable, str(repository / "factory/runtime_host.py"), "start",
                   "--slot", args.slot, "--root", args.root,
                   "--expected-revision", revision]
        if args.connection_file:
            command.extend(["--connection-file", str(args.connection_file)])
        if args.mutation:
            command.extend(["--mutation", args.mutation])
        return subprocess.run(command, cwd=repository).returncode
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("Runtime resource operation refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
