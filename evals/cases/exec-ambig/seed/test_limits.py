"""Tests for limits.py. Exactly three of these are expected to fail
against a buggy implementation; the rest must pass unchanged."""

from limits import clamp_percent, clamp_byte, clamp_ratio, clamp_signed


def test_percent_inside_the_range_is_unchanged():
    assert clamp_percent(42) == 42


def test_percent_below_the_range_becomes_zero():
    assert clamp_percent(-5) == 0


def test_percent_above_the_range_becomes_one_hundred():
    assert clamp_percent(150) == 100


def test_percent_at_the_upper_endpoint_is_unchanged():
    assert clamp_percent(100) == 100


def test_byte_inside_the_range_is_unchanged():
    assert clamp_byte(200) == 200


def test_byte_below_the_range_becomes_zero():
    assert clamp_byte(-1) == 0


def test_byte_above_the_range_becomes_two_five_five():
    assert clamp_byte(300) == 255


def test_ratio_inside_the_range_is_unchanged():
    assert clamp_ratio(0.25) == 0.25


def test_ratio_below_the_range_becomes_zero():
    assert clamp_ratio(-2) == 0


def test_ratio_above_the_range_becomes_one():
    assert clamp_ratio(7) == 1


def test_signed_inside_the_range_is_unchanged():
    assert clamp_signed(5) == 5


def test_signed_below_the_range_becomes_minus_one_twenty_eight():
    assert clamp_signed(-500) == -128


def test_signed_above_the_range_becomes_one_twenty_seven():
    assert clamp_signed(500) == 127
