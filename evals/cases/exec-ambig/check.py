"""Checks for the exec-ambig case.

Same structure and same guards as exec-bugfix's check, so the cases differ in
one respect only: here the bugs sit on DUPLICATED lines.

Why this case exists (ROADMAP 5.55). b120 tried to A/B the ambiguous-match
message on exec-bugfix and could not: the message fires only in the read-first
branch, that branch is ~24% of runs, and the branch draw (6 vs 1 across two
arms of 14) dominates the VERIFIED metric it would be graded on. The lever
fired in ONE of fourteen candidate runs. No amount of extra r on exec-bugfix
fixes that — the instrument has to make the message fire.

The three seeded bugs are copy-paste errors, each on a line that is CORRECT
somewhere else in the file:

  * clamp_byte   `return 100` should be `return 255` — that exact line occurs 2x
  * clamp_ratio  `return 0`   should be `return 1`   — that exact line occurs 4x
  * clamp_signed `return 128` should be `return 127` — unique, the control bug

So any `old` the model copies off a failing line is ambiguous by construction,
and `replace_all` is an active trap rather than a shortcut: applying it to
`return 100` fixes clamp_byte and breaks clamp_percent in the same edit, for
net zero progress. Verified before landing — targeted fixes reach 13/13, and
the replace_all route stays at 3 failed.

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
