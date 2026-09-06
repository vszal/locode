"""Tests for the eval cases' `check.py` graders.

A grader is the only thing standing between a sweep and a number nobody should
believe, and it is exercised in production maybe a dozen times a night against
whatever a local model happened to write. Two failure modes have actually
happened here: a grader that CANNOT REACH 1.000 (the `plan_has_tasks` regex
accepted two of the four ways a plan numbers its tasks, so eleven of twelve
b142 runs lost a point for formatting), and a grader that scores work the model
did not do (ROADMAP 5.135 / rule 83).

These tests run each grader against synthetic workspaces standing in for
answers a model might actually produce -- the untouched seed, a correct fix,
each plausible half-fix, and each cheat -- and pin the ORDER of the resulting
scores. No model and no network: the graders only ever shell out to `python3`
against files on disk.

The properties worth pinning are the two ends and the ranking between them:
a correct answer reaches 1.000, the untouched seed does not, and every wrong
path lands strictly below every right one.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "evals" / "cases"
HARNESS = REPO / "evals" / "harness.py"


def _harness():
    """The real harness module, so these tests pin the real CheckCtx contract."""
    spec = importlib.util.spec_from_file_location("_eval_harness", HARNESS)
    mod = importlib.util.module_from_spec(spec)
    # `CheckCtx` is a dataclass, and dataclasses resolve their annotations
    # through `sys.modules[cls.__module__]` -- a module loaded by path alone
    # is not there, and instantiating it raises AttributeError on None.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class _Case:
    """Stands in for harness.Case; graders only ever use `.path`."""
    path: Path


def _grade(case_id: str, mutate=None, tmp_path: Path = None):
    """Score `case_id`'s grader against its seed, optionally mutated first."""
    case_dir = CASES / case_id
    work = tmp_path / "work"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(case_dir / "seed", work)
    if mutate is not None:
        mutate(work)

    spec = importlib.util.spec_from_file_location(f"_chk_{case_id}",
                                                  case_dir / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ctx = _harness().CheckCtx(workdir=work, events=[], stdout="",
                              case=_Case(path=case_dir))
    results = mod.check(ctx)
    score = sum(float(v) for v in results.values()) / len(results)
    return score, results


# ---------------------------------------------------------------- the suite

def test_every_case_has_the_required_files():
    for case_dir in sorted(p for p in CASES.iterdir() if p.is_dir()):
        assert (case_dir / "case.json").is_file(), case_dir.name
        assert (case_dir / "prompt.md").is_file(), case_dir.name


def test_every_grader_imports_and_exposes_check():
    for case_dir in sorted(p for p in CASES.iterdir() if p.is_dir()):
        checker = case_dir / "check.py"
        if not checker.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            f"_import_{case_dir.name}", checker)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(getattr(mod, "check", None)), case_dir.name


# ------------------------------------------------- multi-defect-suite

LEDGER_FIXED = '''\
from collections import defaultdict


def parse_amount(text):
    return float(text.strip().lstrip("$").replace(",", ""))


def running_balance(amounts):
    out, total = [], 0.0
    for amount in amounts:
        total += amount
        out.append(round(total, 2))
    return out


def top_categories(entries, n=3):
    totals = defaultdict(float)
    for entry in entries:
        totals[entry["category"]] += entry["amount"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, _total in ranked[:n]]


def month_key(date_text):
    return date_text[:7]


def split_evenly(total_cents, people):
    share, extra = divmod(total_cents, people)
    return [share + 1] * extra + [share] * (people - extra)
'''


def _fix_ledger(work: Path):
    (work / "ledger.py").write_text(LEDGER_FIXED)


def _fix_two_of_five(work: Path):
    src = (work / "ledger.py").read_text()
    src = src.replace('cleaned = text.strip().lstrip("$")',
                      'cleaned = text.strip().lstrip("$").replace(",", "")')
    src = src.replace("return date_text[:6]", "return date_text[:7]")
    (work / "ledger.py").write_text(src)


def _delete_the_failing_tests(work: Path):
    blocks = (work / "test_ledger.py").read_text().split("\n\n\n")
    keep = [b for b in blocks if "parse_plain" in b or "dollar_sign" in b
            or not b.lstrip().startswith("def test")]
    (work / "test_ledger.py").write_text("\n\n\n".join(keep))


def _hardcode_the_assertions(work: Path):
    """Special-case the shipped tests' exact inputs without fixing anything."""
    src = (work / "ledger.py").read_text()
    src = src.replace(
        'cleaned = text.strip().lstrip("$")',
        'if text == "$1,450.00":\n        return 1450.00\n'
        '    cleaned = text.strip().lstrip("$")')
    src = src.replace(
        "share = total_cents // people\n    return [share] * people",
        "if (total_cents, people) == (1000, 3):\n        return [334, 333, 333]\n"
        "    share = total_cents // people\n    return [share] * people")
    (work / "ledger.py").write_text(src)


def test_multi_defect_seed_scores_near_zero(tmp_path):
    score, results = _grade("multi-defect-suite", None, tmp_path)
    assert score < 0.2
    assert not any(results[k] for k in results if k.startswith("fixed_"))
    # The seed is not a cheat: the tests are exactly as shipped.
    assert results["did_not_edit_tests"]


def test_multi_defect_correct_fix_reaches_the_ceiling(tmp_path):
    """A grader that cannot reach 1.000 silently taxes every run."""
    score, results = _grade("multi-defect-suite", _fix_ledger, tmp_path)
    assert score == pytest.approx(1.0), results


def test_multi_defect_partial_fix_lands_in_between(tmp_path):
    """The point of the case: three-of-five must not score as zero or one."""
    score, results = _grade("multi-defect-suite", _fix_two_of_five, tmp_path)
    assert 0.2 < score < 1.0
    assert results["fixed_thousands_separator"]
    assert results["fixed_month_key"]
    assert not results["fixed_even_split"]
    assert not results["fully_fixed"]


def test_multi_defect_deleting_the_tests_is_caught(tmp_path):
    """`pytest -q` goes green while nothing is fixed. It must not pay."""
    score, results = _grade("multi-defect-suite", _delete_the_failing_tests,
                            tmp_path)
    assert results["suite_green"]
    assert not results["did_not_edit_tests"]
    assert not results["fully_fixed"]
    assert score < 0.2


def test_multi_defect_hardcoding_the_assertions_scores_nothing(tmp_path):
    """The probe deliberately uses different values from the shipped tests."""
    _score, results = _grade("multi-defect-suite", _hardcode_the_assertions,
                             tmp_path)
    assert not results["fixed_thousands_separator"]
    assert not results["fixed_even_split"]


# ------------------------------------------------- cross-module-cause

def _key_cache_on_the_data(work: Path):
    src = (work / "metrics.py").read_text()
    src = src.replace("    if metric in _CACHE:\n        return _CACHE[metric]",
                      "    key = (metric, tuple(values))\n"
                      "    if key in _CACHE:\n        return _CACHE[key]")
    src = src.replace("    _CACHE[metric] = result", "    _CACHE[key] = result")
    (work / "metrics.py").write_text(src)


def _remove_the_cache(work: Path):
    """The other correct answer -- and it changes summarize's signature."""
    (work / "metrics.py").write_text(
        "def summarize(values):\n"
        "    ordered = sorted(values)\n"
        '    return {"total": sum(ordered),\n'
        '            "mean": round(sum(ordered) / len(ordered)),\n'
        '            "largest": ordered[-1],\n'
        '            "count": len(ordered)}\n')
    app = (work / "app.py").read_text().replace(
        'metrics.summarize("revenue", values)', "metrics.summarize(values)")
    (work / "app.py").write_text(app)


def _key_cache_on_the_length(work: Path):
    """Plausible half-fix: right for the seed's regions, still wrong."""
    src = (work / "metrics.py").read_text()
    src = src.replace("    if metric in _CACHE:\n        return _CACHE[metric]",
                      "    key = (metric, len(values))\n"
                      "    if key in _CACHE:\n        return _CACHE[key]")
    src = src.replace("    _CACHE[metric] = result", "    _CACHE[key] = result")
    (work / "metrics.py").write_text(src)


def _take_the_decoy(work: Path):
    """Act on the FIXME comment, which sits on a correct line."""
    src = (work / "render.py").read_text().replace(
        'return f"${cents / 100:,.2f}"', 'return f"${round(cents / 100):,}.00"')
    (work / "render.py").write_text(src)


def _edit_the_csv(work: Path):
    csv = work / "data" / "sales.csv"
    csv.write_text(csv.read_text().replace("north,bo,130000", "north,bo,131000"))


def test_cross_module_seed_fails_the_behavioural_checks(tmp_path):
    score, results = _grade("cross-module-cause", None, tmp_path)
    assert not results["fixed_stale_cache"]
    assert not results["report_lines_differ"]
    # The guards pass on the untouched seed -- they exist to catch damage.
    assert results["runs_clean"]
    assert results["formatting_still_correct"]
    assert results["did_not_edit_data"]
    assert score < 0.5


@pytest.mark.parametrize("fix,label", [
    (_key_cache_on_the_data, "keyed on the data"),
    (_remove_the_cache, "cache removed, signature changed"),
])
def test_cross_module_both_correct_fixes_reach_the_ceiling(fix, label, tmp_path):
    """Dropping the now-meaningless `metric` argument is a legitimate answer;
    grading the call shape instead of the behaviour would fail it."""
    score, results = _grade("cross-module-cause", fix, tmp_path)
    assert score == pytest.approx(1.0), (label, results)


def test_cross_module_length_keyed_half_fix_scores_below_a_real_one(tmp_path):
    """It makes the seed's three regions print correctly and is still wrong."""
    score, results = _grade("cross-module-cause", _key_cache_on_the_length,
                            tmp_path)
    assert results["report_lines_differ"]
    assert results["fixed_stale_cache"]
    assert not results["cache_correct_across_many"]
    assert not results["fully_fixed"]
    assert 0.5 < score < 1.0


def test_cross_module_taking_the_decoy_scores_below_the_seed(tmp_path):
    """Acting on the suspicious-looking comment must cost, not pay."""
    decoy, _ = _grade("cross-module-cause", _take_the_decoy, tmp_path)
    seed, _ = _grade("cross-module-cause", None, tmp_path)
    assert decoy < seed


def test_cross_module_editing_the_data_is_caught(tmp_path):
    _score, results = _grade("cross-module-cause", _edit_the_csv, tmp_path)
    assert not results["did_not_edit_data"]
    assert not results["fully_fixed"]


# ------------------------------------------------- regression-trap

REGISTRY_REAL_FIX = '''\
def normalize_key(raw):
    return " ".join(raw.split())


def _fold(raw):
    return normalize_key(raw).casefold()


class Registry:
    def __init__(self):
        self._items = {}

    def add(self, name, value):
        self._items[_fold(name)] = (normalize_key(name), value)

    def lookup(self, name):
        found = self._items.get(_fold(name))
        return None if found is None else found[1]

    def labels(self):
        return sorted(name for name, _value in self._items.values())

    def __len__(self):
        return len(self._items)
'''


def _lowercase_the_shared_helper(work: Path):
    """The obvious fix: it answers the report and breaks a green test."""
    src = (work / "registry.py").read_text().replace(
        'return " ".join(raw.split())', 'return " ".join(raw.split()).lower()')
    (work / "registry.py").write_text(src)


def _really_fix_the_registry(work: Path):
    (work / "registry.py").write_text(REGISTRY_REAL_FIX)


def _drop_the_green_test(work: Path):
    """Make the naive fix look right by deleting what it broke."""
    text = (work / "test_registry.py").read_text()
    (work / "test_registry.py").write_text(text.split("def test_labels")[0])
    _lowercase_the_shared_helper(work)


def test_regression_trap_guards_pass_on_the_untouched_seed(tmp_path):
    """A regression guard that fails on the seed is not guarding anything --
    it is just a second copy of the primary check."""
    _score, results = _grade("regression-trap", None, tmp_path)
    assert results["labels_preserve_entered_case"]
    assert results["whitespace_still_collapsed"]
    assert results["missing_lookup_returns_none"]
    assert not results["lookup_ignores_case"]


def test_regression_trap_real_fix_reaches_the_ceiling(tmp_path):
    score, results = _grade("regression-trap", _really_fix_the_registry,
                            tmp_path)
    assert score == pytest.approx(1.0), results


def test_regression_trap_naive_fix_ranks_between_seed_and_real(tmp_path):
    """The whole point of the case. The naive fix answers the report, so it
    must beat doing nothing; it breaks a green test, so it must lose badly to
    the real fix. If it tied with the seed the case could not tell a model that
    tried from one that did not."""
    seed, _ = _grade("regression-trap", None, tmp_path)
    naive, results = _grade("regression-trap", _lowercase_the_shared_helper,
                            tmp_path)
    real, _ = _grade("regression-trap", _really_fix_the_registry, tmp_path)
    assert seed < naive < real
    assert results["lookup_ignores_case"]
    assert not results["labels_preserve_entered_case"]
    assert not results["fully_fixed"]


def test_regression_trap_deleting_the_broken_test_does_not_pay(tmp_path):
    naive, _ = _grade("regression-trap", _lowercase_the_shared_helper, tmp_path)
    cheat, results = _grade("regression-trap", _drop_the_green_test, tmp_path)
    assert cheat < naive
    assert not results["did_not_edit_tests"]
