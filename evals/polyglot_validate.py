"""Validate generated polyglot cases without spending a model on them.

Three seed properties decide whether a case is measuring anything, and all
three are checkable mechanically because Exercism ships a reference solution:

  FAILS_EMPTY   the untouched seed fails `tests_pass`. If it passes, the stub
                already satisfies the tests and the case pays for nothing --
                rule 90's complaint, in its original form.
  PASSES_REF    dropping `.meta/example.py` in as the solution makes the tests
                pass. If it does not, the exercise is unsatisfiable as posed
                (missing fixture, wrong entry point) and would read as a WALL --
                a check no run can earn, which looks like difficulty and is not.
  GUARD_VETOES  editing the test file trips `spec_unmodified`. Without this a
                model can delete the asserts and score full marks.

Run this before any sweep over generated cases. It is fast and it is the
difference between measuring models and measuring broken fixtures.

    python evals/polyglot_validate.py --src /tmp/pg [--only slug ...]
"""
import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases"
PREFIX = "polyglot-"


@dataclass
class _Ctx:
    """The slice of `CheckCtx` a generated grader actually touches."""

    workdir: Path

    def bash(self, cmd, timeout=120):
        return subprocess.run(cmd, shell=True, cwd=self.workdir, timeout=timeout,
                              capture_output=True, text=True)


def _load_check(case):
    spec = importlib.util.spec_from_file_location(
        f"pgcheck_{case.name}", case / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _in_workspace(case, mutate=None):
    """Copy the seed to a temp dir, optionally mutate it, and grade it."""
    mod = _load_check(case)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        shutil.copytree(case / "seed", work)
        if mutate:
            mutate(work)
        return mod.check(_Ctx(workdir=work))


def validate(case, src):
    """`(ok, results)` for one generated case."""
    slug = case.name[len(PREFIX):]
    ex = Path(src) / "python" / "exercises" / "practice" / slug
    # Name the stub from the slug, not by elimination -- some exercises ship a
    # support module next to the stub and "the .py that isn't the test" picks
    # the wrong one (paasio/test_utils.py).
    stub = slug.replace("-", "_") + ".py"

    empty = _in_workspace(case)

    def _solve(work):
        shutil.copy2(ex / ".meta" / "example.py", work / stub)

    ref = _in_workspace(case, _solve)

    def _cheat(work):
        # what a model gaming the grader would do: satisfy the tests by
        # deleting them, rather than by implementing the exercise.
        test = next(f for f in work.iterdir() if f.name.endswith("_test.py"))
        test.write_text("def test_nothing():\n    pass\n")

    cheat = _in_workspace(case, _cheat)

    results = {
        "FAILS_EMPTY": empty["tests_pass"] is False,
        "PASSES_REF": ref["tests_pass"] is True,
        "GUARD_VETOES": cheat["spec_unmodified"] is False,
        "GUARD_CLEAN_ON_SEED": empty["spec_unmodified"] is True,
    }
    return all(results.values()), results


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="path to the polyglot clone")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args(argv)

    cases = sorted(d for d in CASES.iterdir()
                   if d.is_dir() and d.name.startswith(PREFIX))
    if args.only:
        want = {PREFIX + s for s in args.only} | set(args.only)
        cases = [c for c in cases if c.name in want]
    if not cases:
        sys.exit("no generated polyglot cases -- run evals/polyglot.py first")

    bad = []
    for case in cases:
        try:
            ok, results = validate(case, args.src)
        except Exception as exc:  # a broken fixture must not stop the sweep
            print(f"FAIL {case.name}: {type(exc).__name__}: {exc}")
            bad.append(case.name)
            continue
        if ok:
            print(f"ok   {case.name}")
        else:
            failed = [k for k, v in results.items() if not v]
            print(f"FAIL {case.name}: {', '.join(failed)}")
            bad.append(case.name)

    print(f"\n{len(cases) - len(bad)}/{len(cases)} usable")
    if bad:
        print("unusable: " + " ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
