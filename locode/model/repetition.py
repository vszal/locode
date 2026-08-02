"""Detect degenerate, runaway repetition in a model's streamed output.

Local models sometimes fall into a token-level attractor and emit one giant
reply that repeats a short unit forever — e.g. `…megahypermegahyper…universal
(…universe, etc.).` over and over — until it burns the whole `max_tokens` or the
turn's wallclock. The agent's stuck-detectors all key on the signature of a
*completed* reply or tool call, so they run only BETWEEN completions and never
see a single reply that loops internally. This does, on the token stream, so the
client can cut the generation off early instead of paying for thousands of
wasted tokens.

The bar for `is_runaway_repetition` is deliberately conservative: it fires only
when the tail is a short unit repeated MANY times over a LONG span, which
legitimate prose or code effectively never is. A markdown rule, a repeated
import, a table row — all fall under one of the thresholds. The cost of a false
positive is aborting a real reply, so we would rather miss a mild loop than kill
good work.
"""

from __future__ import annotations

# Only the tail matters — a loop is detected by what the model is doing NOW, not
# by repetition scattered earlier in a long, legitimate reply. Must be wide
# enough to hold MIN_REPS whole units at MAX_UNIT, or the longest loops can
# never accumulate enough repeats to be seen (see below).
WINDOW = 8000
# Largest repeating unit we look for. Real degeneration loops at three scales: a
# short phrase (`megahyper`, ~9 chars), a sentence template (~300 chars), and a
# whole multi-paragraph ANALYSIS BLOCK — the qythos9 case that motivated raising
# this was a 932-char unit ("Based on the error and the context… Let me look at
# the exact code") repeated verbatim. At 700 that loop was rejected outright for
# being too long, and even lifting the cap alone would not have helped: a
# 2000-char window holds only 2.15 reps of a 932-char unit, so MIN_REPS=4 was
# arithmetically unreachable. Both had to move together.
MAX_UNIT = 2000
# The repeated span must be at least this many chars AND repeat at least this
# many times. Both, so neither a short stutter nor a couple of long paragraphs
# that happen to rhyme trips it.
MIN_SPAN = 600
MIN_REPS = 4
# Confidence in a repeat scales with how long the repeating unit is: a 9-char
# unit seen 3 times is everywhere in ordinary text, while a 400+ char block
# reproduced BYTE-IDENTICALLY three times running is not something legitimate
# prose or code does — that is 1200+ chars of exact duplication. So long units
# clear at 3 reps instead of 4, which catches a paragraph-scale loop a whole
# repeat (~1 KB of wasted generation) sooner. Short units are unaffected; for
# them MIN_SPAN is the binding constraint anyway.
LONG_UNIT = 400
LONG_UNIT_MIN_REPS = 3
# Fingerprint length: the tail slice whose nearest earlier occurrence reveals the
# period. Must be short enough to sit inside one repeat of the smallest unit we
# care about, but long enough that a chance match in ordinary text is unlikely.
PROBE = 48
# The streaming caller re-checks only every this-many newly generated chars —
# the scan is cheap but not free, and a loop that has already run MIN_SPAN chars
# is not made more urgent by catching it a few hundred chars sooner.
CHECK_STRIDE = 250


def is_runaway_repetition(text: str) -> bool:
    """True when the tail of `text` is a unit repeated enough to be a degenerate
    loop rather than legitimate content.

    Fingerprints the last `PROBE` chars and finds their nearest earlier
    occurrence; the gap is the loop's period. Reports a runaway only when that
    period is at most `MAX_UNIT`, the unit carries real (non-whitespace) content,
    and it repeats enough times (`reps_required`, which eases for long units)
    across at least `MIN_SPAN` characters. Deriving the period from the data
    (rather than scanning every candidate length) keeps this cheap enough to call
    per token-batch even with a wide `MAX_UNIT`.
    """
    tail = text[-WINDOW:]
    n = len(tail)
    if n < MIN_SPAN:
        return False
    probe = tail[-PROBE:]
    prev = tail.rfind(probe, 0, n - PROBE)  # nearest earlier occurrence
    if prev < 0:
        return False
    p = (n - PROBE) - prev  # distance between the two occurrences = the period
    if p <= 0 or p > MAX_UNIT:
        return False
    unit = tail[-p:]
    if not unit.strip():
        return False  # a run of pure whitespace is not the loop we care about
    reps = 1
    i = n - 2 * p
    while i >= 0 and tail[i:i + p] == unit:
        reps += 1
        i -= p
    return reps >= reps_required(p) and reps * p >= MIN_SPAN


def reps_required(period: int) -> int:
    """How many verbatim repeats it takes to call a loop, given its unit size.

    Long units need fewer: the evidence is the *volume* of byte-identical text,
    and three reps of a 400-char block is already twice the duplication that four
    reps of a 150-char one gives.
    """
    return LONG_UNIT_MIN_REPS if period >= LONG_UNIT else MIN_REPS
