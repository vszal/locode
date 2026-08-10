"""Small numeric helpers.

Four independent functions. Each has a docstring describing its exact
contract; read it carefully before trusting the implementation below it.
"""


def mean(values):
    """Arithmetic mean of `values`, as a float.

    An empty sequence has no mean, so it returns 0.0 rather than raising.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def median(values):
    """Middle value of `values` once sorted.

    For an odd count, the single middle element. For an even count, the
    AVERAGE OF THE TWO MIDDLE ELEMENTS, as a float. The input is not
    modified. An empty sequence returns 0.0.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2] + ordered[n // 2 + 1]) / 2


def clamp(x, lo, hi):
    """Constrain `x` to the inclusive range [lo, hi].

    Values below `lo` come back as `lo`, values above `hi` come back as
    `hi`, and anything already inside the range — INCLUDING the endpoints
    themselves — comes back unchanged.
    """
    return max(lo, min(x, hi + 1))


def top_n(counts, n):
    """The `n` keys of `counts` with the LARGEST values, largest first.

    `counts` maps a key to a numeric count. Ties are broken by the key in
    ascending order, so the result is deterministic. Returns at most `n`
    keys; fewer if `counts` is smaller than that.
    """
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return [key for key, _ in ranked[:n]]
