"""Preloading, so a later lookup is cheap."""

import store


def warm(keys):
    """Preload `keys`, returning a mapping of only the ones that exist."""
    out = {}
    for key in keys:
        value = store.get(key)
        if value is not None:
            out[key] = value
    return out
