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
    python evals/polyglot.py --src /tmp/pg [--track javascript] [--limit N]
    python evals/polyglot.py --install-jsdeps      # once, before --track javascript
    python evals/polyglot.py --clean

TRACKS
------
`python` (34 items) is the original and its case ids are unprefixed beyond
`polyglot-`, because sweeps are already archived under those ids and rule 96
means a stored score is only readable against the rubric it was scored with.
`javascript` (49 items) lands under `polyglot-js-` so the two never collide.

Adding a track is a `Track` entry plus, if its toolchain cannot be assumed, a
`setup` script. What made JavaScript more than a table row is the skipping (see
`_unskip`) and the 99 MB of `node_modules` (see `install_jsdeps`).
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases"
PREFIX = "polyglot-"

# One `npm install` serves the whole JavaScript track: all 49 exercises declare
# an identical `devDependencies` block, and jest resolves `node_modules` through
# the symlink `setup.sh` drops in each workspace. Installing per case would need
# the network mid-sweep and ~99 MB per run.
JSDEPS = ROOT / "evals" / ".jsdeps"

PROMPT = """\
{instructions}

Implement the above in `{stub}`. The file currently contains stubs.

`{test}` holds the unit tests for this exercise. Run them with
`{run}` and keep going until they pass.

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
on, and neutering that is the same cheat one indirection further out. On the
JavaScript track it also covers `package.json` and `babel.config.js`, which are
not the spec but are the only things standing between jest and a green run on an
empty suite.
"""

import hashlib

TEST_CMD = {cmd!r}
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
    """False on a timeout, never an exception.

    A submission that makes the suite hang is a FAILED submission, not an
    ungraded one, and the difference is not pedantic: letting `TimeoutExpired`
    escape marks the run "checker raised" and drops it from the denominator,
    which quietly excuses the model for the worst class of defect it can ship.
    qwythos9 wrote an `sgf_parsing.parse` whose child loop never advances its
    index, so pytest ran forever (§5.160).

    Distinct from rule 99, which is about a budget stopping the AGENT mid-work
    — an unknown, because we interrupted it. Here the agent finished and handed
    over an artefact that does not terminate. That is a property of the
    deliverable, so it is a verdict."""
    try:
        proc = ctx.bash(TEST_CMD, timeout={timeout})
    except Exception:
        return False
    return proc.returncode == 0
'''

JS_SETUP = """\
#!/usr/bin/env bash
# Link the shared jest toolchain into the workspace.
#
# Every exercise in the JavaScript track declares the same devDependencies, so
# one install serves all 49. A symlink rather than a copy: `node_modules` is
# 99 MB and a sweep of 49 cases x N repeats would otherwise write tens of GB of
# identical files into scratch dirs. The harness already skips `node_modules`
# and symlinks when it snapshots a workspace, so this leaves no trace in the
# archived result.
set -euo pipefail
DEPS="{deps}"
if [ ! -d "$DEPS" ]; then
  echo "missing $DEPS -- run: python evals/polyglot.py --install-jsdeps" >&2
  exit 1
fi
ln -sfn "$DEPS" node_modules
"""

JS_PACKAGE = """\
{
  "name": "locode-polyglot-js-toolchain",
  "private": true,
  "devDependencies": {
    "@babel/core": "^7.25.2",
    "@exercism/babel-preset-javascript": "^0.2.1",
    "babel-jest": "^29.6.4",
    "core-js": "~3.37.1",
    "jest": "^29.7.0"
  }
}
"""


def _unskip(text: str) -> str:
    """Turn Exercism's `xtest`/`xit`/`xdescribe` back into live tests.

    The JavaScript track ships every exercise with all but the first assertion
    disabled -- 855 of 906 across the 49 items, and not one exercise is exempt.
    That is a teaching convention: the student un-skips as they go. Graded as
    shipped it would mean scoring most exercises on a single test, which is not
    a weaker measurement of the same thing but a different and far easier task,
    and not comparable to the Python track where every test is live.

    So the generator un-skips, and the seed property that matters is checked
    mechanically afterwards: `polyglot_validate.py` asserts the untouched stub
    fails and `.meta/proof.ci.js` passes, with all tests active. Anything the
    substitution mangles shows up there rather than as a silently easy case.
    """
    return re.sub(r"\bx(test|it|describe)\b", r"\1", text)


@dataclass(frozen=True)
class Track:
    """How one language track is laid out and how its tests are run."""

    lang: str                       # directory name inside the clone
    prefix: str                     # case-id prefix; must be unique per track
    stub: str                       # "{base}.py"  -- {slug} and {base} available
    test: str                       # "{base}_test.py"
    reference: str                  # path to the reference solution, from ex/
    run: str                        # test command shown to the model
    # What the grader runs. Usually identical, but not necessarily: on macOS a
    # bare `python` need not exist outside a venv, so the Python track tells the
    # model `python` (what its own docs say) and grades with `python3`.
    check_run: str = ""
    check_timeout: int = 120        # grader's own cap on that command
    # `case.json`'s description. A Track field only because the Python wording
    # predates multi-track support and is frozen for byte-identity with the 34
    # already-archived cases (rule 96).
    desc: str = ("Exercism `{slug}` from Aider's polyglot benchmark, generated by "
                 "evals/polyglot.py. Borrowed task set: resolution comes from the "
                 "item count, not from the check count (ROADMAP 5.157).")
    # Top-level entries copied into the seed and sha-protected alongside the
    # test file. `support` is called with the exercise dir and the names of the
    # stub and test, and returns relative paths (files, or dirs to walk).
    support: Callable[[Path, str, str], list[str]] = lambda ex, stub, test: []
    transform: Callable[[str, str], str] = lambda name, text: text
    setup: str = ""
    # slug -> why it cannot be graded. Excluded loudly, never silently: a task
    # set is only worth borrowing if what got dropped from it is on the record.
    exclude: dict = field(default_factory=dict)

    def base(self, slug: str) -> str:
        return slug.replace("-", "_")

    def names(self, slug: str) -> tuple[str, str]:
        f = {"slug": slug, "base": self.base(slug)}
        return self.stub.format(**f), self.test.format(**f)


def _py_support(ex: Path, stub: str, test: str) -> list[str]:
    """Every other top-level `.py` file.

    Deliberately narrow, and deliberately unchanged: the 34 Python cases are
    already archived under these ids and rule 96 makes a stored score readable
    only against the rubric it was scored with. Broadening this would silently
    re-rubric them.
    """
    names = {f.name for f in ex.iterdir() if f.is_file() and f.suffix == ".py"}
    return sorted(names - {stub, test})


def _js_support(ex: Path, stub: str, test: str) -> list[str]:
    """The toolchain files jest needs, plus any fixture directory.

    `babel.config.js` and `package.json` are not the specification, but jest
    will not transform an ES-module spec without the first and resolves its
    config through the second, so a model that edits either can make the suite
    trivially green. They are protected for the same reason `paasio`'s
    `test_utils.py` is. `grep` is the one exercise with fixture data (`data/`),
    read by the assertions themselves.
    """
    out = []
    for entry in sorted(ex.iterdir()):
        if entry.name.startswith(".") or entry.name in {stub, test, "LICENSE"}:
            continue
        if entry.is_file():
            out.append(entry.name)
        elif entry.is_dir():
            out += sorted(str(p.relative_to(ex)) for p in entry.rglob("*")
                          if p.is_file())
    return out


TRACKS = {
    "python": Track(
        lang="python",
        prefix=PREFIX,
        stub="{base}.py",
        test="{base}_test.py",
        reference=".meta/example.py",
        run="python -m pytest {test} -q",
        check_run="python3 -m pytest {test} -q",
        support=_py_support,
    ),
    "javascript": Track(
        lang="javascript",
        prefix=PREFIX + "js-",
        stub="{slug}.js",
        test="{slug}.spec.js",
        reference=".meta/proof.ci.js",
        # Straight to the binary rather than `npm test`: npm would re-read the
        # exercise's own package.json scripts, and `--forceExit` keeps a leaked
        # timer or open socket from turning a decided run into a hang (the
        # `promises` and `rest-api` exercises can leave both).
        #
        # TZ is pinned because `ledger` is otherwise unsatisfiable west of
        # Greenwich: its own reference solution formats 01/01/2015 as
        # 12/31/2014 under PDT, so the case would read as a WALL that no model
        # could ever clear. Pinning it in the command the *model* is given as
        # well as the one the grader runs is deliberate -- a model that sees a
        # failure the grader will not see burns its budget chasing a phantom.
        run="TZ=UTC ./node_modules/.bin/jest {test} --ci --forceExit",
        check_timeout=180,
        desc=("Exercism `{slug}` (javascript) from Aider's polyglot benchmark, "
              "generated by evals/polyglot.py. Every test un-skipped -- the track "
              "ships 855 of 906 disabled. Resolution comes from the item count "
              "(ROADMAP 5.157)."),
        support=_js_support,
        transform=lambda name, text: _unskip(text) if name.endswith(".spec.js") else text,
        setup=JS_SETUP,
        exclude={
            # Not a write-from-spec problem at all. Exercism ships `ledger` as a
            # *refactoring* exercise -- the stub is a complete, deliberately ugly
            # implementation, and its own instructions say it "consistently
            # passes the test suite". Graded on tests_pass the untouched seed
            # scores full marks, which is rule 90 exactly: a check true of the
            # seed is not something a model can earn. Caught by
            # polyglot_validate.py's FAILS_EMPTY, which is what that check is
            # for. 48 items, not 49.
            "ledger": "refactoring exercise -- the untouched stub already passes",
        },
    ),
}


def install_jsdeps() -> int:
    """`npm install` the shared JavaScript toolchain. Returns an exit code."""
    JSDEPS.mkdir(parents=True, exist_ok=True)
    (JSDEPS / "package.json").write_text(JS_PACKAGE)
    (JSDEPS / ".npmrc").write_text("audit=false\nfund=false\n")
    print(f"installing the shared jest toolchain into {JSDEPS} ...")
    try:
        proc = subprocess.run(["npm", "install"], cwd=JSDEPS, text=True)
    except OSError as exc:
        print(f"could not run npm: {exc}", file=sys.stderr)
        return 1
    if proc.returncode == 0:
        print(f"ok -- {JSDEPS / 'node_modules'}")
    return proc.returncode


def _exercises(src, track):
    """`(slug, dir)` for every practice exercise in the track."""
    root = Path(src) / track.lang / "exercises" / "practice"
    if not root.is_dir():
        sys.exit(f"no {track.lang} exercises under {src} -- is that the polyglot clone?")
    return sorted((d.name, d) for d in root.iterdir() if d.is_dir())


def _files(ex, slug, track):
    """`(stub, test, support)` names for an exercise, or `None` if it is not the
    shape we expect.

    Keyed off the slug rather than "the source file that isn't the test",
    because that heuristic is wrong: `paasio` ships `test_utils.py` alongside
    `paasio.py` and `paasio_test.py`, and whichever of the two non-test files
    happened to be iterated last won. Exercism names the stub after the
    exercise, so use that and treat everything else as support to be copied but
    not solved."""
    stub, test = track.names(slug)
    if not (ex / stub).is_file() or not (ex / test).is_file():
        return None
    return stub, test, track.support(ex, stub, test)


def build(src, track, limit=None, only=None):
    """Generate a case directory per exercise. Returns the slugs written."""
    written = []
    for slug, ex in _exercises(src, track):
        if only and slug not in only:
            continue
        if slug in track.exclude:
            print(f"  skip {slug}: {track.exclude[slug]}", file=sys.stderr)
            continue
        found = _files(ex, slug, track)
        if found is None:
            print(f"  skip {slug}: unexpected file layout", file=sys.stderr)
            continue
        stub, test, support = found
        instructions = (ex / ".docs" / "instructions.md")
        if not instructions.is_file():
            print(f"  skip {slug}: no instructions.md", file=sys.stderr)
            continue

        case = CASES / (track.prefix + slug)
        seed = case / "seed"
        shutil.rmtree(case, ignore_errors=True)
        seed.mkdir(parents=True)

        # The stub and the tests go in the workspace. The reference solution
        # must not: copying the whole exercise directory would hand the model
        # the answer.
        shutil.copy2(ex / stub, seed / stub)
        shas = {}
        for name in [test] + support:
            dest = seed / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = (ex / name).read_bytes()
            try:
                out = track.transform(name, raw.decode()).encode()
            except UnicodeDecodeError:      # fixture data, e.g. grep/data/*.txt
                out = raw
            dest.write_bytes(out)
            shas[name] = hashlib.sha256(out).hexdigest()

        run = track.run.format(test=test)
        check_run = (track.check_run or track.run).format(test=test)
        (case / "prompt.md").write_text(PROMPT.format(
            instructions=instructions.read_text().strip(), stub=stub, test=test,
            run=run, protected=", ".join(f"`{n}`" for n in [test] + support)))
        (case / "check.py").write_text(CHECK.format(
            shas=shas, cmd=check_run + " 2>&1", timeout=track.check_timeout))
        if track.setup:
            (case / "setup.sh").write_text(
                track.setup.format(deps=JSDEPS / "node_modules"))
        (case / "case.json").write_text(json.dumps({
            "id": track.prefix + slug,
            "track": "execute",
            "description": track.desc.format(slug=slug),
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


def clean(track=None):
    """Remove generated cases -- one track's, or every track's. Returns how many."""
    prefix = track.prefix if track else PREFIX
    gone = [d for d in CASES.iterdir() if d.is_dir() and d.name.startswith(prefix)]
    for d in gone:
        shutil.rmtree(d)
    return len(gone)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", help="path to a polyglot-benchmark clone")
    ap.add_argument("--track", default="python", choices=sorted(TRACKS),
                    help="language track to generate (default: python)")
    ap.add_argument("--limit", type=int, help="generate at most N cases")
    ap.add_argument("--only", action="append", default=[],
                    help="generate just this exercise slug (repeatable)")
    ap.add_argument("--clean", action="store_true",
                    help="delete generated cases and exit (all tracks, or just "
                         "--track's if one is named)")
    ap.add_argument("--install-jsdeps", action="store_true",
                    help="npm install the shared JavaScript toolchain and exit")
    args = ap.parse_args(argv)

    if args.install_jsdeps:
        return install_jsdeps()
    if args.clean:
        # `--track` defaults to python, so only narrow the sweep when the user
        # actually typed one; otherwise --clean means "all of them".
        named = "--track" in argv
        print(f"removed {clean(TRACKS[args.track] if named else None)} generated case(s)")
        return 0
    if not args.src:
        ap.error("--src is required unless --clean or --install-jsdeps")

    track = TRACKS[args.track]
    if track.setup and not (JSDEPS / "node_modules").is_dir():
        print(f"warning: {JSDEPS / 'node_modules'} is missing -- every {args.track} "
              "case will fail in setup.sh until you run --install-jsdeps",
              file=sys.stderr)

    written = build(args.src, track, limit=args.limit, only=args.only or None)
    print(f"wrote {len(written)} case(s) under {CASES}")
    for slug in written:
        print(f"  {track.prefix}{slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
