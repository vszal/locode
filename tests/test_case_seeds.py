"""Rule 90, enforced: every case's untouched seed must score 0.000.

A grader asserts several kinds of thing at once. Some checks are *outcomes* --
true only if the model did the work. Others are *guards* -- true of the seed
already, false only if the model cheated or regressed something. Averaging the
two together pays a model that changed nothing, which is how three of the four
shipped cases once paid an untouched seed 0.500 (ROADMAP 5.142) and how thirteen
research cases were still paying up to 0.500 as of ROADMAP 5.150.

The fix is a declaration (`GUARDS`), and a declaration drifts. So this test
copies each case's seed into a temp workspace, grades it with the case's own
`check()`, and asserts the score is exactly zero. A new case that forgets to
declare its guards fails here rather than silently inflating a sweep.

It does NOT run a model; it grades a workspace nobody edited.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from locode.bench.runner import CheckCtx, load_grader, _score

REPO = Path(__file__).resolve().parent.parent
CASE_ROOTS = [REPO / "locode" / "bench" / "cases", REPO / "evals" / "cases"]


def _all_cases():
    for root in CASE_ROOTS:
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "check.py").is_file():
                yield d


class _Case:
    """The two attributes a grader may reach for on `ctx.case`."""

    def __init__(self, path: Path):
        self.path = path
        self.id = path.name


CASES = list(_all_cases())


def test_case_roots_are_populated():
    """Guard the guard: an empty glob would make every test below vacuous."""
    assert len(CASES) >= 18, f"expected the full suite, found {len(CASES)}"


@pytest.mark.parametrize("case_dir", CASES, ids=lambda d: d.name)
def test_untouched_seed_scores_zero(case_dir):
    case = _Case(case_dir)
    check, guards, derived = load_grader(case)
    assert check is not None, f"{case.id} has no check() function"

    workdir = Path(tempfile.mkdtemp(prefix=f"seedcheck-{case.id}-"))
    try:
        seed = case_dir / "seed"
        if seed.is_dir():
            shutil.copytree(seed, workdir, dirs_exist_ok=True)
        ctx = CheckCtx(workdir=workdir, events=[], stdout="", case=case)
        checks = dict(check(ctx))
        score = _score(checks, guards, derived)

        unearned = sorted(
            k for k, v in checks.items()
            if v and k not in guards and k not in derived
        )
        assert score == 0.0, (
            f"{case.id}: untouched seed scores {score:.3f}, not 0.000. "
            f"Checks true without any model work: {unearned}. "
            f"Declare them in GUARDS (or DERIVED if they are aggregates)."
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
