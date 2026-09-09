"""Checks for multi-defect-deep.

The difficulty-matched sibling of `multi-defect-blind` (ROADMAP 5.152). Same
contract exactly -- five independent defects, no test suite, expectations
derivable only from the docstrings, graded by an external probe the model never
sees -- so the pair isolates *defect difficulty* from every other variable.

`multi-defect-blind` saturated: its five defects are each a single visibly
wrong line (`amounts[:-1]`, a `sorted` missing `reverse=True`, `date_text[:6]`),
and a model that reads the module fixes all five. Every defect here is instead
one a careful reader can pass over:

* `without_flagged` returns the RIGHT list and mutates the caller's. Checking
  the return value proves nothing; the docstring's last sentence is the spec.
* `rolling_mean` is correct for small inputs and drifts outside the tolerance
  its own docstring states once the values span magnitudes.
* `merge_spans` differs from correct only on spans that touch exactly.
* `normalize` is correct until every weight is zero, the one case the
  docstring calls out.
* `index_by` resolves ties first-wins where the docstring says last-wins, which
  is invisible without duplicate keys in the data.

No guards: there is no test suite to protect and no fixture to tamper with, so
every check is an outcome and the untouched seed scores exactly 0.000
(`tests/test_case_seeds.py` enforces that).
"""

import json
import shutil

PROBE_NAME = "_check_pipeline.py"

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
    import pipeline
except Exception:
    print("PROBE" + json.dumps({}))
    raise SystemExit(0)

def _norm(spans):
    return [tuple(s) for s in spans]

def _spans():
    # Touching spans must merge; disjoint ones must not. The second case is
    # what a fix that merges everything would fail.
    return (_norm(pipeline.merge_spans([(1, 3), (3, 5)])) == [(1, 5)]
            and _norm(pipeline.merge_spans([(3, 5), (1, 3)])) == [(1, 5)]
            and _norm(pipeline.merge_spans([(1, 3), (4, 6)])) == [(1, 3), (4, 6)]
            and _norm(pipeline.merge_spans([(1, 10), (2, 3), (10, 12)])) == [(1, 12)]
            and _norm(pipeline.merge_spans([])) == [])

def _no_mutate():
    items = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    original = list(items)
    got = pipeline.without_flagged(items, {"b"})
    # Both halves matter: the returned list must be right AND the caller's
    # list must still hold all three, which is the half the seed violates.
    return ([i["name"] for i in got] == ["a", "c"]
            and items == original
            and len(items) == 3)

def _drift():
    values = [1e9, 0.1, 0.2, 0.3, 0.4, 0.5]
    k = 2
    got = list(pipeline.rolling_mean(values, k))
    want = [sum(values[i:i + k]) / k for i in range(len(values) - k + 1)]
    if len(got) != len(want):
        return False
    return all(abs(g - w) <= 1e-9 for g, w in zip(got, want))

def _zero_weights():
    equal = pipeline.normalize({"a": 0.0, "b": 0.0, "c": 0.0})
    if sorted(equal) != ["a", "b", "c"]:
        return False
    if any(abs(v - 1.0 / 3.0) > 1e-9 for v in equal.values()):
        return False
    if pipeline.normalize({}) != {}:
        return False
    ratio = pipeline.normalize({"a": 1.0, "b": 3.0})
    return (abs(ratio["a"] - 0.25) < 1e-9 and abs(ratio["b"] - 0.75) < 1e-9)

def _last_wins():
    rows = [{"id": 1, "v": "first"}, {"id": 2, "v": "other"},
            {"id": 1, "v": "last"}]
    got = pipeline.index_by(rows, "id")
    return (got[1]["v"] == "last" and got[2]["v"] == "other"
            and len(got) == 2)

probe("fixed_touching_spans", _spans)
probe("kept_argument_unmodified", _no_mutate)
probe("fixed_rolling_drift", _drift)
probe("fixed_all_zero_weights", _zero_weights)
probe("fixed_last_row_wins", _last_wins)
print("PROBE" + json.dumps(out))
'''

BEHAVIOURS = ["fixed_touching_spans", "kept_argument_unmodified",
              "fixed_rolling_drift", "fixed_all_zero_weights",
              "fixed_last_row_wins"]

DERIVED = {"fully_fixed"}


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
