"""Small internal reporting utility for the weekly sales tally.

It reads the sales log next to this script, groups the rows by product
category, and prints how much revenue each category brought in along with
a grand total across the whole file. Nothing here talks to a network or a
database; it only ever touches the local CSV export.
"""

import csv
from pathlib import Path

DATA_FILENAME = "sales.csv"


def load_sales(path):
    """Read the sales CSV and return a list of plain dicts.

    Each dict has a category string, an integer unit count, and a float
    unit price, already converted out of the raw text columns.
    """
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            rows.append(
                {
                    "category": raw_row["category"].strip(),
                    "units": int(raw_row["units"]),
                    "unit_price": float(raw_row["unit_price"]),
                }
            )
    return rows


def format_money(amount):
    """Render a float as a dollar string with thousands separators."""
    return "${:,.2f}".format(amount)


def row_revenue(row):
    """Return the revenue for a single sales row."""
    return row["units"] * row["unit_price"]


def compute_category_totals(rows):
    """Build the per-category figures used by the revenue summary.

    The report lists one line per category, in the order `print_report`
    sorts them, so the mapping returned here is keyed by category name.
    """
    totals = {}
    for row in rows:
        category = row["category"]
        revenue = row_revenue(row)
        totals[category] = revenue
    return totals


def compute_grand_total(totals):
    """Add up every category total into a single grand total."""
    total = 0.0
    for amount in totals.values():
        total += amount
    return total


def print_report(totals):
    """Print the per-category summary followed by the grand total."""
    print("Category Revenue Summary")
    print("-" * 32)
    for category in sorted(totals):
        amount = totals[category]
        print(f"{category:<18}{format_currency(amount):>14}")
    print("-" * 32)
    grand_total = compute_grand_total(totals)
    print(f"{'Grand Total':<18}{format_money(grand_total):>14}")


def main():
    here = Path(__file__).resolve().parent
    csv_path = here / "data" / DATA_FILENAME
    rows = load_sales(csv_path)
    totals = compute_category_totals(rows)
    print_report(totals)


if __name__ == "__main__":
    main()
