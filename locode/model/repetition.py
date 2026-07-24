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
# by repetition scattered earlier in a long, legitimate reply.
WINDOW = 2000
# Largest repeating unit we look for. Real degeneration loops at two scales: a
# short phrase (`megahyper`, ~9 chars) and a whole sentence template (~300+
# chars), so this has to reach well past a single line.
MAX_UNIT = 700
# The repeated span must be at least this many chars AND repeat at least this
# many times. Both, so neither a short stutter nor a couple of long paragraphs
# that happen to rhyme trips it.
MIN_SPAN = 600
MIN_REPS = 4
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
    and it repeats at least `MIN_REPS` times across at least `MIN_SPAN`
    characters. Deriving the period from the data (rather than scanning every
    candidate length) keeps this cheap enough to call per token-batch even with a
    wide `MAX_UNIT`.
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
    return reps >= MIN_REPS and reps * p >= MIN_SPAN
