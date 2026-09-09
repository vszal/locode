"""Validate generated polyglot cases without spending a model on them.

Three seed properties decide whether a case is measuring anything, and all
three are checkable mechanically because Exercism ships a reference solution:

  FAILS_EMPTY   the untouched seed fails `tests_pass`. If it passes, the stub
                already satisfies the tests and the case pays for nothing --
                rule 90's complaint, in its original form.
  PASSES_REF    dropping the reference solution in makes the tests pass. If it
                does not, the exercise is unsatisfiable as posed (missing
                fixture, wrong entry point) and would read as a WALL -- a check
                no run can earn, which looks like difficulty and is not.
  GUARD_VETOES  editing the test file trips `spec_unmodified`. Without this a
                model can delete the asserts and score full marks.

FAILS_EMPTY earns its keep twice over on the JavaScript track, where the seed is
not the file Exercism ships: the generator un-skips 855 disabled tests, and if
that substitution silently missed, the stub would pass its one remaining live
test and the case would be flagged here rather than quietly scoring a model on
nothing.

Run this before any sweep over generated cases. It is fast and it is the
difference between measuring models and measuring broken fixtures.

    python evals/polyglot_validate.py --src /tmp/pg [--track javascript] [--only slug]
"""
import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polyglot import TRACKS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases"

# What a model gaming the grader would write instead of the tests: a suite that
# is green because it asserts nothing.
NEUTERED = {
    "python": "def test_nothing():\n    pass\n",
    "javascript": "test('nothing', () => {});\n",
}


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
    """Copy the seed to a temp dir, optionally mutate it, and grade it.

    Runs the case's `setup.sh` exactly as the harness does, and for the same
    reason: on the JavaScript track that script is what puts `node_modules` in
    the workspace, so without it every case would fail for want of jest and
    FAILS_EMPTY would pass for entirely the wrong reason.
    """
    mod = _load_check(case)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        shutil.copytree(case / "seed", work)
        setup = case / "setup.sh"
        if setup.is_file():
            proc = subprocess.run(["bash", str(setup)], cwd=work, text=True,
                                  capture_output=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"setup.sh failed: {(proc.stdout + proc.stderr).strip()[-300:]}")
        if mutate:
            mutate(work)
        return mod.check(_Ctx(workdir=work))


def validate(case, src, track):
    """`(ok, results)` for one generated case."""
    slug = case.name[len(track.prefix):]
    ex = Path(src) / track.lang / "exercises" / "practice" / slug
    stub, test = track.names(slug)

    empty = _in_workspace(case)

    def _solve(work):
        shutil.copy2(ex / track.reference, work / stub)

    ref = _in_workspace(case, _solve)

    def _cheat(work):
        (work / test).write_text(NEUTERED[track.lang])

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
    ap.add_argument("--track", default="python", choices=sorted(TRACKS))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=1,
                    help="validate N cases in parallel (each is three test runs)")
    args = ap.parse_args(argv)
    track = TRACKS[args.track]

    # Longest prefix wins, so `polyglot-js-affine-cipher` is never mistaken for
    # a Python case named `js-affine-cipher`.
    others = [t.prefix for t in TRACKS.values()
              if t.prefix != track.prefix and t.prefix.startswith(track.prefix)]
    cases = sorted(d for d in CASES.iterdir()
                   if d.is_dir() and d.name.startswith(track.prefix)
                   and not any(d.name.startswith(o) for o in others))
    if args.only:
        want = {track.prefix + s for s in args.only} | set(args.only)
        cases = [c for c in cases if c.name in want]
    if not cases:
        sys.exit(f"no generated {args.track} cases -- run evals/polyglot.py first")

    def one(case):
        try:
            return case, validate(case, args.src, track), None
        except Exception as exc:  # a broken fixture must not stop the sweep
            return case, None, exc

    if args.jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            outcomes = list(pool.map(one, cases))
    else:
        outcomes = [one(c) for c in cases]

    bad = []
    for case, got, exc in outcomes:
        if exc is not None:
            print(f"FAIL {case.name}: {type(exc).__name__}: {exc}")
            bad.append(case.name)
            continue
        ok, results = got
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
