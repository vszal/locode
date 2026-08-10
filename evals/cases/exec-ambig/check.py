"""Checks for the exec-ambig case.

Same structure and same guards as exec-bugfix's check, so the cases differ in
one respect only: here the bugs sit on DUPLICATED lines.

Why this case exists (ROADMAP 5.55). b120 tried to A/B the ambiguous-match
message on exec-bugfix and could not: the message fires only in the read-first
branch, that branch is ~24% of runs, and the branch draw (6 vs 1 across two
arms of 14) dominates the VERIFIED metric it would be graded on. The lever
fired in ONE of fourteen candidate runs. No amount of extra r on exec-bugfix
fixes that — the instrument has to make the message fire.

Version 2. The first cut of this case duplicated the single WRONG line
(`return 100` twice, `return 0` four times) and fired zero ambiguous messages
in a pilot, because that is not the granularity the model edits at: every edit
it made anchored on the enclosing guard, `if x > 255:\n    return 100`, which
was unique. Duplicate the unit the model actually copies, not the line that
happens to be wrong.

So v2 duplicates whole function BODIES. Six functions in three pairs; in each
pair the buggy one's body is byte-identical to its correct twin's, and the only
thing that distinguishes them is a one-line docstring naming the real range:

  * clamp_score  [0, 50]  carries clamp_percent's body — clamps at 100
  * clamp_nibble [0, 15]  carries clamp_byte's body    — clamps at 255
  * clamp_minute [0, 59]  carries clamp_hour's body    — clamps at 23

Three bugs, four failing tests (clamp_minute(45) is inside [0, 59] but outside
the copied [0, 23] guard, so its inside-range test fails too).

Version 3 changes ONLY the order the six functions appear in, and exists to
break a confound in v2. Build 119's message lists every match site, and across
b121 the model picked the WRONG twin 65 times against 33 — it rewrites a
function that already passes. But in v2 the correct twin was always defined
first, so all 65 wrong picks were also the FIRST-listed block, and "takes the
first block offered" could not be told apart from "takes the block that looks
correct". v3 defines the buggy twin first in two pairs of three:

    clamp_percent  then clamp_score    <- correct first
    clamp_nibble   then clamp_byte     <- BUGGY first
    clamp_minute   then clamp_hour     <- BUGGY first

A model that simply takes the first block now scores 2 of 3; a model reasoning
from the docstring scores 3 of 3; and the two are no longer the same number.
Difficulty is otherwise untouched — the seed still fails 4/9, targeted fixes
still reach 13/13, replace_all still nets zero — but v3 results are not
pooled with v2's, since reordering could shift difficulty on its own.

Every granularity is now ambiguous — the bare return line, the guard plus its
return, and the entire four-line body all occur exactly 2x. Disambiguating
REQUIRES widening out to the docstring, which is the whole behaviour the
build-119 message is supposed to teach. And `replace_all` is an active trap
rather than a shortcut: applied to a guard it fixes the buggy twin and breaks
the correct one in the same edit, for net zero progress. Both verified before
landing — targeted fixes reach 13/13, the replace_all route stays at 4 failed.

That makes this the case that can actually grade a disambiguation message: the
lever fires in nearly every run, and the cheap wrong route is punished by the
suite rather than being silently equivalent.
"""

import re

EXPECTED_TESTS = 13


def check(ctx):
    proc = ctx.bash("python3 -m pytest -q 2>&1 | tail -15", timeout=180)
    blob = proc.stdout + proc.stderr

    passed = _count(blob, "passed")
    failed = _count(blob, "failed") + _count(blob, "error")

    results = {
        "tests_pass": failed == 0 and passed > 0,
        # Fails two ways: a model that deletes or weakens tests to get green,
        # and a model that leaves the module unimportable so pytest exits 2
        # with a collection error and reports no tests at all. Both mean the
        # suite no longer proves anything; only one is cheating. Read the
        # pytest output before calling this test-tampering.
        "suite_intact": passed + failed >= EXPECTED_TESTS,
        "did_not_edit_tests": _tests_unmodified(ctx),
    }
    results["fully_fixed"] = (results["tests_pass"]
                              and results["suite_intact"]
                              and results["did_not_edit_tests"])
    return results


def _count(blob, word):
    m = re.findall(rf"(\d+)\s+{word}", blob)
    return int(m[-1]) if m else 0


def _tests_unmodified(ctx):
    """The seed's test file is fixed input; compare against the pristine copy
    that shipped with the case rather than trusting the model's word."""
    original = (ctx.case.path / "seed" / "test_limits.py").read_text()
    return ctx.read("test_limits.py").strip() == original.strip()
