"""Operator-owned files, outside every Git checkout."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import config


def root() -> Path:
    key = hashlib.sha256(str(config.SHARED.resolve()).encode()).hexdigest()[:20]
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state")
    path = (base / "software-factory" / key).resolve()
    path.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, timeout=30)
    if probe.returncode == 0:
        raise RuntimeError(f"Operator runtime must be outside Git: {path}")
    return path


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value
