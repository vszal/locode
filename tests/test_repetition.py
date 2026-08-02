"""Tests for the runaway-repetition detector (model/repetition.py)."""

from locode.model import repetition as rep
from locode.model.repetition import is_runaway_repetition, reps_required


# The actual loop a user hit on qythos9 (2026-08-02), transcribed from the
# session: a 932-char analysis block re-emitted verbatim until they pressed Esc.
# Kept whole rather than reduced to a synthetic string — the thresholds exist to
# catch THIS, and a paraphrase would not pin the length that broke them.
_QYTHOS_BLOCK = """Based on the error and the context, the issue is likely in \
the staging logic where it tries to construct the destination path. The code \
might be doing something like:

```python
dest_path = new_file.parent / "something"
```

But if new_file is a string, new_file.parent will fail.

The fix is to ensure that when we construct the destination path, we convert \
the source path to a string first, or use new_file.as_posix() to get a string \
representation.

Looking at the staging code, it's possible that the code is trying to construct \
the destination path by doing something like:

```python
dest_path = Path(str(new_file))
```

But then later, it tries to access dest_path.parent, which should work.

Actually, the issue might be that the code is trying to use new_file.parent \
directly, assuming new_file is a Path object. But if new_file is a string, this \
will fail.

Let me look at the exact code to see where this is happening.

"""


# --- the paragraph-scale loop that got through ----------------------------

def test_detects_the_qythos9_analysis_block_loop():
    # Was invisible: period 932 > the old MAX_UNIT of 700, so it was rejected
    # before repeat-counting ever ran.
    assert is_runaway_repetition(_QYTHOS_BLOCK * 4)


def test_the_window_can_hold_enough_reps_of_the_largest_unit():
    # The second, independent blocker: raising MAX_UNIT alone would not have
    # helped, because the old 2000-char window held only 2.15 reps of a 932-char
    # unit and MIN_REPS was 4 — arithmetically unreachable. Any future change to
    # these constants must keep the window able to hold what it demands.
    assert rep.WINDOW >= rep.MAX_UNIT * rep.MIN_REPS


def test_a_long_block_is_caught_at_three_reps():
    # ~1000 chars x3 is 3 KB of byte-identical text; waiting for a fourth just
    # burns another KB of generation.
    assert is_runaway_repetition(_QYTHOS_BLOCK * 3)


def test_two_reps_of_a_long_block_is_not_enough():
    # A model legitimately restating a conclusion once must not be killed.
    assert not is_runaway_repetition(_QYTHOS_BLOCK * 2)


def test_reps_required_eases_only_for_long_units():
    assert reps_required(rep.LONG_UNIT) == rep.LONG_UNIT_MIN_REPS
    assert reps_required(rep.LONG_UNIT - 1) == rep.MIN_REPS
    assert reps_required(9) == rep.MIN_REPS


def test_a_short_unit_still_needs_the_full_rep_count():
    # The easement is for LONG units only. This unit clears MIN_SPAN at 3 reps
    # (250 x 3 = 750 > 600) so only the rep count can reject it — which is the
    # point: below LONG_UNIT it still takes MIN_REPS.
    unit = ("Retrying the request now, since the previous attempt came back "
            "with a gateway timeout and the queue still has entries left to "
            "drain before the summary can be printed to the console safely, "
            "and the backoff has not yet reached its configured ceiling. ")
    assert rep.MIN_SPAN / 3 < len(unit) < rep.LONG_UNIT
    assert not is_runaway_repetition(unit * 3)
    assert is_runaway_repetition(unit * 4)


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
