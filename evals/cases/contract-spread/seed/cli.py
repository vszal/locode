"""The command-line front end."""

import store


def run(args):
    """Look up each requested key, skipping unknown ones silently.

    Returns `(count, lines)` where `lines` holds one `"key value"` string per
    key that existed, in the order requested.
    """
    lines = []
    for key in args:
        value = store.get(key)
        if value is not None:
            lines.append(f"{key} {value}")
    return len(lines), lines
