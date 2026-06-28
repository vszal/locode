"""Alias resolution: short model alias -> full Hugging Face model id.

locode ships **no** built-in model table — aliases live in the user's
`config.toml` `[aliases]` block (scaffolded on first run; see `scaffold.py`) and
reach the resolver via `SingleGpuManager`'s config overrides. This module is
just the resolution rule: a value containing "/" is already a full id and passes
through unchanged; a bare name is looked up in `ALIASES` (empty by default, but
kept as an extension point) and otherwise raises.

Prefer `SingleGpuManager.resolve` / `.known_aliases`, which fold in the user's
configured aliases; the bare functions here only see the built-in table.
"""

from __future__ import annotations

# Intentionally empty: no personal/default model roster is shipped in code. The
# user's models come from config.toml [aliases] (merged in by the manager).
ALIASES: dict[str, str] = {}


def resolve(name: str) -> str:
    """Resolve an alias (or full id) to a full Hugging Face model id.

    A value containing "/" is already a full id and returned unchanged.
    Raises KeyError naming the unknown alias (and the known set) otherwise.
    """
    if "/" in name:
        return name
    try:
        return ALIASES[name]
    except KeyError:
        known = ", ".join(sorted(ALIASES)) or "(none built-in — define in config.toml)"
        raise KeyError(f"unknown model alias {name!r}; known: {known}") from None


def known_aliases() -> list[str]:
    """Built-in alias names (empty by default; the manager adds config aliases)."""
    return list(ALIASES)
