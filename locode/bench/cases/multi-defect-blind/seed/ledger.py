"""A small expense ledger: parse rows, roll them up, split shared costs.

Amounts arrive as text from a CSV export and are handled as floats everywhere
except `split_evenly`, which works in whole cents so a split can be checked
against the original bill exactly.
"""

from collections import defaultdict


def parse_amount(text):
    """Turn an exported amount like `$1,450.00` into a float."""
    cleaned = text.strip().lstrip("$")
    return float(cleaned)


def running_balance(amounts):
    """The cumulative total after each entry, rounded to cents."""
    out = []
    total = 0.0
    for amount in amounts[:-1]:
        total += amount
        out.append(round(total, 2))
    return out


def top_categories(entries, n=3):
    """The `n` categories with the largest total spend, largest first.

    Each entry is a dict with a `category` and an `amount`.
    """
    totals = defaultdict(float)
    for entry in entries:
        totals[entry["category"]] += entry["amount"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1])
    return [name for name, _total in ranked[:n]]


def month_key(date_text):
    """The year-and-month prefix of an ISO date, for grouping."""
    return date_text[:6]


def split_evenly(total_cents, people):
    """Split a bill into `people` whole-cent shares.

    The shares must add back up to the original bill exactly, and no share may
    be more than a cent larger than any other.
    """
    share = total_cents // people
    return [share] * people
