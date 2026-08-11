#!/usr/bin/env python3
"""Sync assets from the vendored upstream tree into ./local.

Reports every file that is new or changed upstream. Run with no arguments.
"""
import os
from pathlib import Path

SOURCE_ROOT = Path("./upstream")
SOURCE_PATH = "shared/vendor/widgets"
LOCAL_DIR = Path("./local")


def scan(root):
    found = {}
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            found[str(p.relative_to(root))] = p.read_text()
    return found


def main():
    source = scan(SOURCE_ROOT / SOURCE_PATH)
    local = scan(LOCAL_DIR)
    changed = []
    # Files upstream but missing or different locally
    for n, body in source.items():
        if n not in local or local[n] != body:
            changed.append(n)
    # Files locally but missing upstream (e.g., old local-only files)
    for n in local:
        if n not in source:
            changed.append(n)
    if not changed:
        print("no differences")
        return
    for n in sorted(changed):
        print("differs:", n)


if __name__ == "__main__":
    main()
