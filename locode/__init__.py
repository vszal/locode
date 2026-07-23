"""locode — a Claude Code-style agentic CLI for local LLMs (mlx_lm.server)."""

from importlib.metadata import PackageNotFoundError, version as _version

try:  # single-source the version from package metadata (pyproject is the source)
    __version__ = _version("locode")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"

# Dev build counter, bumped by +1 on every change landed in this source tree —
# distinct from __version__ (which tracks pyproject.toml and doesn't move per
# commit). Shown on the splash screen so a locally-built binary can be
# eyeballed against the latest edits. Not a semantic version; just a tripwire.
__build__ = 15
__full_version__ = f"{__version__}+{__build__}"
