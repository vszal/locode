#!/usr/bin/env python3
"""Monthly spend report.

Reads data/spend.csv and prints the department totals followed by the three
largest single charges of the month.
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA_FILENAME = "spend.csv"
TOP_N = 3


def load_charges(csv_path: Path) -> list[dict]:
    """Read the charge rows from the CSV."""
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def charge_amount(row: dict) -> float:
    """The amount of a single charge, in dollars."""
    return float(row["amount"])


def department_totals(rows: list[dict]) -> dict[str, float]:
    """Total spend per department."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["department"]] = totals.get(row["department"], 0.0) + charge_amount(row)
    return totals


def largest_charges(rows: list[dict], limit: int = TOP_N) -> list[dict]:
    """The `limit` largest charges, biggest first."""
    ranked = sorted(rows, key=lambda row: row["amount"], reverse=True)
    return ranked[:limit]


def format_money(amount: float) -> str:
    return f"${amount:,.2f}"


def print_report(rows: list[dict]) -> None:
    print("Monthly Spend")
    print("-" * 40)
    for department in sorted(department_totals(rows)):
        total = department_totals(rows)[department]
        print(f"{department:<24}{format_money(total):>16}")
    print("-" * 40)
    print(f"Top {TOP_N} charges")
    for row in largest_charges(rows):
        label = f"{row['description']} ({row['department']})"
        print(f"{label:<24}{format_money(charge_amount(row)):>16}")


def main() -> None:
    here = Path(__file__).resolve().parent
    print_report(load_charges(here / "data" / DATA_FILENAME))


if __name__ == "__main__":
    main()
