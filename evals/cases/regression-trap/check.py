"""Checks for regression-trap.

Why this case exists (ROADMAP 5.140): `multi-defect-suite` measures breadth and
`cross-module-cause` measures depth, and both reward finding MORE. This one
rewards not breaking something -- the situation where the obvious fix to the
reported bug is wrong because a different caller depends on the current
behaviour.

`normalize_key` has two callers with conflicting needs. `lookup` wants names
compared case-insensitively; `labels` lists the normalised key back to the user
and must show the name as it was entered. Lowercasing inside `normalize_key`
makes both reported failures pass and turns `labels()` into lowercase, breaking
`test_labels_preserve_the_name_as_entered` -- which is GREEN in the seed. The
real fix separates the two roles: fold for the index, keep the original for
display.

This is why the case does not simply grade `pytest -q`. Running only the tests
you were told about is precisely the failure being measured, so the score has
to come from behaviour the model was not pointed at. `suite_green` is here as
one check out of eight, not as the verdict.

The seed already passes three of the behaviour probes, and that is the shape of
a regression trap rather than a flaw in the rubric: most of the module works,
and the question is whether it still works afterwards. What the case has to
separate is the naive fix from the real one, and it does -- lowercasing inside
`normalize_key` scores barely above changing nothing at all, because it buys
two behaviours and sells one.

Probe values differ from the shipped tests throughout, so a fix special-cased
to `"Acme Corp"` scores nothing.
"""

import json
import shutil

PROBE_NAME = "_check_registry.py"

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
    from registry import Registry
except Exception:
    print("PROBE" + json.dumps({}))
    raise SystemExit(0)

def _case_insensitive():
    reg = Registry()
    reg.add("Globex Industries", 41)
    return all(reg.lookup(spelling) == 41 for spelling in [
        "Globex Industries", "GLOBEX INDUSTRIES",
        "globex industries", "GloBex InDustries"])

def _labels_keep_case():
    reg = Registry()
    reg.add("MiXeD CaSe Co", 1)
    reg.add("ALLCAPS Inc", 2)
    return list(reg.labels()) == ["ALLCAPS Inc", "MiXeD CaSe Co"]

def _whitespace_still_collapsed():
    reg = Registry()
    reg.add("  Hooli   Corp  ", 7)
    return (list(reg.labels()) == ["Hooli Corp"]
            and reg.lookup("Hooli    Corp") == 7
            and reg.lookup("Hooli Corp") == 7)

def _variant_replaces():
    reg = Registry()
    reg.add("Vandelay Industries", 1)
    reg.add("VANDELAY industries", 2)
    return len(reg) == 1 and reg.lookup("Vandelay Industries") == 2

def _case_and_whitespace_together():
    """Both normalisations at once -- a lookup-site fix that folds case but
    forgets to collapse whitespace passes _case_insensitive and fails here."""
    reg = Registry()
    reg.add("Initech  LLC", 12)
    return (reg.lookup("  INITECH   llc  ") == 12
            and reg.lookup("initech llc") == 12)

def _missing_is_none():
    reg = Registry()
    reg.add("Soylent Corp", 3)
    return reg.lookup("Nobody Here") is None

probe("lookup_ignores_case", _case_insensitive)
probe("labels_preserve_entered_case", _labels_keep_case)
probe("whitespace_still_collapsed", _whitespace_still_collapsed)
probe("case_variant_replaces_entry", _variant_replaces)
probe("lookup_folds_case_and_whitespace", _case_and_whitespace_together)
probe("missing_lookup_returns_none", _missing_is_none)
print("PROBE" + json.dumps(out))
'''

BEHAVIOURS = ["lookup_ignores_case", "labels_preserve_entered_case",
              "whitespace_still_collapsed", "case_variant_replaces_entry",
              "lookup_folds_case_and_whitespace", "missing_lookup_returns_none"]


def check(ctx):
    got = _probe(ctx)
    results = {name: bool(got.get(name)) for name in BEHAVIOURS}

    results["did_not_edit_tests"] = _tests_unchanged(ctx)

    proc = ctx.bash("python3 -m pytest -q 2>&1 | tail -5", timeout=180)
    blob = proc.stdout + proc.stderr
    results["suite_green"] = ("passed" in blob
                              and "failed" not in blob
                              and "error" not in blob.lower())

    results["fully_fixed"] = (all(results[name] for name in BEHAVIOURS)
                              and results["did_not_edit_tests"])
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


def _tests_unchanged(ctx):
    original = (ctx.case.path / "seed" / "test_registry.py").read_text()
    return ctx.read("test_registry.py").strip() == original.strip()
