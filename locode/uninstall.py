"""Build a PLAN for removing an installed locode — it deletes nothing itself.

`locode uninstall` calls uninstall_plan() and then executes the plan behind a
confirmation prompt (see cli.py). Keeping the planning pure makes it testable
and keeps the only destructive code (rmtree) on the CLI side, gated by a yes/no.
"""

from __future__ import annotations

import sys
from pathlib import Path

from locode.config import CONFIG_DIR, DATA_DIR, STATE_DIR


def uninstall_plan(
    method: str, detail: str = "", purge: bool = False
) -> tuple[list[list[str]], list[Path]]:
    """Return (commands, paths): argv lists to run and Paths to delete.

    Raises ValueError on an unknown method. `purge` additionally schedules the
    config, state, and data dirs for removal, for every method.
    """
    commands: list[list[str]] = []
    paths: list[Path] = []

    if method == "pipx":
        commands.append(["pipx", "uninstall", "locode"])
    elif method == "uv":
        commands.append(["uv", "tool", "uninstall", "locode"])
    elif method == "venv":
        paths.append(DATA_DIR / "venv")
        paths.append(Path.home() / ".local" / "bin" / "locode")
    elif method == "git":
        commands.append([sys.executable, "-m", "pip", "uninstall", "-y", "locode"])
    else:
        raise ValueError(f"unknown install method: {method!r}")

    if purge:
        paths += [CONFIG_DIR, STATE_DIR, DATA_DIR]
    return commands, paths
