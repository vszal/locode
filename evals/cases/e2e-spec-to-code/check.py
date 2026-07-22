"""Checks for the end-to-end case.

This is the one that matters: did a high-level spec become a design, a plan, and
working tested code in a single session? Scored in stages so a partial run gets
partial credit and the report shows exactly where it fell over — "wrote both
docs, never coded" and "coded but never wrote the plan" are different failures
needing different harness fixes.

The functional checks run the model's own tests AND an independent test of our
own, because a model that writes weak tests can make `pytest -q` green without
implementing the spec.
"""

import re

INDEPENDENT_TEST = '''
import os, sys, tempfile, textwrap
sys.path.insert(0, os.getcwd())
import envcfg

FAILURES = []

def check(label, fn):
    try:
        fn()
    except Exception as e:
        FAILURES.append(f"{label}: {type(e).__name__}: {e}")

def _load(defaults, toml_text=None, prefix="APP", env=None):
    path = None
    if toml_text is not None:
        fh = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        fh.write(textwrap.dedent(toml_text))
        fh.close()
        path = fh.name
    fn = getattr(envcfg, "load", None) or getattr(envcfg, "load_config", None)
    if fn is None:
        raise AssertionError("no load()/load_config() function exported")
    try:
        return fn(defaults, path, prefix, env or {})
    except TypeError:
        return fn(defaults, path=path, prefix=prefix, environ=env or {})

def t_precedence():
    out = _load({"server": {"port": 1, "host": "a"}},
                '[server]\\nport = 2\\n',
                env={"APP__SERVER__HOST": "c"})
    assert out["server"]["port"] == 2, out
    assert out["server"]["host"] == "c", out

def t_defaults_survive():
    out = _load({"a": 1, "b": 2}, "a = 9\\n")
    assert out["b"] == 2, out

def t_coerce_int():
    out = _load({"n": 0}, None, env={"APP__N": "42"})
    assert out["n"] == 42 and isinstance(out["n"], int), out

def t_coerce_bool():
    for raw, want in [("true", True), ("TRUE", True), ("yes", True),
                      ("1", True), ("false", False), ("no", False), ("0", False)]:
        out = _load({"f": False}, None, env={"APP__F": raw})
        assert out["f"] is want, (raw, out)

def t_coerce_float():
    out = _load({"x": 0.0}, None, env={"APP__X": "1.5"})
    assert abs(out["x"] - 1.5) < 1e-9, out

def t_bad_coercion_raises():
    try:
        _load({"n": 0}, None, env={"APP__N": "banana"})
    except Exception as e:
        assert "n" in str(e).lower(), f"error should name the key: {e}"
        return
    raise AssertionError("bad int coercion did not raise")

def t_unknown_key_raises():
    try:
        _load({"n": 0}, None, env={"APP__NOPE": "1"})
    except Exception as e:
        return
    raise AssertionError("unknown env key did not raise")

def t_missing_file_ok():
    fn = getattr(envcfg, "load", None) or getattr(envcfg, "load_config", None)
    out = fn({"a": 1}, "/nonexistent/path/nope.toml", "APP", {})
    assert out["a"] == 1, out

for name, fn in sorted(globals().items()):
    if name.startswith("t_"):
        check(name, fn)

print("INDEPENDENT_PASS" if not FAILURES else "INDEPENDENT_FAIL")
for f in FAILURES:
    print("  -", f)
'''


def check(ctx):
    design = ctx.read("DESIGN.md")
    plan = ctx.read("PLAN.md")
    results = {}

    # --- stage 1: design -------------------------------------------------
    results["wrote_design_doc"] = len(design.strip()) >= 600
    results["design_covers_precedence"] = bool(
        re.search(r"(?i)(precedence|priority|override|highest|lowest)", design))
    results["design_covers_coercion"] = bool(
        re.search(r"(?i)(coerc|cast|convert|type)", design))

    # --- stage 2: plan ---------------------------------------------------
    results["wrote_plan_doc"] = len(plan.strip()) >= 400
    results["plan_has_milestones"] = bool(
        re.search(r"(?im)^[#\s*_]*(milestone|M\d+\b)", plan))
    results["plan_has_tasks"] = len(
        re.findall(r"(?m)^\s*(?:\d+[.)]\s+|[-*]\s*\[[ x]\]\s*)\S", plan)) >= 6

    # --- stage 3: code ---------------------------------------------------
    module = ctx.read("envcfg.py")
    results["wrote_module"] = bool(module.strip())
    results["wrote_tests"] = bool(ctx.read("test_envcfg.py").strip())

    own_tests_pass = False
    if results["wrote_module"] and results["wrote_tests"]:
        proc = ctx.bash("python3 -m pytest -q 2>&1 | tail -20", timeout=180)
        blob = proc.stdout + proc.stderr
        own_tests_pass = bool(re.search(r"\b\d+ passed", blob)) and \
            not re.search(r"\b\d+ (failed|error)", blob)
    results["own_tests_pass"] = own_tests_pass

    # Independent verification: does it actually implement the spec, or did it
    # write tests that agree with whatever it happened to build?
    independent = False
    if results["wrote_module"]:
        (ctx.workdir / "_independent_check.py").write_text(INDEPENDENT_TEST)
        proc = ctx.bash("python3 _independent_check.py 2>&1", timeout=120)
        independent = "INDEPENDENT_PASS" in proc.stdout
        # Surface the detail into the run log for debugging.
        ctx.independent_output = proc.stdout[-2000:]
    results["independent_spec_check"] = independent

    return results
