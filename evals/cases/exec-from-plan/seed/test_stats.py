"""Tests for stats.py. The mean tests pass immediately; the rest fail
until median, mode, and summary are implemented per PLAN.md."""

import pytest

from stats import mean, median, mode, summary


def test_mean_basic():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_single_value():
    assert mean([7]) == 7.0


def test_mean_empty_raises():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd_length():
    assert median([5, 1, 3]) == 3.0


def test_median_even_length():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_unsorted_even_length():
    assert median([10, 2, 8, 4]) == 6.0


def test_median_does_not_mutate_input():
    values = [3, 1, 2]
    median(values)
    assert values == [3, 1, 2]


def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])


def test_mode_basic():
    assert mode([1, 2, 2, 3]) == 2


def test_mode_tie_returns_smallest():
    # 2 and 5 both occur twice; smallest tied value wins.
    assert mode([5, 5, 2, 2, 9]) == 2


def test_mode_all_unique_returns_smallest():
    # every value occurs exactly once, so all are "tied" at count 1.
    assert mode([9, 4, 7]) == 4


def test_mode_empty_raises():
    with pytest.raises(ValueError):
        mode([])


def test_summary_keys_and_values():
    result = summary([1, 2, 2, 4])
    assert result == {
        "n": 4,
        "mean": 2.25,
        "median": 2.0,
        "mode": 2,
        "min": 1,
        "max": 4,
    }


def test_summary_empty_raises():
    with pytest.raises(ValueError):
        summary([])
