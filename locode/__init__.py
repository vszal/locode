"""locode — a Claude Code-style agentic CLI for local LLMs (mlx_lm.server)."""

from importlib.metadata import PackageNotFoundError, version as _version

try:  # single-source the version from package metadata (pyproject is the source)
    __version__ = _version("locode")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
