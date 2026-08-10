"""Tests for numkit.py. Exactly three of these are expected to fail
against a buggy implementation; the rest must pass unchanged."""

from numkit import mean, median, clamp, top_n


def test_mean_of_a_few_values():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_of_empty_is_zero():
    assert mean([]) == 0.0


def test_mean_of_one_value():
    assert mean([7]) == 7.0


def test_median_odd_count_is_the_middle_value():
    assert median([5, 1, 3]) == 3


def test_median_even_count_averages_the_two_middle_values():
    # sorted -> [1, 2, 3, 4]; the two middle values are 2 and 3.
    assert median([3, 1, 4, 2]) == 2.5


def test_median_of_empty_is_zero():
    assert median([]) == 0.0


def test_clamp_leaves_a_value_inside_the_range_alone():
    assert clamp(5, 0, 10) == 5


def test_clamp_raises_a_value_below_the_range():
    assert clamp(-5, 0, 10) == 0


def test_clamp_lowers_a_value_above_the_range():
    assert clamp(15, 0, 10) == 10


def test_clamp_keeps_the_lower_endpoint():
    assert clamp(0, 0, 10) == 0


def test_top_n_returns_the_largest_counts_first():
    counts = {"a": 3, "b": 9, "c": 5}
    assert top_n(counts, 2) == ["b", "c"]


def test_top_n_of_empty_is_empty():
    assert top_n({}, 3) == []


def test_top_n_with_one_key_ignores_order():
    assert top_n({"solo": 1}, 5) == ["solo"]
