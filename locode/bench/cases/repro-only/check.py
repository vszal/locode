"""Checks for repro-only.

Built to the specification in ROADMAP 5.117, after both `bugfix-notest` and
`plan-hijack` turned out to be unusable as vehicles for a lever about
reproducing before editing — the first because its named bug is findable by
reading (7/8 without running anything), the second because its script cannot be
run to completion at all.

The three properties this case has to have, and how it gets them:

1. RUNS OFFLINE. One script, one CSV, no network, no missing fixture.
   `python3 report.py` prints a full report on the seed as shipped.

2. INVISIBLE BY READING. The defect is `sorted(rows, key=lambda row:
   row["amount"])` in `largest_charges` — the CSV amounts are still strings
   there, so the ranking is lexicographic. Nothing crashes, no docstring
   contradicts the code, and `charge_amount()` does the float conversion
   correctly everywhere else, so the line looks like every other sort key.

3. UNMISSABLE IN THE OUTPUT. Lexicographic order puts an $89.99 charge in the
   top three and leaves the $1,450.00 one out entirely. Anyone who runs it once
   sees it.

The department totals are CORRECT and must stay that way; they are the part of
the report that already works, and breaking them to fix the ranking is a
regression, not a fix.

`fixed_ranking` is graded through `load_charges` -> `largest_charges` rather
than by reading the source, so a fix in either function counts. Two fixtures,
because one can be satisfied by returning constants.
"""

import json
import shutil

PROBE_NAME = "_check_ranking.py"

# (filename, csv body, expected descriptions in order). The second fixture's
# lexicographic order ("9.00" > "3.50" > "20.00" > "100.00") differs from its
# numeric order in a different way than the seed's, so a fix that merely
# reverses something cannot satisfy both.
FIXTURES = [
    ("data/spend.csv", None,
     ["Laptop refresh", "Conference booth", "Monitor batch"]),
    ("_fixture_b.csv",
     "department,description,amount\n"
     "A,alpha,9.00\nB,beta,100.00\nC,gamma,20.00\nD,delta,3.50\n",
     ["beta", "gamma", "alpha"]),
]

EXPECTED_TOTALS = {"Engineering": 2490.00, "Facilities": 385.25,
                   "Marketing": 1290.49}

PROBE = '''\
import json, sys, pathlib
sys.path.insert(0, ".")
import report

out = {}
for name in sys.argv[1:]:
    rows = report.load_charges(pathlib.Path(name))
    out[name] = [str(r["description"]) for r in report.largest_charges(rows)]
totals = report.department_totals(report.load_charges(pathlib.Path("data/spend.csv")))
out["__totals__"] = {str(k): round(float(v), 2) for k, v in totals.items()}
print("PROBE" + json.dumps(out))
'''


def check(ctx):
    got = _probe(ctx)
    proc = ctx.bash("python3 report.py 2>&1", timeout=60)
    out = proc.stdout + proc.stderr
    ran = proc.returncode == 0 and "Traceback" not in out

    ranking_ok = got is not None and all(
        got.get(name) == want for name, _body, want in FIXTURES)

    results = {
        "runs_clean": ran,
        # THE PRIMARY. Both fixtures ranked correctly, through the public path.
        "fixed_ranking": ranking_ok,
        # The part of the report that already worked. Breaking it is a
        # regression, and a "fix" that rewrites the money handling wholesale
        # tends to take this with it.
        "totals_still_right": _totals_ok(got),
        # The cheapest wrong fix: reorder the CSV until the output looks right.
        "did_not_edit_data": _data_unchanged(ctx),
        # Visible in the report itself, not just in the function.
        "report_shows_top_charge": ran and "Laptop refresh" in _top_block(out),
    }
    results["fully_fixed"] = (results["fixed_ranking"]
                              and results["runs_clean"]
                              and results["totals_still_right"]
                              and results["did_not_edit_data"])
    return results


def _probe(ctx):
    """Rank each fixture through `load_charges` -> `largest_charges`."""
    written = []
    try:
        (ctx.workdir / PROBE_NAME).write_text(PROBE)
        written.append(ctx.workdir / PROBE_NAME)
        names = []
        for name, body, _want in FIXTURES:
            if body is not None:
                path = ctx.workdir / name
                path.write_text(body)
                written.append(path)
            names.append(name)
        proc = ctx.bash(f"python3 {PROBE_NAME} " + " ".join(names), timeout=60)
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.startswith("PROBE"):
                try:
                    return json.loads(line[len("PROBE"):])
                except json.JSONDecodeError:
                    return None
        return None
    finally:
        for path in written:
            path.unlink(missing_ok=True)
        shutil.rmtree(ctx.workdir / "__pycache__", ignore_errors=True)


def _totals_ok(got):
    if got is None:
        return False
    totals = got.get("__totals__") or {}
    if set(totals) != set(EXPECTED_TOTALS):
        return False
    return all(abs(totals[k] - v) < 0.005 for k, v in EXPECTED_TOTALS.items())


def _top_block(out):
    """Just the "Top N charges" section, so a total cannot satisfy this."""
    marker = "Top "
    idx = out.find(marker)
    return out[idx:] if idx != -1 else ""


def _data_unchanged(ctx):
    original = (ctx.case.path / "seed" / "data" / "spend.csv").read_text()
    return ctx.read("data/spend.csv").strip() == original.strip()
