"""The sub-floor generation-rate warning must distinguish a throttled box from
a model that is merely slow. See ROADMAP §5.137."""
from types import SimpleNamespace

from evals.harness import _budget_bound


def _run(seconds=100.0, timed_out=False, stop_reason=None):
    return SimpleNamespace(seconds=seconds, timed_out=timed_out,
                           metrics={"stop_reason": stop_reason})


def test_no_run_out_of_time_is_not_budget_bound():
    assert _budget_bound([_run(), _run(), _run()]) is False


def test_a_timed_out_run_is_budget_bound():
    assert _budget_bound([_run(), _run(timed_out=True)]) is True


def test_a_wallclock_stop_reason_is_budget_bound():
    assert _budget_bound([_run(stop_reason="hit the wallclock budget")]) is True


def test_stop_reason_matching_is_case_insensitive():
    assert _budget_bound([_run(stop_reason="Exceeded the Time Limit")]) is True


def test_an_ordinary_stall_stop_is_not_budget_bound():
    runs = [_run(stop_reason="the model repeated the same tool call "
                             "without making progress")]
    assert _budget_bound(runs) is False


def test_missing_metrics_does_not_raise():
    assert _budget_bound([SimpleNamespace(seconds=1.0, timed_out=False,
                                          metrics=None)]) is False


def test_empty_sweep_is_not_budget_bound():
    assert _budget_bound([]) is False


def test_the_real_qwen38_sweep_is_not_budget_bound():
    """138s of a 600s budget at 21.7 chars/s — the case that motivated this."""
    runs = [_run(seconds=s) for s in (159.5, 128.4, 128.2)]
    assert _budget_bound(runs) is False
