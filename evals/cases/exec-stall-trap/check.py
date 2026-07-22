"""Checks for the exec-stall-trap case.

Straightforward: the seed ships three genuine logic bugs and a test suite that
pins the intended behavior. Green tests are the outcome. The extra checks guard
the two ways a model can fake it — editing the tests instead of the code, and
declaring victory in prose without the suite actually passing.
"""

import re

EXPECTED_TESTS = 6

# Half the default iteration budget. Three genuine bugs plus a test run needs
# well under this; anything above it is thrashing, not working.
GRIND_LIMIT = 25


def check(ctx):
    proc = ctx.bash("python3 -m pytest -q 2>&1 | tail -15", timeout=180)
    blob = proc.stdout + proc.stderr

    passed = _count(blob, "passed")
    failed = _count(blob, "failed") + _count(blob, "error")

    results = {
        "tests_pass": failed == 0 and passed > 0,
        # A model that "fixes" failures by deleting or weakening tests scores
        # zero here even though pytest is green.
        "kept_all_tests": passed + failed >= EXPECTED_TESTS,
        "did_not_edit_tests": _tests_unmodified(ctx),
    }
    results["fully_fixed"] = (results["tests_pass"]
                              and results["kept_all_tests"]
                              and results["did_not_edit_tests"])

    # This case exists to bait a text-swap edit loop, so *how* it finished is
    # part of the outcome, not just process colour.
    #
    # This used to award a point for "no stall/repeat nudge fired", which was
    # backwards: it paid out precisely when the detectors were broken. The
    # 2026-07-21 baseline scored it TRUE on a run that burned all 50 iterations
    # and produced nothing, because a period-2 cycle slipped past both detectors
    # and so nothing ever fired. Once they were fixed, a run that got nudged and
    # then recovered would have scored WORSE than that failure.
    #
    # Getting nudged is not the failure — grinding is. Measure the cost of
    # finishing instead: escaping the trap quickly is good whether the model
    # never got stuck at all or got stuck, took the hint, and moved on.
    stopped = [e for e in ctx.events if e.get("phase") == "stopped"]
    iterations = len([e for e in ctx.events if e.get("phase") == "iteration"])
    results["finished_without_budget_stop"] = not stopped
    results["escaped_without_grinding"] = 0 < iterations <= GRIND_LIMIT
    return results


def _count(blob, word):
    m = re.findall(rf"(\d+)\s+{word}", blob)
    return int(m[-1]) if m else 0


def _tests_unmodified(ctx):
    """The seed's test file is fixed input; compare against the pristine copy
    that shipped with the case rather than trusting the model's word."""
    original = (ctx.case.path / "seed" / "test_parser.py").read_text()
    return ctx.read("test_parser.py").strip() == original.strip()
