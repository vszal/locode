"""A tiny in-memory key-value store."""

_DATA = {"alpha": 1, "beta": 2, "gamma": 3}


def get(key):
    """The value stored for `key`, or None when the key is not present."""
    return _DATA.get(key)


def keys():
    """Every key currently in the store, sorted."""
    return sorted(_DATA)
