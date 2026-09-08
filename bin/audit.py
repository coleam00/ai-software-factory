#!/usr/bin/env python3
"""Audit shipped consumer ownership. Use factory doctor for installed source readiness."""
import argparse
import ast
import sys
from pathlib import Path

def audit(root):
    errors = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as error:
            errors.append(str(error))
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".yaml", ".yml"} or path.name == "SKILL.md":
            errors.append(f"Factory must not ship workflows or prompt skills: {path}")
    for name in ("harness/agentcheck.py", "harness/mutations/run.py"):
        text = (root / name).read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if any(m.split(".")[0] in {"subprocess", "consumer", "sdlc", "anthropic", "openai"} for m in modules):
                    errors.append(f"Execution dependency in data-only helper: {name}")
    for error in errors:
        print(error)
    print(f"CONSUMER_AUDIT errors={len(errors)}; live source/provenance requires factory doctor")
    return int(bool(errors))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent / "template")
    raise SystemExit(audit(parser.parse_args().repo))
