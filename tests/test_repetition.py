"""Tests for the runaway-repetition detector (model/repetition.py)."""

from locode.model.repetition import is_runaway_repetition


# --- positives: genuine degeneration -------------------------------------

def test_detects_a_short_phrase_repeated_many_times():
    text = "Here is my plan.\n\n" + "megahyper" * 200
    assert is_runaway_repetition(text)


def test_detects_a_sentence_level_loop():
    # The real-world case: a ~90-char template repeated verbatim.
    unit = "We will not implement any task megahyperuniversal (megahyperuniverse).\n\n"
    assert is_runaway_repetition("preamble " + unit * 20)


def test_detects_a_long_sentence_template_loop():
    # The real-world runaway: a ~330-char template (with its own inner phrase
    # repeats) repeated verbatim — longer than a single line, which a narrow
    # period scan would miss.
    unit = ("We will not implement any task supermega" + "hypermega" * 14 +
            "universal (supermega" + "hypermega" * 14 + "universe, etc.).\n\n")
    assert is_runaway_repetition("preamble text. " + unit * 6)


def test_detects_a_single_character_run():
    assert is_runaway_repetition("ok then " + "a" * 800)


def test_fires_on_the_tail_even_after_a_long_healthy_start():
    healthy = "This is a perfectly ordinary paragraph of prose. " * 20
    assert is_runaway_repetition(healthy + "loop " * 200)


# --- negatives: legitimate content ---------------------------------------

def test_ignores_ordinary_prose():
    text = (
        "The scraper reads each page and extracts the fields we care about. "
        "It writes the rows to a CSV as it goes, flushing every hundred records. "
        "On a timeout it backs off and retries, up to three times per URL. "
        "Every error is logged with the request context needed to reproduce it. "
        "When the queue drains, it prints a short summary and exits cleanly. "
        "The whole run is idempotent, so a restart never double-counts a page.")
    assert not is_runaway_repetition(text)


def test_ignores_code_with_repeated_but_distinct_lines():
    code = "".join(f"import module_{i}\n" for i in range(60))
    assert not is_runaway_repetition(code)


def test_ignores_a_short_repeat_under_the_span_floor():
    # "ha" x 100 = 200 chars, below MIN_SPAN — a stutter, not a runaway.
    assert not is_runaway_repetition("well " + "ha" * 100)


def test_ignores_a_pure_whitespace_run():
    assert not is_runaway_repetition("done." + "\n" * 900)


def test_ignores_empty_and_short_text():
    assert not is_runaway_repetition("")
    assert not is_runaway_repetition("hello world")
