"""Generate locode eval cases from Aider's polyglot benchmark (Exercism).

Why borrow a task set at all. §5.157 measured the four shipped cases over 1061
archived runs and found none of them can discriminate: models solve them or fail
them whole, so the per-check decimals carry nothing a solved/unsolved bit does
not. The suite gets its resolution from *checks within a case*, which is a
handful of hand-authored booleans whose statistical behaviour we then have to go
discover empirically -- that is what `percheck`, `checkdeps` and rules 90/97 all
exist to police.

An established benchmark gets its resolution somewhere else entirely: from the
*number of items*. 34 Python exercises graded pass/fail give a score with an
analytic confidence interval and need no rubric hygiene at all, because there is
no rubric -- the exercise's own unit tests decide. That is the structural fix for
the problem §5.157 documented, and it is why this is worth wiring up rather than
authoring a 21st bespoke case.

What this does NOT replace. Polyglot exercises are single-file, self-contained,
write-from-spec problems. They do not exercise repo navigation, `edit_file`
exact-match, path scoping, the repetition guard, or the tolerant tool parser --
locode's own failure modes, and the things the bespoke cases were actually built
to catch. Two jobs, two instruments: borrow this one for *model selection*, keep
the bespoke cases for *harness regression*.

Why the dataset is not vendored. The exercises are copyright Exercism, used
under its track licences. Cloning at generation time keeps that content out of
this repo and out of the wheel. Generated cases land in `evals/cases/polyglot-*`
and are gitignored; `discover_cases` picks them up like any other case.

    git clone --depth 1 https://github.com/Aider-AI/polyglot-benchmark /tmp/pg
    python evals/polyglot.py --src /tmp/pg [--limit N] [--only slug ...]
    python evals/polyglot.py --clean
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases"
PREFIX = "polyglot-"

# Python only, deliberately. The other five tracks need toolchains this box does
# not have, and mixing languages would confound "can the model code" with "is Go
# installed". 34 items is already an order of magnitude more resolution than the
# whole bespoke suite has.
LANG = "python"

PROMPT = """\
{instructions}

Implement the above in `{stub}`. The file currently contains stubs.

`{test}` holds the unit tests for this exercise. Run them with
`python -m pytest {test} -q` and keep going until they pass.

Do not edit {protected} -- that is the specification and it is what you will be
graded on.
"""

CHECK = '''\
"""Grader for a generated polyglot exercise -- see evals/polyglot.py.

One outcome check. That is deliberate and it is the point of borrowing this task
set: resolution comes from having ~34 items, not from slicing one item into
several booleans. §5.157 showed the bespoke suite tried the second and failed.

Note the consequence, so nobody re-derives it as a bug later: a single-outcome
case can never produce a "mixed" run, so rule 97 is undefined on it (§5.157).
Correct here -- rule 97 audits whether a case's checks discriminate *each other*,
and there is only one, so the question does not arise. Discrimination is measured
across the item set instead.

`spec_unmodified` is a rule-90 guard: true of the untouched seed, so it cannot
pay, and it vetoes to 0.0 if the model edits the spec to make it pass. It covers
every graded file, not just the test module -- some exercises ship a support
module (`paasio` has `test_utils.py`) holding the mocks the assertions depend
on, and neutering that is the same cheat one indirection further out.
"""

import hashlib

TEST_FILE = {test!r}
SPEC_SHAS = {shas!r}

GUARDS = {{"spec_unmodified"}}


def check(ctx):
    return {{
        "spec_unmodified": _unmodified(ctx),
        "tests_pass": _tests_pass(ctx),
    }}


def _unmodified(ctx):
    for name, want in SPEC_SHAS.items():
        path = ctx.workdir / name
        if not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            return False
    return True


def _tests_pass(ctx):
    proc = ctx.bash("python3 -m pytest " + TEST_FILE + " -q 2>&1", timeout=120)
    return proc.returncode == 0
'''


def _exercises(src):
    """`(slug, dir)` for every practice exercise in the Python track."""
    root = Path(src) / LANG / "exercises" / "practice"
    if not root.is_dir():
        sys.exit(f"no {LANG} exercises under {src} -- is that the polyglot clone?")
    return sorted((d.name, d) for d in root.iterdir() if d.is_dir())


def _files(ex, slug):
    """`(stub, test, support)` names for an exercise, or `None` if it is not the
    shape we expect.

    Keyed off the slug rather than "the .py that isn't the test", because that
    heuristic is wrong: `paasio` ships `test_utils.py` alongside `paasio.py` and
    `paasio_test.py`, and whichever of the two non-test files happened to be
    iterated last won. Exercism names the stub after the exercise, so use that
    and treat everything else as support to be copied but not solved."""
    base = slug.replace("-", "_")
    names = {f.name for f in ex.iterdir() if f.is_file() and f.suffix == ".py"}
    stub, test = f"{base}.py", f"{base}_test.py"
    if stub not in names or test not in names:
        return None
    return stub, test, sorted(names - {stub, test})


def build(src, limit=None, only=None):
    """Generate a case directory per exercise. Returns the slugs written."""
    import hashlib

    written = []
    for slug, ex in _exercises(src):
        if only and slug not in only:
            continue
        found = _files(ex, slug)
        if found is None:
            print(f"  skip {slug}: unexpected file layout", file=sys.stderr)
            continue
        stub, test, support = found
        instructions = (ex / ".docs" / "instructions.md")
        if not instructions.is_file():
            print(f"  skip {slug}: no instructions.md", file=sys.stderr)
            continue

        case = CASES / (PREFIX + slug)
        seed = case / "seed"
        shutil.rmtree(case, ignore_errors=True)
        seed.mkdir(parents=True)

        # The stub and the tests go in the workspace. `.meta/example.py` is the
        # reference solution and must not: copying the whole exercise directory
        # would hand the model the answer.
        shutil.copy2(ex / stub, seed / stub)
        for name in [test] + support:
            shutil.copy2(ex / name, seed / name)
        shas = {name: hashlib.sha256((ex / name).read_bytes()).hexdigest()
                for name in [test] + support}

        (case / "prompt.md").write_text(PROMPT.format(
            instructions=instructions.read_text().strip(), stub=stub, test=test,
            protected=", ".join(f"`{n}`" for n in [test] + support)))
        (case / "check.py").write_text(CHECK.format(test=test, shas=shas))
        (case / "case.json").write_text(json.dumps({
            "id": PREFIX + slug,
            "track": "execute",
            "description": (
                f"Exercism `{slug}` from Aider's polyglot benchmark, generated by "
                "evals/polyglot.py. Borrowed task set: resolution comes from the "
                "item count, not from the check count (ROADMAP 5.157)."),
            "allow_tools": ["read_file", "write_file", "edit_file",
                            "replace_lines", "bash", "ls", "grep", "glob"],
            "timeout": 900,
            "weight": 1.0,
            "extra_args": ["--max-iterations", "40", "--max-wallclock", "600"],
        }, indent=2) + "\n")
        written.append(slug)
        if limit and len(written) >= limit:
            break
    return written


def clean():
    """Remove every generated case. Returns how many went."""
    gone = [d for d in CASES.iterdir() if d.is_dir() and d.name.startswith(PREFIX)]
    for d in gone:
        shutil.rmtree(d)
    return len(gone)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", help="path to a polyglot-benchmark clone")
    ap.add_argument("--limit", type=int, help="generate at most N cases")
    ap.add_argument("--only", action="append", default=[],
                    help="generate just this exercise slug (repeatable)")
    ap.add_argument("--clean", action="store_true", help="delete generated cases and exit")
    args = ap.parse_args(argv)

    if args.clean:
        print(f"removed {clean()} generated case(s)")
        return 0
    if not args.src:
        ap.error("--src is required unless --clean")

    written = build(args.src, limit=args.limit, only=args.only or None)
    print(f"wrote {len(written)} case(s) under {CASES}")
    for slug in written:
        print(f"  {PREFIX}{slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
