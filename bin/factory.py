#!/usr/bin/env python3
"""Install, configure and invoke the shared Archon SDLC pack."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path
from install import sync, install_source, configure
import consumer


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] != "init":
        return consumer.main(args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("init")
    parser.add_argument("--source", help="Complete Archon checkout or clone URL")
    parser.add_argument("--revision", help="Exact integration commit SHA")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/factory/archon")
    parser.add_argument("--bun", default="bun", help="Bun executable, not a provider or Archon override")
    parser.add_argument("--yes", action="store_true", help="Compatibility option; init has no prompts")
    parser.add_argument("--scaffold-only", action="store_true", help="Install files without claiming engine readiness")
    options = parser.parse_args(args)
    try:
        root = consumer.project_root()
        sync(root)
        if options.scaffold_only:
            print("Scaffold installed. Integration source is not configured or verified.")
            return 0
        revision = options.revision or consumer.MANIFEST["integration_revision_required"]
        if revision:
            settings = install_source(options.source or consumer.MANIFEST["repository"],
                                      revision, options.cache, options.bun)
            configure(root, settings)
        else:
            if options.source:
                raise ValueError("--source requires --revision; moving source refs are not pins")
            consumer.doctor(consumer.read_settings(root))
        print("Consumer installed and source validated. Shared gates own decisions. Live integration remains required.")
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"Factory installation incomplete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
