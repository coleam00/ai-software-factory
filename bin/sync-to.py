#!/usr/bin/env python3
"""Upgrade consumer files, retaining user data and backing up replaced machinery."""
import argparse
from pathlib import Path
from install import sync

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sync(args.destination, args.dry_run)
