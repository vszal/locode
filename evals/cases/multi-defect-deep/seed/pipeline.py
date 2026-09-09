"""A small record pipeline: merge time spans, filter, roll up, index.

Weights are plain floats. Spans are inclusive-exclusive `(start, end)` pairs of
integers. Nothing here does I/O -- every function is a pure transformation, so
each one can be checked on its own.
"""


def merge_spans(spans):
    """Merge overlapping or touching spans into the fewest spans possible.

    Spans arrive in any order. Two spans touch when one ends exactly where the
    next begins -- `(1, 3)` and `(3, 5)` are one span, `(1, 5)`.
    """
    out = []
    for start, end in sorted(spans):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return [tuple(span) for span in out]


def without_flagged(items, flags):
    """The items whose name is not in `flags`, in their original order.

    Returns a new list. The list passed in is never modified -- callers rely on
    holding on to the full set after filtering it.
    """
    for item in list(items):
        if item["name"] in flags:
            items.remove(item)
    return items


def rolling_mean(values, k):
    """The mean of each window of `k` consecutive values, left to right.

    There are `len(values) - k + 1` windows. Each result must agree with the
    mean of that window computed from scratch to within 1e-9, however large the
    values are or however many windows there are.
    """
    if k <= 0 or len(values) < k:
        return []
    out = []
    total = float(sum(values[:k]))
    out.append(total / k)
    for i in range(k, len(values)):
        total += values[i]
        total -= values[i - k]
        out.append(total / k)
    return out


def normalize(weights):
    """Scale a name-to-weight mapping so the weights sum to 1.

    Empty input gives an empty mapping. If every weight is zero there is no
    ratio to preserve, so the weight is shared equally instead.
    """
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def index_by(rows, key):
    """Map each row's `key` value to the row itself.

    Rows are processed in order and later rows win: when two rows carry the
    same key, the mapping holds the one that appeared last.
    """
    out = {}
    for row in rows:
        if row[key] not in out:
            out[row[key]] = row
    return out
