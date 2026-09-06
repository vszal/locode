"""Print a per-region sales summary."""

import csv
import pathlib

import metrics
import render

DATA = pathlib.Path("data/sales.csv")


def load(path):
    with path.open(newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def main():
    rows = load(DATA)
    regions = sorted({row["region"] for row in rows})
    print("Revenue by region")
    print("-" * 52)
    for region in regions:
        values = [int(row["revenue_cents"]) for row in rows
                  if row["region"] == region]
        summary = metrics.summarize("revenue", values)
        print(render.line(region, summary))


if __name__ == "__main__":
    main()
