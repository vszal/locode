#!/usr/bin/env python3
"""
Sync script to copy changes from the upstream skills repo to the local checkout (one-way).

This script:
1. Clones the upstream repository into a temporary directory
2. Identifies changes (new, modified, deleted files) in the source
3. Stages changes to the _staged-diffs directory for review
4. Optionally applies staged changes to the local ./toolkit directory
5. Cleans up the temporary clone

The local ./toolkit directory is assumed to be checked out from the
downstream repository.

Usage:
    python syncdirs.py              # Stage changes for review
    python syncdirs.py --apply      # Stage and apply changes immediately

Author: locode
Date: 2024
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

# Repository URLs
SOURCE_REPO = "https://github.com/example-org/skills.git"
SOURCE_PATH = "skills/toolkit"
DEST_REPO = "https://github.com/example-user/skills.git"
DEST_PATH = "toolkit"

# Local working directory (where the downstream repo is checked out)
LOCAL_DIR = Path("./toolkit")

# Where staged changes are written for review before they are applied.
STAGING_DIR = Path("./_staged-diffs")


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    """Run a shell command and return its output."""
    env = dict(os.environ)
    # Never block on a credential prompt in a non-interactive run.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=check, env=env
    )
    return result.stdout


def clone_source(tmpdir: str) -> Path:
    """Clone the upstream repository and return the path to the synced subtree."""
    run_cmd(["git", "clone", "--depth", "1", SOURCE_REPO, tmpdir])
    return Path(tmpdir) / SOURCE_PATH


def _file_hash(path: Path) -> str:
    """Return the sha256 hex digest of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(root: str) -> set[str]:
    """Walk root and return the set of file paths relative to root.

    Paths are returned as posix-style strings (forward slashes) so callers
    can compare files across directories consistently regardless of
    platform.
    """
    root_path = Path(root)
    found: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for filename in filenames:
            full_path = Path(dirpath) / filename
            rel_path = full_path.relative_to(root_path)
            found.add(rel_path.as_posix())
    return found


def compare_dirs(
    source: str, dest: str
) -> tuple[list[str], list[str], list[str]]:
    """Compare a source directory against a destination directory.

    Returns a tuple of (new_files, modified_files, deleted_files), each a
    list of paths relative to the two directory roots. "new" files exist
    in source but not dest, "deleted" files exist in dest but not source,
    and "modified" files exist in both but differ in content.
    """
    source_root = Path(source).resolve()
    dest_root = Path(dest)

    source_files: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(source_root):
        for filename in filenames:
            full_path = Path(dirpath) / filename
            rel_path = full_path.relative_to(source_root)
            # only include files under the source root
            if rel_path.is_relative_to(source_root):
                source_files.add(rel_path.as_posix())

    dest_files = collect_files(str(dest_root))

    new_files = sorted(source_files - dest_files)
    deleted_files = sorted(dest_files - source_files)

    modified_files = []
    for rel in sorted(source_files & dest_files):
        source_hash = _file_hash(source_root / rel)
        dest_hash = _file_hash(dest_root / rel)
        if source_hash != dest_hash:
            modified_files.append(rel)

    return new_files, modified_files, deleted_files


def stage_changes(
    source: str,
    staging_dir: str,
    changed_files: Iterable[str],
) -> None:
    """Copy the given relative paths from source into staging_dir."""
    source_root = Path(source)
    staging_root = Path(staging_dir)
    for rel in changed_files:
        src_path = source_root / rel
        dst_path = staging_root / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)


def apply_changes(
    staging_dir: str,
    dest: str,
    new_files: Iterable[str],
    modified_files: Iterable[str],
    deleted_files: Iterable[str],
) -> None:
    """Apply staged new/modified files and remove deleted files in dest."""
    staging_root = Path(staging_dir)
    dest_root = Path(dest)

    for rel in list(new_files) + list(modified_files):
        src_path = staging_root / rel
        dst_path = dest_root / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)

    for rel in deleted_files:
        dst_path = dest_root / rel
        if dst_path.exists():
            dst_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync files one way from the upstream skills repo into "
        "the local checkout."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the staged changes. Without this flag, only a "
        "dry-run summary is printed.",
    )
    parser.add_argument(
        "--staging-dir",
        default=None,
        help="Directory to stage changes into. Defaults to a fresh "
        "temporary directory.",
    )
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="syncdirs-clone-")
    try:
        source_path = clone_source(tmpdir)

        new_files, modified_files, deleted_files = compare_dirs(
            str(source_path), str(LOCAL_DIR)
        )

        print(f"new:      {len(new_files)}")
        print(f"modified: {len(modified_files)}")
        print(f"deleted:  {len(deleted_files)}")

        if not args.apply:
            print("Dry run only; pass --apply to write these changes.")
            return

        staging_dir = args.staging_dir or tempfile.mkdtemp(prefix="syncdirs-")
        created_temp = args.staging_dir is None
        try:
            stage_changes(
                str(source_path), staging_dir, new_files + modified_files
            )
            apply_changes(
                staging_dir, str(LOCAL_DIR), new_files, modified_files,
                deleted_files,
            )
        finally:
            if created_temp:
                shutil.rmtree(staging_dir, ignore_errors=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
