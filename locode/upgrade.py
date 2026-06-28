"""Build the commands that update an installed locode, per its install method.

`locode upgrade` reads the install-method marker (see install.py) and runs the
argv(s) this returns. Kept as a pure function so it's trivially testable; the
CLI layer is what actually shells out.
"""

from __future__ import annotations

import sys

from locode.config import DATA_DIR


def upgrade_argv(method: str, detail: str = "", pre: bool = False) -> list[list[str]]:
    """Return the command(s) (each an argv list) to upgrade locode, in order.

    Raises ValueError on an unknown method. `pre` opts the PyPI-backed methods
    into pre-releases; it has no effect on the git checkout path.
    """
    if method == "pipx":
        cmd = ["pipx", "upgrade", "locode"]
        if pre:
            cmd.append("--pre")
        return [cmd]
    if method == "uv":
        cmd = ["uv", "tool", "upgrade", "locode"]
        if pre:
            cmd.append("--pre")
        return [cmd]
    if method == "venv":
        pip_path = str(DATA_DIR / "venv" / "bin" / "pip")
        cmd = [pip_path, "install", "-U", "locode"]
        if pre:
            cmd.append("--pre")
        return [cmd]
    if method == "git":
        pull = ["git", "-C", detail, "pull"]
        resync = [sys.executable, "-m", "pip", "install", "-e", detail]
        return [pull, resync]
    raise ValueError(f"unknown install method: {method!r}")
