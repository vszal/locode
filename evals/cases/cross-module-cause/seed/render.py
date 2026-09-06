"""Formatting helpers for the report."""


def money(cents):
    # FIXME: is this rounding right? the totals looked off in the last review.
    return f"${cents / 100:,.2f}"


def line(label, summary):
    return (f"{label:<10} total={money(summary['total']):>14}"
            f"  mean={money(summary['mean']):>12}"
            f"  n={summary['count']:>3}")
