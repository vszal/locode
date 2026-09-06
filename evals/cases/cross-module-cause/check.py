"""Checks for cross-module-cause.

Why this case exists (ROADMAP 5.140): `repro-only` established that a defect
which is invisible by reading and unmissable when run separates models that
reproduce from models that guess -- and by 2026-09-06 the default model was
passing it 20/20. This case keeps that property and adds the two axes
`repro-only` does not test: DEPTH and a DECOY.

Depth: the symptom is in `app.py` (every region prints the same figures), but
`app.py` slices the rows per region correctly and `render.py` formats whatever
it is handed. The cause is a module further on -- `metrics.summarize` memoises
on the metric NAME and ignores the data, so the first region's numbers are
served to all of them. Reaching it means following the value, not reading the
file that was named.

The decoy: `render.money` carries a `FIXME: is this rounding right?` comment on
a line that is correct. It is the most suspicious-looking thing near the
symptom, and it is not the bug. `formatting_still_correct` grades BEHAVIOUR
rather than whether the line was touched, so rewriting it is fine and breaking
it is not -- the case punishes acting on suspicion, not curiosity about it.

`cache_correct_across_many` is what separates a real fix from a plausible
half-fix. Keying the cache on the length of `values` makes the seed's three
regions look right (3, 2 and 4 rows) while still being wrong, so the probe
includes two datasets of equal length and different contents.

The probe tolerates a changed signature: dropping the now-meaningless `metric`
argument is a legitimate fix, and grading the call shape instead of the
behaviour would fail a correct answer.
"""

import json
import shutil

PROBE_NAME = "_check_metrics.py"

# (label, values, total, mean, largest, count). A and D have the same length
# and different contents, so a cache keyed on len(values) fails the sweep.
DATASETS = [
    ("A", [100, 200, 300], 600, 200, 300, 3),
    ("B", [7, 7, 7, 7, 7, 8], 43, 7, 8, 6),
    ("C", [5000], 5000, 5000, 5000, 1),
    ("D", [1, 2, 3], 6, 2, 3, 3),
    ("E", [900, 100], 1000, 500, 900, 2),
]

# region -> (formatted total, formatted mean). From the seed CSV; the bug makes
# every line show north's pair.
REGION_TOTALS = {"north": "$4,700.00", "south": "$9,200.00", "west": "$1,500.00"}

MONEY = [(0, "$0.00"), (5, "$0.05"), (470000, "$4,700.00"),
         (123456789, "$1,234,567.89")]

PROBE = '''\
import json, sys
sys.path.insert(0, ".")

out = {}

def probe(name, fn):
    try:
        out[name] = bool(fn())
    except Exception:
        out[name] = False

try:
    import metrics, render
except Exception:
    print("PROBE" + json.dumps({}))
    raise SystemExit(0)

DATASETS = %(datasets)s
MONEY = %(money)s

def call(values):
    """Summarise `values`, whatever `summarize` now takes."""
    try:
        return metrics.summarize("revenue", values)
    except TypeError:
        return metrics.summarize(values)

def ok(summary, total, mean, largest, count):
    return (int(summary["total"]) == total
            and int(summary["mean"]) == mean
            and int(summary["largest"]) == largest
            and int(summary["count"]) == count)

def _two():
    a = call(DATASETS[0][1])
    b = call(DATASETS[1][1])
    return ok(a, *DATASETS[0][2:]) and ok(b, *DATASETS[1][2:])

def _many():
    for _label, values, total, mean, largest, count in DATASETS:
        if not ok(call(values), total, mean, largest, count):
            return False
    return True

def _repeat():
    values = DATASETS[2][1]
    first, second = call(values), call(values)
    return (ok(first, *DATASETS[2][2:]) and ok(second, *DATASETS[2][2:])
            and dict(first) == dict(second))

def _money():
    return all(render.money(cents) == want for cents, want in MONEY)

probe("fixed_stale_cache", _two)
probe("cache_correct_across_many", _many)
probe("repeat_call_stable", _repeat)
probe("formatting_still_correct", _money)
print("PROBE" + json.dumps(out))
''' % {"datasets": repr([list(d) for d in DATASETS]), "money": repr(MONEY)}

BEHAVIOURS = ["fixed_stale_cache", "cache_correct_across_many",
              "repeat_call_stable", "formatting_still_correct"]


def check(ctx):
    got = _probe(ctx)
    results = {name: bool(got.get(name)) for name in BEHAVIOURS}

    proc = ctx.bash("python3 app.py 2>&1", timeout=60)
    out = proc.stdout + proc.stderr
    results["runs_clean"] = proc.returncode == 0 and "Traceback" not in out

    # The report itself, not just the function behind it.
    body = [ln for ln in out.splitlines() if ln.strip()
            and any(r in ln for r in REGION_TOTALS)]
    results["report_shows_each_region"] = all(
        any(region in ln and total in ln for ln in body)
        for region, total in REGION_TOTALS.items())
    # The literal complaint in the prompt: the lines were identical.
    figures = [ln.split(None, 1)[1] if " " in ln else ln for ln in body]
    results["report_lines_differ"] = len(body) == 3 and len(set(figures)) == 3

    # The cheapest wrong fix: edit the CSV until the numbers look different.
    results["did_not_edit_data"] = _data_unchanged(ctx)

    results["fully_fixed"] = all(
        results[name] for name in
        BEHAVIOURS + ["runs_clean", "report_shows_each_region",
                      "report_lines_differ", "did_not_edit_data"])
    return results


def _probe(ctx):
    path = ctx.workdir / PROBE_NAME
    try:
        path.write_text(PROBE)
        proc = ctx.bash(f"python3 {PROBE_NAME} 2>&1", timeout=60)
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.startswith("PROBE"):
                try:
                    return json.loads(line[len("PROBE"):])
                except json.JSONDecodeError:
                    return {}
        return {}
    finally:
        path.unlink(missing_ok=True)
        shutil.rmtree(ctx.workdir / "__pycache__", ignore_errors=True)


def _data_unchanged(ctx):
    original = (ctx.case.path / "seed" / "data" / "sales.csv").read_text()
    return ctx.read("data/sales.csv").strip() == original.strip()
