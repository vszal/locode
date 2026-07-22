"""Tests for the eval harness's measurement and gating logic.

The harness is not shipped code, but it decides whether a change lands, so the
parts that turn numbers into a verdict are worth pinning down — a gate that
reports FAIL on data it could not have measured is how a good change gets
reverted.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "eval_harness", ROOT / "evals" / "harness.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


harness = _load_harness()


def _run(case="c", model="m", score=1.0, **metrics):
    return harness.RunResult(
        case=case, track="t", model=model, repeat=1, score=score,
        checks={}, metrics=metrics, returncode=0, timed_out=False,
        seconds=1.0, workdir="/tmp")


# --- _gen_rate ------------------------------------------------------------
def test_gen_rate_pairs_start_with_end():
    events = [
        {"phase": "assistant_start", "t": 1.0},
        {"phase": "assistant_end", "t": 3.0, "chars": 200},
        {"phase": "assistant_start", "t": 5.0},
        {"phase": "assistant_end", "t": 7.0, "chars": 600},
    ]
    got = harness._gen_rate(events)
    assert got["gen_seconds"] == 4.0
    assert got["gen_chars"] == 800
    assert got["gen_chars_per_sec"] == 200.0


def test_gen_rate_is_none_for_logs_without_chars():
    """Sweeps recorded before throughput was tracked must compare as unknown,
    not as infinitely slow — otherwise every old baseline trips the confound
    check the moment it is used."""
    events = [
        {"phase": "assistant_start", "t": 0.0},
        {"phase": "assistant_end", "t": 9.0},
    ]
    assert harness._gen_rate(events)["gen_chars_per_sec"] is None


def test_gen_rate_ignores_unpaired_end():
    """A killed run's log can end mid-reply, or start with a stray end."""
    events = [
        {"phase": "assistant_end", "t": 2.0, "chars": 50},
        {"phase": "assistant_start", "t": 4.0},
    ]
    got = harness._gen_rate(events)
    assert got["gen_seconds"] == 0.0 and got["gen_chars"] == 0
    assert got["gen_chars_per_sec"] is None


def test_gen_rate_handles_empty_log():
    assert harness._gen_rate([])["gen_chars_per_sec"] is None


# --- _mean_rate -----------------------------------------------------------
def test_mean_rate_pools_rather_than_averaging_per_run():
    """Total chars over total seconds, so a 1-second run can't outvote a
    100-second one on what the box was doing."""
    runs = [_run(gen_chars=10, gen_seconds=1.0),      # 10 ch/s
            _run(gen_chars=900, gen_seconds=99.0)]    # ~9.1 ch/s
    assert harness._mean_rate(runs) == pytest.approx(9.1, abs=0.05)


def test_mean_rate_none_when_nothing_recorded():
    assert harness._mean_rate([_run(), _run()]) is None


# --- _validity_warnings ---------------------------------------------------
def _summary(rows, rate=None):
    return {"rows": {k: {} for k in rows}, "gen_rate": rate}


def test_partial_sweep_is_flagged():
    base = _summary(["a::m", "b::m", "c::m"])
    cand = _summary(["a::m"])
    warns = harness._validity_warnings(base, cand)
    assert len(warns) == 1
    assert "missing 2 of 3" in warns[0]


def test_complete_sweep_at_same_speed_is_not_flagged():
    base = _summary(["a::m", "b::m"], rate=100.0)
    cand = _summary(["a::m", "b::m"], rate=95.0)
    assert harness._validity_warnings(base, cand) == []


def test_degraded_throughput_is_flagged():
    base = _summary(["a::m"], rate=106.0)
    cand = _summary(["a::m"], rate=11.0)
    warns = harness._validity_warnings(base, cand)
    assert len(warns) == 1
    assert "chars/s" in warns[0]


def test_faster_candidate_is_not_flagged():
    """Only a SLOWER box confounds the result; a faster one can't manufacture
    a passing score out of a failing change."""
    base = _summary(["a::m"], rate=50.0)
    cand = _summary(["a::m"], rate=200.0)
    assert harness._validity_warnings(base, cand) == []


def test_missing_rate_on_either_side_skips_the_throughput_check():
    base = _summary(["a::m"], rate=None)
    cand = _summary(["a::m"], rate=1.0)
    assert harness._validity_warnings(base, cand) == []


def test_extra_candidate_rows_are_not_a_problem():
    """Adding a case to the suite is normal; only losing one is suspicious."""
    base = _summary(["a::m"])
    cand = _summary(["a::m", "b::m"])
    assert harness._validity_warnings(base, cand) == []


# --- compare --------------------------------------------------------------
def _full(rows, rate=100.0, overall=1.0, clean=1.0):
    return {
        "overall_score": overall, "clean_finish_rate": clean,
        "total_nudges": 0, "total_iterations": 10, "nudge_histogram": {},
        "gen_rate": rate,
        "rows": {k: {"case": k.split("::")[0], "track": "t",
                     "model": k.split("::")[1], "n": 1, "score_mean": v,
                     "score_min": v, "iterations_mean": 1.0, "nudges_mean": 0.0,
                     "clean_finish_rate": 1.0, "seconds_mean": 1.0,
                     "gen_rate_mean": rate, "stop_reasons": []}
                 for k, v in rows.items()},
    }


def test_compare_passes_on_equal_sweeps(capsys):
    s = _full({"a::m": 1.0, "b::m": 1.0})
    assert harness.compare(s, _full({"a::m": 1.0, "b::m": 1.0})) == 0
    assert "PASS" in capsys.readouterr().out


def test_compare_fails_on_a_real_regression(capsys):
    base = _full({"a::m": 1.0, "b::m": 1.0}, overall=1.0)
    cand = _full({"a::m": 0.2, "b::m": 1.0}, overall=0.6)
    assert harness.compare(base, cand) == 1
    assert "FAIL" in capsys.readouterr().out


def test_compare_is_inconclusive_not_failing_on_a_partial_sweep(capsys):
    """The bug this whole check exists for: an interrupted sweep scored 0.591
    against 0.857 and reported FAIL, when it had no standing to report at all."""
    base = _full({"a::m": 1.0, "b::m": 1.0}, overall=0.857)
    cand = _full({"a::m": 0.2}, overall=0.591)
    assert harness.compare(base, cand) == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert "NOT as a verdict" in out


def test_compare_is_inconclusive_when_the_box_was_slow(capsys):
    base = _full({"a::m": 1.0}, rate=106.0, overall=1.0)
    cand = _full({"a::m": 0.1}, rate=11.0, overall=0.1)
    assert harness.compare(base, cand) == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out
