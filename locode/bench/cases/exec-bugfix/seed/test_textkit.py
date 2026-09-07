"""Tests for textkit.py. Exactly three of these are expected to fail
against a buggy implementation; the rest must pass unchanged."""

from textkit import word_wrap, truncate, title_case, dedupe_spaces


def test_word_wrap_exact_fit_stays_on_one_line():
    # "abcd efgh" is exactly 9 characters -> should NOT wrap at width 9.
    assert word_wrap("abcd efgh", 9) == ["abcd efgh"]


def test_word_wrap_splits_when_over_width():
    # Each pairing here is clearly over width (11 > 8), nowhere near a
    # boundary, so this must pass regardless of any off-by-one at the edge.
    assert word_wrap("abcde fghij", 8) == ["abcde", "fghij"]


def test_word_wrap_oversized_single_word_gets_own_line():
    assert word_wrap("supercalifragilistic short", 10) == [
        "supercalifragilistic",
        "short",
    ]


def test_word_wrap_empty_text_returns_empty_list():
    assert word_wrap("", 10) == []


def test_truncate_returns_unchanged_when_it_fits():
    assert truncate("hello", 10) == "hello"


def test_truncate_shortens_and_appends_suffix_at_exact_limit():
    result = truncate("abcdefghij", 5)
    assert result == "ab..."
    assert len(result) == 5


def test_truncate_boundary_length_equal_limit():
    # len(text) == limit exactly -> returned unchanged, no truncation math.
    assert truncate("abcde", 5) == "abcde"


def test_title_case_basic_no_small_words():
    assert title_case("hello world") == "Hello World"


def test_title_case_keeps_interior_small_words_lowercase():
    # First word here ("lord") is not a small word, isolating the interior
    # small-word behavior from the first-word exception tested below.
    assert title_case("lord of the rings and me") == "Lord of the Rings and Me"


def test_title_case_first_word_always_capitalized():
    assert title_case("a tale of two cities") == "A Tale of Two Cities"


def test_dedupe_spaces_collapses_runs():
    assert dedupe_spaces("a   b\tc\n\nd") == "a b c d"


def test_dedupe_spaces_strips_ends():
    assert dedupe_spaces("   hello world   ") == "hello world"


def test_dedupe_spaces_no_change_needed():
    assert dedupe_spaces("already fine") == "already fine"
