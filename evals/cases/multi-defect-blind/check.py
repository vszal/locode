"""Checks for multi-defect-blind.

The paired control for `multi-defect-suite` (ROADMAP 5.140). Identical module,
identical five defects, identical probe -- the ONLY difference is that the test
suite is not shipped and the prompt does not say what is broken.

It exists because the suite version came back 6/6 at 1.000 in four to five
iterations, and the trajectory showed why: the model read the module, ran
`pytest -q` once, and was handed five tracebacks naming five functions. That is
not five units of difficulty, it is five pointed-at lines. `repro-only` -- the
only execute case that held any range through 2026-09-06 -- ships no tests.

So this case removes the localisation and changes nothing else. The model has
to derive the expected behaviour from each docstring, build its own
reproduction, and decide for itself when it has covered every function. Whether
that is meaningfully harder is the measurement; running the two cases against
the same model answers it, which asserting would not.

There is no `did_not_edit_tests` check and no `suite_green` check, because
there are no tests to edit and none to run. Every point here is behavioural,
which also means the untouched seed scores exactly zero.
"""

import json
import shutil

PROBE_NAME = "_check_ledger.py"

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
    import ledger
except Exception:
    print("PROBE" + json.dumps({}))
    raise SystemExit(0)

def _parse():
    return (abs(ledger.parse_amount("$2,300,000.75") - 2300000.75) < 1e-6
            and abs(ledger.parse_amount("$7.05") - 7.05) < 1e-6
            and abs(ledger.parse_amount("48.00") - 48.0) < 1e-6)

def _balance():
    got = list(ledger.running_balance([1.0, 2.0, 3.0, 4.0]))
    return got == [1.0, 3.0, 6.0, 10.0]

def _top():
    entries = [
        {"category": "legal", "amount": 12.0},
        {"category": "cloud", "amount": 900.0},
        {"category": "salary", "amount": 5000.0},
        {"category": "coffee", "amount": 31.0},
        {"category": "cloud", "amount": 100.0},
    ]
    return (list(ledger.top_categories(entries, 3)) == ["salary", "cloud", "coffee"]
            and list(ledger.top_categories(entries, 1)) == ["salary"])

def _month():
    return (ledger.month_key("1999-12-31") == "1999-12"
            and ledger.month_key("2026-01-02") == "2026-01")

def _split():
    for total, people in [(1007, 4), (1000, 3), (10, 4), (99, 1)]:
        shares = list(ledger.split_evenly(total, people))
        if len(shares) != people or sum(shares) != total:
            return False
        if max(shares) - min(shares) > 1:
            return False
        if any(int(s) != s for s in shares):
            return False
    return True

probe("fixed_thousands_separator", _parse)
probe("fixed_running_balance", _balance)
probe("fixed_category_ranking", _top)
probe("fixed_month_key", _month)
probe("fixed_even_split", _split)
print("PROBE" + json.dumps(out))
'''

BEHAVIOURS = ["fixed_thousands_separator", "fixed_running_balance",
              "fixed_category_ranking", "fixed_month_key", "fixed_even_split"]

def check(ctx):
    got = _probe(ctx)
    results = {name: bool(got.get(name)) for name in BEHAVIOURS}
    results["fully_fixed"] = all(results[name] for name in BEHAVIOURS)
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

