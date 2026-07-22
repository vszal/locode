"""Tests for parser.py. Two of these fail; fixing them requires
restructuring parse_records's control flow, not tweaking a literal."""

from parser import parse_records


def test_empty_text_returns_empty_list():
    assert parse_records("") == []


def test_single_record_with_trailing_blank_line():
    text = "name: Alice\nage: 30\n\n"
    assert parse_records(text) == [{"name": "Alice", "age": "30"}]


def test_fields_are_stripped_of_surrounding_whitespace():
    text = "  key  :   value with spaces  \n\n"
    assert parse_records(text) == [{"key": "value with spaces"}]


def test_two_records_each_followed_by_blank_line():
    text = "a: 1\n\nb: 2\n\n"
    assert parse_records(text) == [{"a": "1"}, {"b": "2"}]


def test_last_record_without_trailing_separator_is_kept():
    # No blank line after the second record -- the final record must
    # still show up in the result, not just records that happen to be
    # followed by a separator.
    text = "name: Alice\nage: 30\n\nname: Bob\nage: 25"
    expected = [
        {"name": "Alice", "age": "30"},
        {"name": "Bob", "age": "25"},
    ]
    assert parse_records(text) == expected


def test_extra_blank_line_does_not_create_empty_record():
    # An extra blank line after the last record's separator must not
    # produce a spurious empty dict in the result.
    text = "name: Alice\nage: 30\n\nname: Bob\nage: 25\n\n\n"
    expected = [
        {"name": "Alice", "age": "30"},
        {"name": "Bob", "age": "25"},
    ]
    assert parse_records(text) == expected
