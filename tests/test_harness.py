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


def _run(case="c", model="m", score=1.0, invalid="", **metrics):
    return harness.RunResult(
        case=case, track="t", model=model, repeat=1, score=score,
        checks={}, metrics=metrics, returncode=0, timed_out=False,
        seconds=1.0, workdir="/tmp", invalid=invalid)


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
def _summary(rows, rate=None, gen_chars=100_000):
    # gen_chars defaults high so the throughput floor (which now also requires
    # enough generation to trust the rate) isn't suppressed by these fixtures;
    # the short-no-op case sets it low explicitly.
    return {"rows": {k: {"n": 1} for k in rows}, "gen_rate": rate,
            "gen_chars": gen_chars}


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


def test_missing_baseline_rate_skips_the_RELATIVE_check_but_not_the_floor():
    """Superseded an earlier assertion that this produced no warning at all.
    That encoded the gap the absolute floor was added to close: with no baseline
    throughput there is nothing to compare against, but 1 char/s is self-evidently
    a broken box and needs no comparison to say so."""
    base = _summary(["a::m"], rate=None)
    cand = _summary(["a::m"], rate=1.0)
    warns = harness._validity_warnings(base, cand)
    assert len(warns) == 1
    assert "floor" in warns[0]
    assert "vs the baseline's" not in warns[0]


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


# --- variance-aware gate (per-run scores present) -------------------------
def _scored(rows, rate=100.0):
    """Like _full but each row carries per-run `scores`; overall is the pooled
    mean of every run, matching what summarize() emits."""
    allscores = [s for v in rows.values() for s in v]
    overall = round(sum(allscores) / len(allscores), 3)
    return {
        "overall_score": overall,
        "clean_finish_rate": 1.0,
        "total_nudges": 0, "total_iterations": 10, "nudge_histogram": {},
        "gen_rate": rate,
        "rows": {k: {"case": k.split("::")[0], "track": "t",
                     "model": k.split("::")[1], "n": len(v),
                     "score_mean": round(sum(v) / len(v), 3),
                     "score_min": min(v), "scores": v,
                     "iterations_mean": 1.0, "nudges_mean": 0.0,
                     "clean_finish_rate": 1.0, "seconds_mean": 1.0,
                     "gen_rate_mean": rate, "stop_reasons": []}
                 for k, v in rows.items()},
    }


def test_variance_gate_hard_fails_a_clean_stable_drop(capsys):
    """Both sweeps internally consistent, CIs separated: a real regression."""
    base = _scored({"a::m": [1.0] * 6})
    cand = _scored({"a::m": [0.5] * 6})
    assert harness.compare(base, cand) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "internally consistent" in out


def test_variance_gate_reviews_but_passes_a_noisy_drop(capsys):
    """The real r17->r20 shape: base mostly-1.0 with a blip, candidate a tight
    low band. Separated CIs but a noisy baseline — advisory REVIEW, PASS(0),
    because per-sweep drift up to ~0.4 happens under identical code."""
    base = _scored({"a::m": [1.0, 1.0, 1.0, 1.0, 0.5, 1.0]})
    cand = _scored({"a::m": [0.5, 0.333, 0.5, 0.5, 0.333, 0.5]})
    assert harness.compare(base, cand) == 0
    out = capsys.readouterr().out
    assert "REVIEW" in out
    assert "sampling drift" in out


def test_variance_gate_does_not_flag_same_code_noise(capsys):
    """The r18-vs-r19 same-code pair (0.72 -> 0.33) must NOT hard-fail — that is
    exactly the non-stationarity this gate exists to tolerate."""
    base = _scored({"a::m": [1.0, 0.167, 1.0, 0.167, 1.0, 1.0]})
    cand = _scored({"a::m": [0.333] * 6})
    assert harness.compare(base, cand) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_variance_gate_passes_an_improvement(capsys):
    base = _scored({"a::m": [1.0, 1.0, 1.0, 1.0, 1.0, 0.5]})
    cand = _scored({"a::m": [1.0] * 6})
    assert harness.compare(base, cand) == 0
    out = capsys.readouterr().out
    assert "REVIEW" not in out
    assert "PASS" in out


def test_variance_gate_ignores_a_tiny_drop(capsys):
    base = _scored({"a::m": [1.0] * 6})
    cand = _scored({"a::m": [0.95] * 6})
    assert harness.compare(base, cand) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_variance_gate_overall_backstop_fails_a_broad_stable_drop(capsys):
    """No single row crosses the per-row floor (each drops 0.07 < 0.10), but all
    three stable rows slide together and the pooled permutation clears the 0.05
    overall floor — the broad-mild-degradation the backstop exists to catch."""
    base = _scored({"a::m": [1.0] * 6, "b::m": [1.0] * 6, "c::m": [1.0] * 6})
    cand = _scored({"a::m": [0.93] * 6, "b::m": [0.93] * 6, "c::m": [0.93] * 6})
    assert harness.compare(base, cand) == 1
    out = capsys.readouterr().out
    assert "pooled drop" in out
    # and no per-row line was itself called a regression
    assert "internally consistent" not in out


def test_variance_gate_overall_backstop_excludes_review_rows(capsys):
    """A single wildly-noisy REVIEW row must not drive a hard overall FAIL on its
    own — it is excluded from the pool, so the verdict is PASS-with-REVIEW."""
    base = _scored({"a::m": [1.0, 1.0, 1.0, 1.0, 0.5, 1.0]})
    cand = _scored({"a::m": [0.4, 0.3, 0.4, 0.3, 0.4, 0.3]})
    assert harness.compare(base, cand) == 0
    out = capsys.readouterr().out
    assert "REVIEW" in out
    assert "pooled drop" not in out


def test_bootstrap_ci_is_degenerate_on_constant_input():
    assert harness._bootstrap_ci([0.5] * 6) == (0.5, 0.5)


def test_permutation_p_is_one_when_candidate_did_not_drop():
    p, drop = harness._permutation_drop_p([0.5] * 6, [0.9] * 6)
    assert p == 1.0
    assert drop < 0


# --- absolute throughput floor --------------------------------------------
def test_absolute_floor_fires_without_a_comparable_baseline():
    """The relative check needs a baseline that recorded throughput, and no
    sweep before 2026-07-22 did — so against every existing baseline it silently
    skips. The floor is the check that actually fires on a degraded run."""
    base = _summary(["a::m"], rate=None)
    cand = _summary(["a::m"], rate=11.0)
    warns = harness._validity_warnings(base, cand)
    assert len(warns) == 1
    assert "floor" in warns[0]


def test_a_healthy_rate_does_not_trip_the_floor():
    base = _summary(["a::m"], rate=None)
    cand = _summary(["a::m"], rate=harness.MIN_GEN_RATE + 1)
    assert harness._validity_warnings(base, cand) == []


def test_relative_check_wins_when_both_would_trip():
    """Both apply at 11 vs 106, but the relative message is strictly more
    informative, so it should be the one reported — and only once."""
    base = _summary(["a::m"], rate=106.0)
    cand = _summary(["a::m"], rate=11.0)
    warns = harness._validity_warnings(base, cand)
    assert len(warns) == 1
    assert "vs the baseline's" in warns[0]


def test_floor_is_skipped_when_throughput_is_unknown():
    base = _summary(["a::m"], rate=100.0)
    cand = _summary(["a::m"], rate=None)
    assert harness._validity_warnings(base, cand) == []


# --- power preflight ------------------------------------------------------
def test_power_state_reads_ac(monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "darwin")
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "Now drawing from 'AC Power'\n"})())
    assert harness._power_state()[0] is True


def test_power_state_reads_battery_with_percentage(monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "darwin")
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "Now drawing from 'Battery Power'\n"
                            " -InternalBattery-0\t14%; discharging; 0:21\n"})())
    on_ac, desc = harness._power_state()
    assert on_ac is False
    assert "14%" in desc


def test_power_state_is_unknown_off_macos(monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "linux")
    assert harness._power_state()[0] is None


def test_power_state_survives_a_missing_pmset(monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "darwin")
    def boom(*a, **k):
        raise OSError("no pmset")
    monkeypatch.setattr(harness.subprocess, "run", boom)
    assert harness._power_state()[0] is None


def test_unknown_power_state_never_blocks_a_sweep(monkeypatch):
    """A box that can't report power must still be able to run evals — the
    preflight refuses only on a POSITIVE battery reading."""
    monkeypatch.setattr(harness.sys, "platform", "linux")
    on_ac, _ = harness._power_state()
    assert on_ac is not False


def test_a_transport_death_is_not_a_clean_finish():
    # r8: mlx-server dropped the connection mid-document on two runs. The turn
    # ended without reaching any stop-detector, so `stopped is None` and both
    # scored a perfect clean finish — the row that produced nothing at all
    # carried the sweep's best clean-finish number.
    h = _load_harness()
    m = h.metrics_from_events([
        {"phase": "turn_start"},
        {"phase": "assistant_end", "chars": 32654},
        {"phase": "turn_end", "result": "(no result)"},
        {"phase": "error", "text": "Server disconnected without sending a response."},
    ])
    assert m["clean_finish"] is False
    assert m["infra_error"].startswith("infrastructure: Server disconnected")
    # And it reads as a stop reason, so the report names it instead of leaving
    # a blank row for someone to go spelunking in the event log for.
    assert "Server disconnected" in m["stop_reason"]


def test_an_ordinary_finished_turn_is_still_clean():
    h = _load_harness()
    m = h.metrics_from_events([
        {"phase": "turn_start"},
        {"phase": "assistant_end", "chars": 120},
        {"phase": "turn_end", "result": "Done."},
    ])
    assert m["clean_finish"] is True
    assert m["infra_error"] is None
    assert m["stop_reason"] is None


def test_a_detector_stop_still_wins_the_stop_reason():
    h = _load_harness()
    m = h.metrics_from_events([
        {"phase": "stopped", "reason": "the model repeated the same tool call"},
        {"phase": "turn_end", "result": "⏹ stopped"},
        {"phase": "error", "text": "All connection attempts failed"},
    ])
    assert m["clean_finish"] is False
    assert m["stop_reason"] == "the model repeated the same tool call"


# --- throttle floor only fires on trustworthy generation ------------------
def test_rate_is_trustworthy_true_for_sustained_generation():
    s = _summary(["a::m", "b::m"], rate=45.8, gen_chars=34_000)  # ~17k/run
    assert harness._rate_is_trustworthy(s) is True


def test_rate_is_trustworthy_false_for_short_noop_runs():
    # r23 stall: 6 runs, ~205 chars each — the rate is latency-dominated, not a
    # throttled box (the concurrent e2e sweep hit 45.8 ch/s on the same box).
    s = {"rows": {"exec-stall-trap::devstral24": {"n": 6}},
         "gen_rate": 13.6, "gen_chars": 1230}
    assert harness._rate_is_trustworthy(s) is False


def test_absolute_floor_suppressed_for_short_noop_candidate():
    # Low rate but almost nothing generated: do not cry "throttled box".
    base = _summary(["a::m"], rate=None)
    cand = _summary(["a::m"], rate=12.0, gen_chars=205)
    assert harness._validity_warnings(base, cand) == []
    # Same low rate, but with real generation behind it -> still flagged.
    cand_real = _summary(["a::m"], rate=12.0, gen_chars=50_000)
    warns = harness._validity_warnings(base, cand_real)
    assert len(warns) == 1 and "floor" in warns[0]


def test_summarize_records_total_gen_chars():
    runs = [_run(gen_chars=1000, gen_seconds=10.0),
            _run(gen_chars=2000, gen_seconds=20.0)]
    s = harness.summarize(runs)
    assert s["gen_chars"] == 3000


# --- ungraded runs: infra kill vs model failure ---------------------------
# The conflation this section pins down: a run the harness could not grade used
# to score 0.0 and average straight into the sweep mean, which is exactly what a
# genuine model failure looks like. A contended box then reads as a code
# regression, and a good change gets reverted.

def test_a_checker_timeout_is_not_a_model_failure():
    # ctx.bash() raising TimeoutExpired on a 180s pytest: the harness cannot
    # tell "the model wrote hanging code" from "the box was busy".
    reason = harness._invalidity(
        error="checker raised: TimeoutExpired: Command '...' timed out",
        checks={}, has_checker=True, infra_error=None)
    assert reason
    assert harness._invalid_kind(reason) == "checker raised"


def test_a_case_with_no_checker_is_not_a_zero():
    reason = harness._invalidity(error="", checks={}, has_checker=False,
                                 infra_error=None)
    assert harness._invalid_kind(reason) == "no checker"


def test_a_transport_death_invalidates_even_with_checks_present():
    # The turn died mid-flight, so the checks describe a truncated run.
    reason = harness._invalidity(error="", checks={"a": True}, has_checker=True,
                                 infra_error="connection reset")
    assert harness._invalid_kind(reason) == "infrastructure"


def test_a_real_verdict_is_valid():
    assert harness._invalidity(error="", checks={"a": True, "b": False},
                               has_checker=True, infra_error=None) == ""


def test_all_false_checks_are_a_real_verdict_not_an_infra_kill():
    # The model genuinely failed every check. That IS a zero.
    assert harness._invalidity(error="", checks={"a": False, "b": False},
                               has_checker=True, infra_error=None) == ""


def test_summarize_excludes_ungraded_runs_from_the_mean():
    # Three runs scored 1.0, one infra-killed. The old behaviour averaged the
    # kill in as a 0.0 and reported 0.75.
    runs = [_run(score=1.0), _run(score=1.0), _run(score=1.0),
            _run(score=0.0, invalid="infrastructure: connection reset")]
    s = harness.summarize(runs)
    assert s["overall_score"] == 1.0
    assert s["n_valid"] == 3 and s["invalid_runs"] == 1
    assert s["invalid_rate"] == 0.25
    assert s["invalid_reasons"] == {"infrastructure": 1}
    assert s["rows"]["c::m"]["n"] == 3
    assert s["rows"]["c::m"]["n_invalid"] == 1


def test_a_row_where_nothing_could_be_graded_reads_as_unmeasured():
    runs = [_run(score=0.0, invalid="no checker: x"),
            _run(score=0.0, invalid="no checker: x")]
    row = harness.summarize(runs)["rows"]["c::m"]
    # None, not 0.0 — "not measured" must not look like "measured and failed".
    assert row["n"] == 0 and row["n_invalid"] == 2
    assert row["score_mean"] is None
    assert row["seconds_mean"] is None
    assert row["clean_finish_rate"] is None
    assert row["scores"] == []


def test_report_prints_a_dash_not_a_zero_for_an_ungraded_row(capsys):
    s = harness.summarize([_run(score=0.0, invalid="infrastructure: boom"),
                           _run(case="d", score=1.0)])
    harness.print_report(s)
    out = capsys.readouterr().out
    assert "ungraded" in out
    # The ungraded row's cells are dashes; a 0.00 there would read as a score.
    row = next(ln for ln in out.splitlines() if ln.startswith("c "))
    assert "0.00" not in row and "-" in row
    # The row that WAS graded still prints its number.
    assert "1.00" in next(ln for ln in out.splitlines() if ln.startswith("d "))


def test_a_sweep_with_nothing_graded_reports_no_headline_score(capsys):
    harness.print_report(
        harness.summarize([_run(score=0.0, invalid="infrastructure: boom")]))
    out = capsys.readouterr().out
    assert "overall score      : n/a" in out
    assert "0.000" not in out


def test_summarize_is_silent_when_every_run_was_graded(capsys):
    harness.print_report(harness.summarize([_run(score=1.0)]))
    assert "ungraded" not in capsys.readouterr().out


def test_gen_rate_still_counts_ungraded_runs():
    # Throughput measures the BOX. A run the checker could not grade still
    # generated tokens, and excluding it would blind the throttle check exactly
    # when things are going wrong.
    runs = [_run(score=1.0, gen_chars=1000, gen_seconds=10.0),
            _run(score=0.0, invalid="infrastructure: boom",
                 gen_chars=2000, gen_seconds=20.0)]
    s = harness.summarize(runs)
    assert s["gen_chars"] == 3000
    assert s["gen_rate"] == 100.0


# --- the gate's response to ungraded runs ---------------------------------

def test_gate_is_inconclusive_when_too_much_of_a_sweep_is_ungraded(capsys):
    base = _full({"a::m": 1.0})
    cand = _full({"a::m": 0.2}, overall=0.2)
    cand.update(invalid_rate=0.5, invalid_runs=3, n_runs=6,
                invalid_reasons={"infrastructure": 3})
    assert harness.compare(base, cand) == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_a_few_ungraded_runs_do_not_block_the_gate():
    base = _full({"a::m": 1.0})
    cand = _full({"a::m": 1.0})
    cand.update(invalid_rate=0.1, invalid_runs=1, n_runs=10,
                invalid_reasons={"checker raised": 1})
    assert harness._validity_warnings(base, cand) == []


def test_an_all_ungraded_row_is_inconclusive_never_a_hard_fail(capsys):
    # The inversion this prevents: a row the harness could not grade at all
    # being read as a score of nothing, i.e. a catastrophic regression.
    base = _full({"a::m": 1.0, "b::m": 1.0})
    cand = _full({"a::m": 1.0, "b::m": 1.0})
    cand["rows"]["b::m"].update(n=0, n_invalid=4, score_mean=None,
                                score_min=None,
                                invalid_reasons={"infrastructure": 4})
    assert harness.compare(base, cand) == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert "NO run could be graded" in out


def test_summaries_without_the_field_are_treated_as_fully_graded():
    # Sweeps recorded before `invalid` existed carry no rate; they must compare
    # exactly as they always did rather than trip the new check.
    base, cand = _full({"a::m": 1.0}), _full({"a::m": 1.0})
    assert "invalid_rate" not in base
    assert harness._validity_warnings(base, cand) == []


def test_a_legacy_result_row_loads_with_a_default_of_valid():
    # RunResult(**raw) over a results.json written before the field existed.
    raw = {"case": "c", "track": "t", "model": "m", "repeat": 1, "score": 1.0,
           "checks": {}, "metrics": {}, "returncode": 0, "timed_out": False,
           "seconds": 1.0, "workdir": "/tmp"}
    assert harness.RunResult(**raw).invalid == ""


# --- small-n uncertainty (2.1) --------------------------------------------
# The r12 false positive: a baseline of 3 identical runs has zero OBSERVED
# variance, so every interval built from its own samples is zero-width and the
# gate reads it as certainty. These pin the floor that fixes it without
# blunting the genuine regressions the gate already caught.

def test_a_tiny_all_identical_baseline_does_not_hard_fail(capsys):
    """r12's exact shape: n=3, 3/3 identical, against a larger, slightly lower
    candidate. Before the floor this was a zero-width CI and a hard FAIL."""
    base = _scored({"a::m": [1.0] * 3})
    cand = _scored({"a::m": [0.8] * 8})
    assert harness.compare(base, cand) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_the_ci_floor_shrinks_with_n():
    # This is what lets the floor coexist with the tuned n=6 behaviour: it is
    # wide enough at n=3 to swallow r12's drop, narrow enough at n=8 not to.
    w3 = harness._score_ci([1.0] * 3)
    w8 = harness._score_ci([1.0] * 8)
    assert (1.0 - w3[0]) > (1.0 - w8[0]) > 0.0


def test_a_constant_sample_never_gets_a_zero_width_gate_interval():
    lo, hi = harness._score_ci([0.5] * 4)
    assert lo < 0.5 < hi
    # ...while the raw empirical CI is still honestly zero-width.
    assert harness._bootstrap_ci([0.5] * 4) == (0.5, 0.5)


def test_gate_intervals_stay_inside_the_score_range():
    lo, hi = harness._score_ci([1.0] * 3)
    assert 0.0 <= lo and hi <= 1.0


def test_too_few_runs_can_only_review_never_fail(capsys):
    # A big drop, both sweeps internally consistent — but on 3 runs each.
    base = _scored({"a::m": [1.0] * 3})
    cand = _scored({"a::m": [0.3] * 3})
    assert harness.compare(base, cand) == 0
    out = capsys.readouterr().out
    assert "REVIEW" in out
    assert "too few runs" in out
    assert "FAIL" not in out


def test_lopsided_n_can_only_review_never_fail(capsys):
    # Equal-variance, big drop, enough runs on both sides — but 4 vs 12 is not
    # a like-for-like comparison.
    base = _scored({"a::m": [1.0] * 4})
    cand = _scored({"a::m": [0.3] * 12})
    info = harness._classify_row([1.0] * 4, [0.3] * 12)
    assert info["thin"] and info["status"] == "review"
    assert harness.compare(base, cand) == 0
    assert "too few runs, or too uneven" in capsys.readouterr().out


def test_a_comparable_pair_at_the_minimum_n_still_fails(capsys):
    # n=4 vs n=4 is the smallest comparison the gate will still act on.
    base = _scored({"a::m": [1.0] * 4})
    cand = _scored({"a::m": [0.4] * 4})
    assert harness.compare(base, cand) == 1
    assert "FAIL" in capsys.readouterr().out


# --- _nudge_bucket --------------------------------------------------------
#
# The histogram ab.py prints at the end of every sweep is read as evidence, so
# a reason that lands in the wrong bucket is a wrong finding. The fallthrough
# bucket is "malformed" — an unparseable tool call — and for a long stretch
# every nudge added after the bucket list was written fell into it.

import re  # noqa: E402  (kept next to the tests that need it)


def _loop_nudge_reasons() -> set[str]:
    """Every literal `reason` the agent loop emits on a nudge event."""
    src = (ROOT / "locode" / "agent" / "loop.py").read_text()
    out = set()
    for m in re.finditer(r'"phase":\s*"nudge",\s*"reason":\s*(f?)"([^"]*)"', src):
        prefix, text = m.groups()
        # An f-string reason is a fixed stem plus a runtime detail; the stem is
        # what has to bucket, so keep the part before the first placeholder.
        out.add(text.split("{")[0].strip() if prefix else text)
    return {r for r in out if r}


def test_every_nudge_reason_has_a_bucket():
    # The one nudge with a free-text reason is the malformed-tool-call one,
    # which does not appear as a literal here — so nothing found in the loop
    # should land in the malformed bucket.
    reasons = _loop_nudge_reasons()
    assert len(reasons) > 10, "the reason scrape found almost nothing"
    misfiled = sorted(r for r in reasons
                      if harness._nudge_bucket(r) == "malformed")
    assert not misfiled, (
        "these nudges are being counted as unparseable tool calls; add them "
        f"to _NUDGE_BUCKETS: {misfiled}")


def test_a_real_parse_failure_still_buckets_as_malformed():
    assert harness._nudge_bucket("no ```tool block found") == "malformed"
    assert harness._nudge_bucket("expecting ',' delimiter") == "malformed"


def test_an_empty_reason_is_other_not_malformed():
    assert harness._nudge_bucket("") == "other"


def test_the_same_failure_family_collapses_to_one_bucket():
    assert (harness._nudge_bucket("same failure (2 runs in a row)")
            == harness._nudge_bucket("same failure (4 runs in a row)")
            == "same failure")


def test_missing_deliverable_keeps_its_bucket_despite_the_filename():
    assert harness._nudge_bucket("missing deliverable: notes.md") == \
        "missing deliverable"


# --- server fingerprint ------------------------------------------------------
# A restart between two sweeps moved clean finishes 40+ points with the model,
# alias, weights, temperature and case all held fixed, and ab.json recorded
# nothing that could have caught it. These pin the identity check that does.

_PS = (
    "  PID                  STARTED COMMAND\n"
    "  501 Sat Aug  2 09:00:00 2026 /usr/sbin/coreaudiod\n"
    " 8412 Sat Aug  8 16:18:10 2026 /opt/homebrew/opt/python/bin/python3.11 "
    "/opt/homebrew/bin/mlx_lm.server --model "
    "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit --host 127.0.0.1 "
    "--port 8081 --max-tokens 8192 --prompt-cache-size 4\n"
)


def test_the_server_fingerprint_reads_pid_start_and_model():
    fp = harness._parse_server_ps(_PS)
    assert fp["pid"] == "8412"
    # `ps` space-pads single-digit days; the fingerprint stores the whitespace-
    # normalized form so the same process compares equal on the 8th and 18th.
    assert fp["started"] == "Sat Aug 8 16:18:10 2026"
    assert fp["model"] == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"


def test_the_fingerprint_keeps_the_whole_argv():
    # The Aug-8 restart differed from its predecessor in cache flags, not the
    # model, so dropping argv would have thrown away the only visible change.
    assert "--prompt-cache-size 4" in harness._parse_server_ps(_PS)["argv"]


def test_a_space_padded_day_number_does_not_shift_the_columns():
    fp = harness._parse_server_ps(_PS.replace("Aug  8", "Aug 18"))
    assert fp["started"] == "Sat Aug 18 16:18:10 2026"
    assert fp["model"].endswith("Qwen2.5-Coder-14B-Instruct-4bit")


def test_no_server_running_is_none_not_a_crash():
    assert harness._parse_server_ps("  PID STARTED COMMAND\n") is None
    assert harness._parse_server_ps("") is None


def test_a_grep_for_the_server_is_not_mistaken_for_the_server():
    line = " 9001 Sat Aug  8 16:18:10 2026 grep mlx_lm.server\n"
    assert harness._parse_server_ps(line) is None


def test_same_server_is_true_only_for_the_same_process():
    a = harness._parse_server_ps(_PS)
    assert harness.same_server(a, dict(a)) is True
    assert harness.same_server(a, {**a, "pid": "9999"}) is False


def test_a_recycled_pid_at_a_new_start_time_is_a_different_server():
    a = harness._parse_server_ps(_PS)
    assert harness.same_server(a, {**a, "started": "Sun Aug  9 01:02:03 2026"}) \
        is False


def test_an_unknowable_comparison_is_none_not_false():
    # None must not read as "different" — an old sweep with no recorded server
    # is unknown, and warning about it would train me to ignore the warning.
    a = harness._parse_server_ps(_PS)
    assert harness.same_server(a, None) is None
    assert harness.same_server(None, None) is None


def test_server_fingerprint_accepts_injected_ps_text():
    assert harness.server_fingerprint(_PS)["pid"] == "8412"


# --- armstats: the 3rd-call opening (5.48) -----------------------------------
def _load_armstats():
    spec = importlib.util.spec_from_file_location(
        "eval_armstats", ROOT / "evals" / "armstats.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_armstats"] = mod
    spec.loader.exec_module(mod)
    return mod


armstats = _load_armstats()


def _calls(*names):
    return [{"phase": "run", "name": n} for n in names]


def test_the_opening_is_the_third_tool_call():
    ev = _calls("bash", "update_plan", "edit_file", "read_file")
    assert armstats._opening(ev) == "edit_file"


def test_the_opening_ignores_non_call_events_between_the_calls():
    # Real logs interleave iteration/result/nudge phases; counting those would
    # slide the index and silently mislabel every run.
    ev = [{"phase": "run", "name": "bash"},
          {"phase": "result", "content": "..."},
          {"phase": "iteration"},
          {"phase": "run", "name": "update_plan"},
          {"phase": "result", "content": "..."},
          {"phase": "run", "name": "read_file"}]
    assert armstats._opening(ev) == "read_file"


def test_a_run_too_short_to_have_a_third_call_has_no_opening():
    assert armstats._opening(_calls("bash", "update_plan")) is None
    assert armstats._opening([]) is None
