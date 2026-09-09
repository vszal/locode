"""Flatten every archived eval sweep to one CSV row per (run, check).

Pure extraction: no grading, no interpretation. `percheck.py` and
`checkdeps.py` re-derive scores and look for structure; this just pools the
raw booleans so they can be analysed elsewhere. Sweep shape varies across the
archive (list vs dict-with-"runs", missing keys, absent "checks") and this
script is the part that absorbs that variance uniformly.

Two things to know before trusting a dump. A sweep still running is already on
disk and partial -- the harness persists after each run -- so a dump taken mid
sweep includes runs that have not happened yet as if that sweep were finished.
And the scan counters (`runs scanned`, `no_checks`) describe everything walked,
while `--case` and `--model` filter only the rows written; that is deliberate,
so the summary always says how much archive was covered.

    python evals/dumpchecks.py [root] [--out PATH] [--case NAME]... [--model NAME]...
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "evals" / "results"

COLUMNS = [
    "sweep", "label", "case", "model", "track", "repeat", "arm", "score",
    "check", "value", "iterations", "wallclock", "nudges", "stop_reason",
    "clean_finish",
]


def _find_runs(data):
    """Return `(label, runs)` for a parsed results.json, or `(None, None)` if
    no run list can be found."""
    if isinstance(data, list):
        return "", data
    if isinstance(data, dict):
        label = data.get("label") or ""
        runs = data.get("runs")
        if isinstance(runs, list):
            return label, runs
        for v in data.values():
            if isinstance(v, list) and v and all(
                isinstance(r, dict) and "case" in r for r in v
            ):
                return label, v
    return None, None


def _render(v):
    """Render a value for a CSV cell: booleans lowercase, missing -> "" ."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def dump(root, cases=None, models=None):
    """Walk `root` for sweep results and return `(rows, stats)`.

    `rows` is a list of dicts matching COLUMNS. `stats` has keys: sweeps_read,
    sweeps_unparsed, runs_seen, runs_no_checks, rows_written.
    """
    cases = set(cases) if cases else None
    models = set(models) if models else None
    stats = {
        "sweeps_read": 0,
        "sweeps_unparsed": 0,
        "runs_seen": 0,
        "runs_no_checks": 0,
        "rows_written": 0,
    }
    rows = []
    for f in sorted(Path(root).glob("**/results.json")):
        sweep = f.parent.name
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            stats["sweeps_unparsed"] += 1
            continue
        label, runs = _find_runs(data)
        if runs is None:
            stats["sweeps_unparsed"] += 1
            continue
        stats["sweeps_read"] += 1
        for r in runs:
            if not isinstance(r, dict):
                continue
            stats["runs_seen"] += 1
            checks = r.get("checks")
            if not checks:
                stats["runs_no_checks"] += 1
                continue
            case = r.get("case")
            model = r.get("model")
            if cases is not None and case not in cases:
                continue
            if models is not None and model not in models:
                continue
            metrics = r.get("metrics") or {}
            base = {
                "sweep": sweep,
                "label": label,
                "case": _render(case),
                "model": _render(model),
                "track": _render(r.get("track")),
                "repeat": _render(r.get("repeat")),
                "arm": _render(r.get("arm")),
                "score": _render(r.get("score")),
                "iterations": _render(metrics.get("iterations")),
                "wallclock": _render(metrics.get("wallclock")),
                "nudges": _render(metrics.get("nudges")),
                "stop_reason": _render(metrics.get("stop_reason")),
                "clean_finish": _render(metrics.get("clean_finish")),
            }
            for check_name, value in checks.items():
                row = dict(base)
                row["check"] = check_name
                row["value"] = _render(value)
                rows.append(row)
                stats["rows_written"] += 1
    return rows, stats


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=str(DEFAULT_ROOT),
                    help="directory to walk for **/results.json (default: evals/results)")
    ap.add_argument("--out", default=None, help="write CSV here instead of stdout")
    ap.add_argument("--case", action="append", default=[], help="filter to this case id (repeatable)")
    ap.add_argument("--model", action="append", default=[], help="filter to this model (repeatable)")
    args = ap.parse_args(argv)

    rows, stats = dump(args.root, cases=args.case, models=args.model)

    out = open(args.out, "w", newline="") if args.out else sys.stdout
    try:
        w = csv.DictWriter(out, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    finally:
        if args.out:
            out.close()

    print(
        f"sweeps read={stats['sweeps_read']} unparsed={stats['sweeps_unparsed']} "
        f"runs scanned={stats['runs_seen']} no_checks={stats['runs_no_checks']} "
        f"rows written={stats['rows_written']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
