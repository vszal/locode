"""Tests for limits.py. Exactly three of these are expected to fail
against a buggy implementation; the rest must pass unchanged."""

from limits import (clamp_percent, clamp_score, clamp_byte, clamp_nibble,
                    clamp_hour, clamp_minute)


def test_percent_inside_the_range_is_unchanged():
    assert clamp_percent(42) == 42


def test_percent_above_the_range_becomes_one_hundred():
    assert clamp_percent(150) == 100


def test_score_inside_the_range_is_unchanged():
    assert clamp_score(20) == 20


def test_score_above_the_range_becomes_fifty():
    assert clamp_score(80) == 50


def test_byte_inside_the_range_is_unchanged():
    assert clamp_byte(200) == 200


def test_byte_above_the_range_becomes_two_five_five():
    assert clamp_byte(300) == 255


def test_nibble_inside_the_range_is_unchanged():
    assert clamp_nibble(9) == 9


def test_nibble_above_the_range_becomes_fifteen():
    assert clamp_nibble(200) == 15


def test_hour_inside_the_range_is_unchanged():
    assert clamp_hour(11) == 11


def test_hour_above_the_range_becomes_twenty_three():
    assert clamp_hour(30) == 23


def test_minute_inside_the_range_is_unchanged():
    assert clamp_minute(45) == 45


def test_minute_above_the_range_becomes_fifty_nine():
    assert clamp_minute(90) == 59


def test_everything_below_the_range_becomes_zero():
    assert clamp_percent(-5) == 0
    assert clamp_score(-5) == 0
    assert clamp_byte(-1) == 0
    assert clamp_nibble(-1) == 0
    assert clamp_hour(-1) == 0
    assert clamp_minute(-1) == 0
