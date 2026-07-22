"""Small statistics helpers. See PLAN.md for the tasks left to implement.

`mean` below is finished and serves as the reference example for style:
a docstring, a ValueError on empty input, and no imports.
"""


def mean(values):
    """Return the arithmetic mean of `values` as a float.

    `values` is a list of numbers (ints or floats). Raises ValueError if
    `values` is empty.
    """
    if not values:
        raise ValueError("values must not be empty")
    return sum(values) / len(values)


def median(values):
    """Return the median of `values` as a float. See PLAN.md task 1."""
    raise NotImplementedError


def mode(values):
    """Return the most common value in `values`. See PLAN.md task 2."""
    raise NotImplementedError


def summary(values):
    """Return a dict summarizing `values`. See PLAN.md task 3."""
    raise NotImplementedError
