"""Human-readable lines for a report."""

import store


def line(key):
    """A display line, `key=value` -- or `key=?` when the key is unknown."""
    value = store.get(key)
    if value is None:
        return f"{key}=?"
    return f"{key}={value}"
