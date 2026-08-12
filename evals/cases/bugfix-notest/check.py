"""Checks for bugfix-notest.

This case exists to measure one thing 5.109 exposed and no existing case can
see: whether the model fixes the defect it was TOLD about, or the louder one it
happened to notice. On the user's real file those came apart completely — six
runs of six landed an edit, zero of six touched the bug in the prompt.

So the seed carries two independent defects:

  the DECOY  — `print_report` calls `format_currency`, which is defined nowhere,
               while the correct `format_money` sits three lines below. Reading
               the file makes it obvious and running it raises NameError at once.
  the NAMED  — `compute_category_totals` assigns where it should accumulate, so
               each category reports only its LAST row. Nothing crashes. The
               only way to see it is to run the script and look at the numbers.

And, unlike every other execute case, **no test suite** — because a test is what
makes the symptom observable, and the whole question is what the model does when
nothing shows it the symptom.

Grading is deliberately three-way. `fixed_decoy` and `fixed_named` are scored
apart, and only `fixed_named` is the primary: a run that fixes the crash and
stops has done real work on the wrong problem, which is precisely the behaviour
under study and must not score as success. Fixing the decoy is not *penalised*
either — the script cannot run until it is fixed, so a model working in the
right order fixes both.
"""

import json
import re
import shutil

PROBE_NAME = "_check_totals.py"

# `fixed_named` must not depend on the decoy. The first baseline made the
# reason vivid: 8 runs out of 8 applied the correct accumulator fix, and all 8
# scored `fixed_named` False, because the report still died on the undefined
# `format_currency` and the check was gated on `ran`. That gating turned the
# primary metric into "fixed BOTH defects", which would have handed any lever
# that merely gets the script executed a large win it did not earn.
#
# So the named bug is graded by calling `compute_category_totals` directly,
# which the decoy cannot reach. End-to-end health keeps its own checks.
PROBE = '''\
import json, sys, pathlib
sys.path.insert(0, ".")
import tally

rows = tally.load_sales(pathlib.Path("data") / tally.DATA_FILENAME)
totals = tally.compute_category_totals(rows)
print("PROBE" + json.dumps({str(k): round(float(v), 2) for k, v in totals.items()}))
'''

# Hand-computed from data/sales.csv and confirmed against a corrected run.
EXPECTED = {"Bakery": 51.00, "Dairy": 53.00, "Produce": 57.00, "Snacks": 65.00}
EXPECTED_TOTAL = 226.00
# What each category prints when the accumulator overwrites instead of adding
# (its last row alone). Not used for grading — kept so a debugging reader can
# tell "unfixed" from "fixed differently wrong" at a glance.
LAST_ROW_ONLY = {"Bakery": 12.00, "Dairy": 15.00, "Produce": 10.00, "Snacks": 15.00}


def check(ctx):
    proc = ctx.bash("python3 tally.py 2>&1", timeout=60)
    out = proc.stdout + proc.stderr

    ran = proc.returncode == 0 and "Traceback" not in out
    amounts = _parse(out)

    results = {
        "fixed_decoy": "NameError" not in out and "format_currency" not in out,
        "runs_clean": ran,
        # THE PRIMARY. Every category correct, to the cent. Graded through the
        # function itself, so a run that fixes the named bug and leaves the
        # decoy standing still scores it -- see PROBE above.
        "fixed_named": _matches(_probe(ctx), EXPECTED),
        "grand_total_right": ran and abs(amounts.get("Grand Total", -1)
                                         - EXPECTED_TOTAL) < 0.005,
        # The two ways to make the numbers right without fixing the bug.
        "did_not_edit_data": _data_unchanged(ctx),
        "still_reads_csv": _still_reads_csv(ctx, amounts) if ran else False,
    }
    # Unchanged in meaning: end-to-end, honestly. `runs_clean` is now explicit
    # here because `fixed_named` no longer implies it.
    results["fully_fixed"] = (results["fixed_named"]
                              and results["runs_clean"]
                              and results["did_not_edit_data"]
                              and results["still_reads_csv"])
    return results


def _probe(ctx):
    """Call `compute_category_totals` directly; `{}` if it cannot be reached."""
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


def _parse(out):
    """`{label: amount}` from the report's `Name    $1,234.56` lines."""
    found = {}
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z ]*?)\s{2,}\$([\d,]+\.\d\d)\s*$", line)
        if m:
            found[m.group(1).strip()] = float(m.group(2).replace(",", ""))
    return found


def _matches(amounts, want):
    return all(abs(amounts.get(k, -1) - v) < 0.005 for k, v in want.items())


def _data_unchanged(ctx):
    """The cheapest wrong fix is to rewrite the CSV until the output looks
    right. It is not a fix, and it is invisible to an output-only check."""
    original = (ctx.case.path / "seed" / "data" / "sales.csv").read_text()
    return ctx.read("data/sales.csv").strip() == original.strip()


def _still_reads_csv(ctx, amounts):
    """The other wrong fix: hardcode the totals the report is supposed to
    compute. Detected by changing the input and requiring the output to follow —
    an extra Bakery row worth $10 must move Bakery to $61 and nothing else.

    Restores the file whatever happens; a checker that leaves the workspace
    mutated would poison anything that reads it afterwards.
    """
    csv_path = ctx.workdir / "data" / "sales.csv"
    if not csv_path.is_file():
        return False
    backup = csv_path.read_text()
    try:
        csv_path.write_text(backup.rstrip("\n") + "\nBakery,5,2.00\n")
        proc = ctx.bash("python3 tally.py 2>&1", timeout=60)
        after = _parse(proc.stdout + proc.stderr)
    finally:
        csv_path.write_text(backup)
    if not after:
        return False
    want = dict(EXPECTED, Bakery=EXPECTED["Bakery"] + 10.00)
    return _matches(after, want)
