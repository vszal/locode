"""Checks for the exec-pinpoint case.

Identical in structure to exec-bugfix's check — green tests are the outcome,
plus the two guards against faking it (editing the tests, or declaring victory
in prose without the suite passing). Kept deliberately the same so the two
cases differ in ONE respect only: the shape of the bugs.

Why this case exists (ROADMAP 5.52). exec-bugfix seeds two one-line bugs and
one that needs a function re-derived from its docstring, so it rewards
whole-block rewriting BY CONSTRUCTION — and the measured finding there was
that runs which rewrite blocks verify at 77% while runs doing single-line
surgery verify at 3%. That finding cannot be separated from the case design
using exec-bugfix alone.

Here all three bugs are single-line, single-token fixes, and each failing test
names the buggy line directly in its traceback:

  * median  `ordered[n // 2 + 1]` should be `ordered[n // 2 - 1]`
  * clamp   `min(x, hi + 1)`      should be `min(x, hi)`
  * top_n   `(kv[1], kv[0])`      should be `(-kv[1], kv[0])`

Nothing here is easier to fix by rewriting the whole function. So: if surgical
editing still loses on this case, 5.52's finding is about the strategy. If it
does not, 5.52's finding is partly about exec-bugfix's bug shapes, and the
generalization has to be withdrawn.
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
    original = (ctx.case.path / "seed" / "test_numkit.py").read_text()
    return ctx.read("test_numkit.py").strip() == original.strip()
