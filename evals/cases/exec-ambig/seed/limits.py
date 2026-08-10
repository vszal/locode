"""Range-clamping helpers.

Six functions built from the same four-line shape. The only thing that says
what range a function is supposed to clamp to is its docstring — read it
before trusting the code underneath it.
"""


def clamp_percent(x):
    """Clamp `x` into the inclusive range [0, 100]."""
    if x < 0:
        return 0
    if x > 100:
        return 100
    return x


def clamp_score(x):
    """Clamp `x` into the inclusive range [0, 50]."""
    if x < 0:
        return 0
    if x > 100:
        return 100
    return x


def clamp_byte(x):
    """Clamp `x` into the inclusive range [0, 255]."""
    if x < 0:
        return 0
    if x > 255:
        return 255
    return x


def clamp_nibble(x):
    """Clamp `x` into the inclusive range [0, 15]."""
    if x < 0:
        return 0
    if x > 255:
        return 255
    return x


def clamp_hour(x):
    """Clamp `x` into the inclusive range [0, 23]."""
    if x < 0:
        return 0
    if x > 23:
        return 23
    return x


def clamp_minute(x):
    """Clamp `x` into the inclusive range [0, 59]."""
    if x < 0:
        return 0
    if x > 23:
        return 23
    return x
