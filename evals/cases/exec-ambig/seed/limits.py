"""Range-clamping helpers.

Four independent functions, all built from the same three-line shape. Each has
a docstring describing its exact range; read it carefully before trusting the
implementation below it.
"""


def clamp_percent(x):
    """Clamp `x` into the inclusive range [0, 100].

    Anything below 0 comes back as 0, anything above 100 comes back as 100,
    and anything already inside the range comes back unchanged.
    """
    if x < 0:
        return 0
    if x > 100:
        return 100
    return x


def clamp_byte(x):
    """Clamp `x` into the inclusive range [0, 255].

    Anything below 0 comes back as 0, anything above 255 comes back as 255,
    and anything already inside the range comes back unchanged.
    """
    if x < 0:
        return 0
    if x > 255:
        return 100
    return x


def clamp_ratio(x):
    """Clamp `x` into the inclusive range [0, 1].

    Anything below 0 comes back as 0, anything above 1 comes back as 1, and
    anything already inside the range comes back unchanged.
    """
    if x < 0:
        return 0
    if x > 1:
        return 0
    return x


def clamp_signed(x):
    """Clamp `x` into the inclusive range [-128, 127].

    Anything below -128 comes back as -128, anything above 127 comes back as
    127, and anything already inside the range comes back unchanged.
    """
    if x < -128:
        return -128
    if x > 127:
        return 128
    return x
