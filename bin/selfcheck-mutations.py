#!/usr/bin/env python3
"""Prove focused consumer tests catch deterministic guard mutations. No agents."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DEFECTS = [
    ("missing workflow allowed", "if name not in discover(settings, source):", "if False:",
     "ConsumerTests.test_missing_shared_workflow_fails_closed"),
    ("source SHA unchecked", "if actual != revision:", "if False:",
     "ConsumerTests.test_sha_dirty_and_ignored_authoring_drift_refused"),
    ("legacy gate succeeds", "    return 2\n\n\ndef invoke", "    return 0\n\n\ndef invoke",
     "ConsumerTests.test_legacy_dial_and_receipts_cannot_merge"),
]

def main():
    for name, anchor, replacement, test in DEFECTS:
        with tempfile.TemporaryDirectory(prefix="consumer selfcheck ") as td:
            root = Path(td)
            for folder in ("bin", "template"):
                shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__"))
            target = root / "template/factory/consumer.py"
            text = target.read_text(encoding="utf-8")
            if text.count(anchor) != 1:
                print(f"NOT_INJECTED {name}")
                return 1
            target.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
            result = subprocess.run([sys.executable, "bin/test_consumer.py", test], cwd=root,
                                    capture_output=True, text=True, timeout=120)
            if result.returncode == 0 or "FAIL:" not in result.stderr:
                print(f"NOT_CAUGHT {name}: {result.stdout} {result.stderr}")
                return 1
            print(f"CAUGHT {name}")
    print(f"SELFCHECK_MUTATIONS_OK caught={len(DEFECTS)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
