"""The generation-rate warning and the censoring report.

Two questions the harness must keep apart (ROADMAP §5.137, §5.159):

  Was the BOX slow?      -> `_judge_rate`, relative to the model's own history.
  Was a RUN cut off?     -> `_censored`, from the agent's own budget marker.

They were and-ed into one verdict until §5.159, which is why a healthy sweep
with four time-censored runs could only be reported as "the machine was sick".
"""
import json
from types import SimpleNamespace

import pytest

from evals.harness import (MIN_GEN_RATE, RATE_DEGRADED_FRACTION, _budget_bound,
                           _censored, _describe_degraded, _judge_rate,
                           _rate_baseline)


def _run(seconds=100.0, timed_out=False, stop_reason=None, case="c",
         model="m", repeat=1, score=0.0):
    return SimpleNamespace(seconds=seconds, timed_out=timed_out, case=case,
                           model=model, repeat=repeat, score=score,
                           metrics={"stop_reason": stop_reason})


# --------------------------------------------------------------------------
# censoring
# --------------------------------------------------------------------------

def test_no_run_out_of_time_is_not_budget_bound():
    assert _budget_bound([_run(), _run(), _run()]) is False


def test_a_timed_out_run_is_budget_bound():
    assert _budget_bound([_run(), _run(timed_out=True)]) is True


def test_the_real_wallclock_stop_reason_is_censored():
    """The agent's actual wording, copied from the archive — not a paraphrase.
    The previous version of this test asserted on invented prose ("hit the
    wallclock budget"), so it passed while saying nothing about real logs."""
    runs = [_run(stop_reason="budget: the turn's wallclock ran out while "
                             "generating (~7,107 chars into this reply)")]
    assert _budget_bound(runs) is True


def test_the_real_max_iterations_stop_reason_is_censored():
    assert _budget_bound([_run(stop_reason="budget: max iterations reached")]) is True


def test_context_exhaustion_is_censored():
    """Also a limit stopping a working model, so also an unknown, not a verdict."""
    runs = [_run(stop_reason="budget: conversation too large (~180,000 chars)")]
    assert _budget_bound(runs) is True


def test_an_ordinary_stall_stop_is_not_censored():
    runs = [_run(stop_reason="the model repeated the same tool call "
                             "without making progress")]
    assert _budget_bound(runs) is False


def test_a_giveup_mentioning_iterations_is_not_censored():
    """The §5.159 false positive. A give-up reason narrates how many iterations
    went by, so substring-matching "iteration" filed `polyglot-scale-generator`
    — a real 0.00 the model earned — as an unknown, inflating the upper bound of
    the pass rate by a case."""
    runs = [_run(stop_reason="`scale_generator_test.py::ScaleGeneratorTest::"
                             "test_harmonic_minor` still fails — 8 iterations "
                             "since the repeat was flagged changed nothing. "
                             "Stopping rather than grinding; the fix needs a "
                             "different idea")]
    assert _censored(runs) == []


def test_the_idle_timeout_is_not_censored():
    """`budget:`-prefixed but the opposite of censoring: it fires because the
    model STOPPED working, so counting it would inflate the upper bound."""
    runs = [_run(stop_reason="budget: no progress for 240s")]
    assert _censored(runs) == []


def test_censored_returns_the_runs_themselves():
    hit, ok = _run(stop_reason="budget: wallclock exceeded"), _run()
    assert _censored([ok, hit, ok]) == [hit]


def test_missing_metrics_does_not_raise():
    assert _budget_bound([SimpleNamespace(seconds=1.0, timed_out=False,
                                          metrics=None)]) is False


def test_empty_sweep_is_not_budget_bound():
    assert _budget_bound([]) is False


def test_the_real_qwen38_sweep_is_not_budget_bound():
    """138s of a 600s budget at 21.7 chars/s — the case that motivated this."""
    runs = [_run(seconds=s) for s in (159.5, 128.4, 128.2)]
    assert _budget_bound(runs) is False


# --------------------------------------------------------------------------
# the rate verdict
# --------------------------------------------------------------------------

@pytest.fixture
def archive(tmp_path, monkeypatch):
    """Write N sweeps for a model at a given rate, and point the harness at them."""
    import evals.harness as H
    monkeypatch.setattr(H, "RESULTS_DIR", tmp_path)

    def write(label, model, chars, seconds, n_runs=1):
        d = tmp_path / label
        d.mkdir()
        runs = [{"model": model,
                 "metrics": {"gen_chars": chars, "gen_seconds": seconds}}
                for _ in range(n_runs)]
        (d / "results.json").write_text(json.dumps({"label": label, "runs": runs}))
    return write


def test_baseline_is_none_without_history(archive):
    assert _rate_baseline("nobody") == (None, 0)


def test_baseline_medians_across_sweeps(archive):
    for i, rate in enumerate((10.0, 20.0, 60.0)):
        archive(f"s{i}", "m", chars=rate * 100, seconds=100.0)
    assert _rate_baseline("m") == (20.0, 3)


def test_baseline_excludes_the_sweep_being_judged(archive):
    """A degraded sweep must not vote on the baseline it is measured against.

    Asserted on the median's VALUE with two sweeps rather than on a larger set,
    because the median is deliberately robust: at three healthy sweeps plus one
    sick one it does not move at all, which would let a broken `exclude` pass."""
    archive("healthy", "m", chars=6000, seconds=100.0)
    archive("sick", "m", chars=500, seconds=100.0)
    assert _rate_baseline("m", exclude="sick") == (60.0, 1)
    assert _rate_baseline("m") == (pytest.approx(32.5), 2)


def test_baseline_pools_within_a_sweep_before_taking_the_median(archive):
    """One long run must not outvote a short one inside the same sweep: 2 runs
    of 100 chars in 1s and 1 run of 100 chars in 99s pool to 300/101, not to the
    mean of their per-run rates."""
    archive("s0", "m", chars=100, seconds=1.0, n_runs=3)
    base, n = _rate_baseline("m")
    assert n == 1 and base == pytest.approx(300 / 3.0)


def test_a_normal_rate_for_this_model_does_not_warn(archive):
    """The §5.159 regression: qwen38 pools at ~24 chars/s on a healthy box and
    has never once cleared the absolute 30 floor, so the old check condemned
    every sweep it ever ran."""
    for i in range(4):
        archive(f"s{i}", "qwen38", chars=2500, seconds=100.0)
    assert _judge_rate("qwen38", 24.1, "now") is None


def test_a_large_relative_drop_warns(archive):
    for i in range(4):
        archive(f"s{i}", "m", chars=6000, seconds=100.0)
    entry = _judge_rate("m", 60.0 * RATE_DEGRADED_FRACTION - 1, "now")
    assert entry is not None
    assert entry[0] == "m" and entry[2] == pytest.approx(60.0)


def test_thin_history_falls_back_to_the_absolute_floor(archive):
    """Two sweeps is not a baseline, it is two numbers."""
    for i in range(2):
        archive(f"s{i}", "m", chars=6000, seconds=100.0)
    assert _judge_rate("m", MIN_GEN_RATE - 1, "now")[2] is None
    assert _judge_rate("m", MIN_GEN_RATE + 1, "now") is None


def test_a_missing_rate_never_warns(archive):
    assert _judge_rate("m", None, "now") is None
    assert _judge_rate("m", 0.0, "now") is None


def test_the_message_names_what_it_compared_against():
    with_base = _describe_degraded(("m", 10.0, 40.0, 7))
    assert "25%" in with_base and "40.0 baseline" in with_base and "7" in with_base
    assert "absolute" in _describe_degraded(("m", 10.0, None, 1))
