"""`evals/checkdeps.py` — which outcome checks actually discriminate.

The analysis is only as good as its handling of uninformative runs, and it got
that wrong twice before landing: an all-or-nothing run makes every pair of
checks agree, and a check that is constant *within* the informative runs agrees
with every other constant one. Both manufacture redundancy that is not there,
so both are pinned here.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import checkdeps  # noqa: E402


def _sweep(tmp_path, case, runs, name="sweep"):
    """Write a results.json whose runs are `[{check: bool}, ...]`."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(
        [{"case": case, "model": "m", "checks": c} for c in runs]))
    return str(d)


def _run(capsys, tmp_path, checks_per_run, decls=(frozenset(), frozenset())):
    d = _sweep(tmp_path, "c", checks_per_run)
    orig = checkdeps._decls
    checkdeps._decls = lambda cid: decls
    try:
        checkdeps.main([d])
    finally:
        checkdeps._decls = orig
    return capsys.readouterr().out


def test_all_or_nothing_runs_are_not_evidence_of_redundancy(capsys, tmp_path):
    # Twelve runs that each did everything or nothing. Every pair of checks
    # agrees in all of them, which is not a fact about the checks at all.
    runs = [{"a": True, "b": True}] * 6 + [{"a": False, "b": False}] * 6
    out = _run(capsys, tmp_path, runs)
    assert "cannot analyse: every run was all-or-nothing" in out
    assert "TWIN" not in out


def test_check_constant_among_mixed_runs_is_scaffolding(capsys, tmp_path):
    # `scaf` is true in every mixed run: it never separates anything, but a
    # flat mean still pays it. It is false on some run, so it is not a guard.
    runs = [{"scaf": True, "hard": i % 2 == 0, "other": i % 3 == 0}
            for i in range(10)] + [{"scaf": False, "hard": False,
                                    "other": False}]
    out = _run(capsys, tmp_path, runs)
    assert "SCAFFOLD  scaf" in out
    assert "TWIN" not in out, "a constant check must not pair off with others"


def test_never_earned_check_is_a_wall(capsys, tmp_path):
    runs = [{"wall": False, "a": i % 2 == 0, "b": i % 4 < 2}
            for i in range(20)]
    out = _run(capsys, tmp_path, runs)
    assert "WALL      wall" in out
    assert "-> 2 effective of 3" in out


def test_a_pair_that_only_tracks_doing_anything_is_scaffolding(capsys, tmp_path):
    # Degenerate but real: `a` and `b` move together and nothing else moves,
    # so every mixed run is one where both fired. Their apparent variation is
    # entirely "did this run do anything at all" -- the very thing the mixed
    # filter exists to discard -- so the honest answer is that this case
    # discriminates nothing, not that it has one good twin pair.
    runs = [{"wall": False, "a": i % 2 == 0, "b": i % 2 == 0}
            for i in range(20)]
    out = _run(capsys, tmp_path, runs)
    assert "SCAFFOLD  a, b" in out
    assert "-> 0 effective of 3" in out


def test_identical_vectors_are_one_effective_check(capsys, tmp_path):
    runs = [{"a": i % 2 == 0, "b": i % 2 == 0, "c": i % 4 < 2}
            for i in range(20)]
    out = _run(capsys, tmp_path, runs)
    assert "TWIN x2   a, b" in out
    assert "-> 2 effective of 3" in out


def test_inverse_checks_are_a_tradeoff_not_a_twin(capsys, tmp_path):
    # Perfectly anti-correlated: the model wins one or the other, never both.
    # That is a finding about the case, not a reason to merge the two.
    runs = [{"a": i % 2 == 0, "b": i % 2 == 1} for i in range(16)]
    out = _run(capsys, tmp_path, runs)
    assert "TRADEOFF  a <-inverse-> b" in out
    assert "TWIN" not in out
    assert "-> 2 effective of 2" in out


def test_guards_and_derived_are_not_scored_as_outcomes(capsys, tmp_path):
    runs = [{"g": True, "d": i % 2 == 0, "real": i % 2 == 0} for i in range(10)]
    out = _run(capsys, tmp_path, runs,
               decls=(frozenset({"g"}), frozenset({"d"})))
    assert "k=1 outcome checks" in out
    for name in ("g,", "g\n", "d,", "d\n"):
        assert f"SCAFFOLD  {name}" not in out


def test_pools_the_same_case_across_sweeps(capsys, tmp_path):
    # Neither sweep alone clears the MIN_MIXED floor; pooled, they do.
    made = [{"x": i % 2 == 0, "y": i % 2 == 0, "z": i % 4 < 2}
            for i in range(10)]
    a = _sweep(tmp_path, "c", made, name="s1")
    b = _sweep(tmp_path, "c", made, name="s2")
    orig = checkdeps._decls
    checkdeps._decls = lambda cid: (frozenset(), frozenset())
    try:
        checkdeps.main([a, b])
    finally:
        checkdeps._decls = orig
    out = capsys.readouterr().out
    assert "10/20 runs mixed" in out, "runs from both sweeps must pool"
    assert "TWIN x2   x, y" in out


def test_refuses_when_nothing_parsed(tmp_path):
    with pytest.raises(SystemExit):
        checkdeps.main([str(tmp_path / "nope")])
