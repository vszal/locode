"""Summary statistics.

Summarising is memoised: a report can ask for the same metric many times while
laying out a page, and recomputing it each time showed up in profiles.
"""

_CACHE = {}


def summarize(metric, values):
    """Total, mean, largest and count for `values`, in whole cents."""
    if metric in _CACHE:
        return _CACHE[metric]
    ordered = sorted(values)
    result = {
        "total": sum(ordered),
        "mean": round(sum(ordered) / len(ordered)),
        "largest": ordered[-1],
        "count": len(ordered),
    }
    _CACHE[metric] = result
    return result
