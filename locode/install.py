"""Record how locode was installed, so `locode upgrade` can pick the right updater.

`install.sh` (and the dev editable install) write a one-line marker in the XDG
data dir naming the install method — pipx / uv / venv / git — and, for git, the
checkout path. Reads are tolerant: a missing or malformed marker yields None.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from locode.config import DATA_DIR

VALID_METHODS = ("pipx", "uv", "venv", "git")

MARKER_PATH = DATA_DIR / "install-method"


@dataclass(frozen=True)
class InstallMethod:
    method: str          # one of VALID_METHODS
    detail: str = ""     # for "git": the checkout path; "" for the others


def write_method(method: str, detail: str = "", path: Path | None = None) -> None:
    """Record the install method. Raises ValueError on an unknown method."""
    if method not in VALID_METHODS:
        raise ValueError(f"unknown install method: {method!r}")
    target = path or MARKER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{method}\t{detail}\n")


def read_method(path: Path | None = None) -> InstallMethod | None:
    """Read the marker, or None if it's missing, blank, or unparseable."""
    target = path or MARKER_PATH
    try:
        text = target.read_text()
    except OSError:
        return None
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return None
    method, _, detail = line.partition("\t")
    if method not in VALID_METHODS:
        return None
    return InstallMethod(method, detail)
