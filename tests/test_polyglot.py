"""Tests for evals/polyglot.py -- generating locode eval cases from Aider's
polyglot-benchmark (Exercism) clone.

`polyglot.CASES` is a module-level constant pointing at the real
`evals/cases/` directory. Every test that calls `build()` or `clean()`
monkeypatches it to a tmp_path directory first, so nothing here ever touches
the real repo.
"""
import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import polyglot  # noqa: E402

# The Python track is the default subject of everything in sections 1-10; the
# JavaScript track gets its own section at the end.
PY_TRACK = polyglot.TRACKS["python"]
JS_TRACK = polyglot.TRACKS["javascript"]


# --------------------------------------------------------------------------
# Fixture builder: a fake polyglot-benchmark clone under tmp_path.
# --------------------------------------------------------------------------

def _src_root(tmp_path):
    """`<tmp_path>/src/python/exercises/practice`, the dir `_exercises` scans."""
    root = tmp_path / "src" / "python" / "exercises" / "practice"
    root.mkdir(parents=True)
    return root


def _make_exercise(practice_root, slug, support=None, stub=True, test=True,
                    instructions=True, example=True):
    """Write one exercise directory under `practice_root` named `slug`.

    `support` is an optional {filename: content} dict of extra .py files
    (e.g. paasio's test_utils.py). Set `stub`/`test`/`instructions`/`example`
    to False to omit that piece, for the "unexpected shape" tests.
    """
    base = slug.replace("-", "_")
    ex = practice_root / slug
    ex.mkdir(parents=True)
    if stub:
        (ex / f"{base}.py").write_text(f"def solve():\n    pass  # {slug} stub\n")
    if test:
        (ex / f"{base}_test.py").write_text(f"def test_{base}():\n    assert True\n")
    for name, content in (support or {}).items():
        (ex / name).write_text(content)
    if instructions:
        (ex / ".docs").mkdir()
        (ex / ".docs" / "instructions.md").write_text(f"# {slug}\n\nSolve {slug}.\n")
    if example:
        (ex / ".meta").mkdir()
        (ex / ".meta" / "example.py").write_text(
            f"def solve():\n    return 'THE ANSWER for {slug}'\n")
    return ex


def _load_check(check_py, name="_polyglot_check"):
    """Load a generated check.py by path, the way the harness would."""
    spec = importlib.util.spec_from_file_location(name, check_py)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. Basic shape of a generated case.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _never_touch_the_real_cases_dir(tmp_path, monkeypatch):
    """Structural guard, not politeness.

    `polyglot.CASES` points at the repo's real `evals/cases/`, and `build()`
    rmtree's a case directory before rewriting it. A test that forgot to patch
    would delete generated cases out from under a sweep running against them --
    which is exactly what was happening when this file was written. Defaulting
    every test to a scratch dir makes forgetting harmless; the tests that want
    their own directory still override this.
    """
    monkeypatch.setattr(polyglot, "CASES", tmp_path / "_unpatched_cases")


def test_generated_case_has_the_four_expected_entries_and_a_matching_id(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src", PY_TRACK)

    assert written == ["leap"]
    case_dir = cases / "polyglot-leap"
    assert {p.name for p in case_dir.iterdir()} == {
        "case.json", "prompt.md", "check.py", "seed"}
    assert (case_dir / "seed").is_dir()

    data = json.loads((case_dir / "case.json").read_text())
    assert data["id"] == "polyglot-leap"


# --------------------------------------------------------------------------
# 2. The regression that matters: paasio's support module.
# --------------------------------------------------------------------------

def test_support_module_does_not_get_mistaken_for_the_stub(tmp_path, monkeypatch):
    # `_files` used to pick the stub by elimination ("the .py that is not the
    # test"), which chose nondeterministically between paasio.py and
    # test_utils.py whenever an exercise ships a support module alongside the
    # stub and the test. The stub is now named from the slug directly, so
    # this must be deterministic regardless of directory iteration order.
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "paasio",
                    support={"test_utils.py": "class MockSocket:\n    pass\n"})

    written = polyglot.build(tmp_path / "src", PY_TRACK)
    assert written == ["paasio"]

    seed = cases / "polyglot-paasio" / "seed"
    assert {p.name for p in seed.iterdir()} == {
        "paasio.py", "paasio_test.py", "test_utils.py"}

    check_mod = _load_check(cases / "polyglot-paasio" / "check.py")
    assert check_mod.TEST_CMD == "python3 -m pytest paasio_test.py -q 2>&1"
    assert set(check_mod.SPEC_SHAS) == {"paasio_test.py", "test_utils.py"}
    assert "paasio.py" not in check_mod.SPEC_SHAS


# --------------------------------------------------------------------------
# 3. The reference solution never reaches the seed.
# --------------------------------------------------------------------------

def test_reference_solution_is_never_copied_into_seed(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    ex = _make_exercise(practice, "leap")
    example_content = (ex / ".meta" / "example.py").read_text()

    polyglot.build(tmp_path / "src", PY_TRACK)

    seed = cases / "polyglot-leap" / "seed"
    assert not (seed / ".meta").exists()
    for p in seed.rglob("*"):
        if p.is_file():
            assert p.read_text() != example_content
    # and no file anywhere under seed is even named example.py
    assert not any(p.name == "example.py" for p in seed.rglob("*"))


# --------------------------------------------------------------------------
# 4. Unexpected shapes are skipped, not raised.
# --------------------------------------------------------------------------

def test_files_returns_none_when_slug_named_stub_is_missing(tmp_path):
    practice = _src_root(tmp_path)
    ex = _make_exercise(practice, "leap", stub=False)
    assert polyglot._files(ex, "leap", PY_TRACK) is None


def test_files_returns_none_when_test_file_is_missing(tmp_path):
    practice = _src_root(tmp_path)
    ex = _make_exercise(practice, "leap", test=False)
    assert polyglot._files(ex, "leap", PY_TRACK) is None


def test_build_skips_exercise_with_missing_stub_instead_of_raising(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "broken", stub=False)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src", PY_TRACK)

    assert written == ["leap"]
    assert not (cases / "polyglot-broken").exists()
    assert (cases / "polyglot-leap").exists()


def test_build_skips_exercise_with_missing_test_instead_of_raising(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "broken", test=False)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src", PY_TRACK)

    assert written == ["leap"]
    assert not (cases / "polyglot-broken").exists()


def test_build_skips_exercise_with_missing_instructions(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "broken", instructions=False)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src", PY_TRACK)

    assert written == ["leap"]
    assert not (cases / "polyglot-broken").exists()


# --------------------------------------------------------------------------
# 5. Hyphenated slugs map to underscored filenames.
# --------------------------------------------------------------------------

def test_hyphenated_slug_maps_to_underscored_filenames_and_case_id(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "affine-cipher")

    written = polyglot.build(tmp_path / "src", PY_TRACK)

    assert written == ["affine-cipher"]
    case_dir = cases / "polyglot-affine-cipher"
    seed = case_dir / "seed"
    assert (seed / "affine_cipher.py").is_file()
    assert (seed / "affine_cipher_test.py").is_file()

    data = json.loads((case_dir / "case.json").read_text())
    assert data["id"] == "polyglot-affine-cipher"


# --------------------------------------------------------------------------
# 6. limit / only.
# --------------------------------------------------------------------------

def test_limit_caps_the_number_of_generated_cases(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    for slug in ("leap", "affine-cipher", "paasio"):
        _make_exercise(practice, slug)

    written = polyglot.build(tmp_path / "src", PY_TRACK, limit=2)

    assert len(written) == 2
    assert sum(1 for d in cases.iterdir() if d.is_dir()) == 2


def test_only_generates_exactly_the_named_slug(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    for slug in ("leap", "affine-cipher", "paasio"):
        _make_exercise(practice, slug)

    written = polyglot.build(tmp_path / "src", PY_TRACK, only=["affine-cipher"])

    assert written == ["affine-cipher"]
    assert [d.name for d in cases.iterdir() if d.is_dir()] == ["polyglot-affine-cipher"]


# --------------------------------------------------------------------------
# 7. clean() only touches polyglot- prefixed dirs.
# --------------------------------------------------------------------------

def test_clean_removes_only_polyglot_prefixed_dirs(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "leap")
    _make_exercise(practice, "paasio", support={"test_utils.py": "pass\n"})
    polyglot.build(tmp_path / "src", PY_TRACK)

    decoy = cases / "bugfix-notest"
    decoy.mkdir()
    (decoy / "case.json").write_text("{}")

    removed = polyglot.clean()

    assert removed == 2
    assert not (cases / "polyglot-leap").exists()
    assert not (cases / "polyglot-paasio").exists()
    assert decoy.is_dir()
    assert (decoy / "case.json").is_file()


# --------------------------------------------------------------------------
# 8. Generated check.py is valid, loadable Python with the right contract.
# --------------------------------------------------------------------------

def test_generated_check_py_loads_and_declares_the_right_contract(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", PY_TRACK)

    mod = _load_check(cases / "polyglot-leap" / "check.py")
    assert callable(mod.check)
    assert mod.GUARDS == {"spec_unmodified"}
    assert mod.TEST_CMD == "python3 -m pytest leap_test.py -q 2>&1"
    assert set(mod.SPEC_SHAS) == {"leap_test.py"}


# --------------------------------------------------------------------------
# 9. prompt.md content.
# --------------------------------------------------------------------------

def test_prompt_md_names_instructions_stub_and_every_protected_file(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "paasio",
                    support={"test_utils.py": "class MockSocket:\n    pass\n"})

    polyglot.build(tmp_path / "src", PY_TRACK)

    prompt = (cases / "polyglot-paasio" / "prompt.md").read_text()
    assert "Solve paasio." in prompt
    assert "paasio.py" in prompt
    assert "paasio_test.py" in prompt
    assert "test_utils.py" in prompt


# --------------------------------------------------------------------------
# 10. A submission that hangs the test suite is a FAILURE, not an ungraded run.
# --------------------------------------------------------------------------

def _leap_check(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "leap")
    polyglot.build(tmp_path / "src", PY_TRACK)
    return _load_check(cases / "polyglot-leap" / "check.py", "_hangcheck")


class _Ctx:
    """A CheckCtx stand-in whose bash() does whatever the test needs."""

    def __init__(self, workdir, behaviour):
        self.workdir, self._behaviour = workdir, behaviour

    def bash(self, cmd, timeout=120):
        return self._behaviour(cmd, timeout)


def test_a_hanging_test_suite_scores_false_rather_than_raising(
        tmp_path, monkeypatch):
    """§5.160. Letting TimeoutExpired escape marks the run "checker raised",
    which drops it from the denominator and quietly excuses the model for the
    worst defect it can ship — qwythos9's `sgf_parsing.parse` never advanced its
    index, so pytest ran forever and the run vanished from the score."""
    mod = _leap_check(tmp_path, monkeypatch)

    def hang(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    result = mod.check(_Ctx(tmp_path / "cases" / "polyglot-leap" / "seed", hang))
    assert result["tests_pass"] is False


def test_any_checker_exception_is_a_failure_not_a_crash(tmp_path, monkeypatch):
    """Broader than the timeout: whatever goes wrong running the model's code,
    the answer is "these tests did not pass", never an exception out of the
    grader."""
    mod = _leap_check(tmp_path, monkeypatch)

    def boom(cmd, timeout):
        raise OSError("no such interpreter")

    seed = tmp_path / "cases" / "polyglot-leap" / "seed"
    assert mod.check(_Ctx(seed, boom))["tests_pass"] is False


def test_a_passing_suite_is_still_true(tmp_path, monkeypatch):
    """The guard must not swallow the ordinary path."""
    mod = _leap_check(tmp_path, monkeypatch)
    ok = lambda cmd, timeout: SimpleNamespace(returncode=0, stdout="", stderr="")
    seed = tmp_path / "cases" / "polyglot-leap" / "seed"
    assert mod.check(_Ctx(seed, ok))["tests_pass"] is True


# --------------------------------------------------------------------------
# 11. The JavaScript track.
#
# Its two hazards are not shared with Python. The exercises ship with almost
# every test disabled (`xtest`), so a generator that forgets to un-skip
# produces cases that look fine and measure nothing; and jest needs a
# `node_modules` that is far too big to copy per run, so the workspace gets it
# from `setup.sh`.
# --------------------------------------------------------------------------

def _js_src_root(tmp_path):
    root = tmp_path / "src" / "javascript" / "exercises" / "practice"
    root.mkdir(parents=True)
    return root


def _make_js_exercise(practice_root, slug, spec=None, extra=None):
    """One JavaScript exercise, laid out the way the real track is."""
    ex = practice_root / slug
    ex.mkdir(parents=True)
    (ex / f"{slug}.js").write_text("export const solve = () => {};\n")
    (ex / f"{slug}.spec.js").write_text(spec if spec is not None else (
        "describe('x', () => {\n"
        "  test('first', () => {});\n"
        "  xtest('second', () => {});\n"
        "});\n"))
    (ex / "babel.config.js").write_text("module.exports = {};\n")
    (ex / "package.json").write_text('{"name": "x"}\n')
    (ex / "LICENSE").write_text("MIT\n")
    (ex / ".eslintrc").write_text("{}\n")
    for name, content in (extra or {}).items():
        path = ex / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (ex / ".docs").mkdir(exist_ok=True)
    (ex / ".docs" / "instructions.md").write_text(f"# {slug}\n\nSolve {slug}.\n")
    (ex / ".meta").mkdir(exist_ok=True)
    (ex / ".meta" / "proof.ci.js").write_text("export const solve = () => 42;\n")
    return ex


def test_unskip_reenables_every_disabled_form():
    got = polyglot._unskip(
        "xtest('a', 1); xit('b', 2); xdescribe('c', 3);")
    assert got == "test('a', 1); it('b', 2); describe('c', 3);"


def test_unskip_leaves_already_live_tests_alone():
    live = "test('a', 1); it('b', 2); describe('c', 3);"
    assert polyglot._unskip(live) == live


def test_unskip_does_not_maul_identifiers_that_merely_contain_the_word():
    r"""`\b` on both sides, and it has to stay that way: `maxit` and
    `xtestimonials` are not disabled tests, and a substitution that rewrote
    them would corrupt the spec into something that fails for reasons the
    model cannot see or fix.

    A *bare* `xtest` is a different matter and is rewritten on purpose -- that
    is the jest global itself, however it is being referenced.
    """
    text = "const maxit = 1; foo.xtestimonials; const relaxity = 2;"
    assert polyglot._unskip(text) == text
    assert polyglot._unskip("const t = xtest;") == "const t = test;"


def test_js_case_is_generated_with_its_own_prefix_and_seed(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "affine-cipher")

    written = polyglot.build(tmp_path / "src", JS_TRACK)

    assert written == ["affine-cipher"]
    case_dir = cases / "polyglot-js-affine-cipher"
    assert json.loads((case_dir / "case.json").read_text())["id"] == \
        "polyglot-js-affine-cipher"
    # The slug is not underscored on this track -- JavaScript files keep the
    # hyphen, unlike Python modules.
    seed = case_dir / "seed"
    assert {p.name for p in seed.iterdir()} == {
        "affine-cipher.js", "affine-cipher.spec.js", "babel.config.js",
        "package.json"}


def test_generated_js_seed_has_no_disabled_tests_left(tmp_path, monkeypatch):
    """The one that would silently gut the track. A spec that reaches the
    workspace still skipped grades the model on a single assertion."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", JS_TRACK)

    spec = (cases / "polyglot-js-leap" / "seed" / "leap.spec.js").read_text()
    assert "xtest(" not in spec and "xit(" not in spec
    assert spec.count("test(") == 2


def test_js_sha_guard_covers_the_toolchain_but_never_the_stub(
        tmp_path, monkeypatch):
    """`package.json` and `babel.config.js` are not the specification, but a
    model that edits either can make jest green on an empty suite, so they are
    protected the same way paasio's `test_utils.py` is. The stub must stay
    unprotected -- it is the file the model is asked to write."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", JS_TRACK)

    mod = _load_check(cases / "polyglot-js-leap" / "check.py", "_jscheck")
    assert set(mod.SPEC_SHAS) == {
        "leap.spec.js", "babel.config.js", "package.json"}
    assert "leap.js" not in mod.SPEC_SHAS
    assert mod.GUARDS == {"spec_unmodified"}


def test_js_sha_is_taken_over_the_unskipped_spec_not_the_original(
        tmp_path, monkeypatch):
    """The seed is not the file Exercism ships, so the guard has to hash what
    actually lands in the workspace. Hashing the original would make every run
    look like it had edited the spec, vetoing the whole track to 0.0."""
    import hashlib

    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", JS_TRACK)

    seed_spec = (cases / "polyglot-js-leap" / "seed" / "leap.spec.js").read_bytes()
    mod = _load_check(cases / "polyglot-js-leap" / "check.py", "_jssha")
    assert mod.SPEC_SHAS["leap.spec.js"] == hashlib.sha256(seed_spec).hexdigest()


def test_js_case_ships_a_setup_script_that_links_the_shared_toolchain(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", JS_TRACK)

    setup = (cases / "polyglot-js-leap" / "setup.sh").read_text()
    assert str(polyglot.JSDEPS / "node_modules") in setup
    assert "ln -sfn" in setup
    # Python cases must not grow one.
    assert not (cases / "polyglot-js-leap" / "seed" / "node_modules").exists()


def test_js_grader_and_prompt_pin_the_same_timezone(tmp_path, monkeypatch):
    """`ledger` aside, TZ has to match between the command the model is told to
    run and the one the grader runs, or the model burns its budget chasing a
    failure the grader will never see."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "leap")

    polyglot.build(tmp_path / "src", JS_TRACK)

    mod = _load_check(cases / "polyglot-js-leap" / "check.py", "_jstz")
    prompt = (cases / "polyglot-js-leap" / "prompt.md").read_text()
    assert mod.TEST_CMD.startswith("TZ=UTC ")
    assert "TZ=UTC ./node_modules/.bin/jest leap.spec.js" in prompt


def test_a_fixture_directory_is_copied_and_protected(tmp_path, monkeypatch):
    """`grep` is the one exercise whose assertions read data files off disk."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "grep", extra={"data/iliad.txt": "sing, goddess\n"})

    polyglot.build(tmp_path / "src", JS_TRACK)

    seed = cases / "polyglot-js-grep" / "seed"
    assert (seed / "data" / "iliad.txt").read_text() == "sing, goddess\n"
    mod = _load_check(cases / "polyglot-js-grep" / "check.py", "_jsgrep")
    assert "data/iliad.txt" in mod.SPEC_SHAS


def test_excluded_slug_is_skipped_loudly(tmp_path, monkeypatch, capsys):
    """`ledger` is a refactoring exercise: its untouched stub already passes,
    so tests_pass is true of the seed and pays for nothing (rule 90)."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _js_src_root(tmp_path)
    _make_js_exercise(practice, "ledger")
    _make_js_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src", JS_TRACK)

    assert written == ["leap"]
    assert not (cases / "polyglot-js-ledger").exists()
    assert "ledger" in capsys.readouterr().err


def test_clean_narrows_to_one_track_but_defaults_to_all(tmp_path, monkeypatch):
    """`polyglot-js-` starts with `polyglot-`, so an unnarrowed clean takes
    both -- which is what `--clean` with no `--track` should do."""
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    _make_exercise(_src_root(tmp_path), "leap")
    _make_js_exercise(_js_src_root(tmp_path), "leap")
    polyglot.build(tmp_path / "src", PY_TRACK)
    polyglot.build(tmp_path / "src", JS_TRACK)
    assert {d.name for d in cases.iterdir()} == {"polyglot-leap", "polyglot-js-leap"}

    assert polyglot.clean(JS_TRACK) == 1
    assert {d.name for d in cases.iterdir()} == {"polyglot-leap"}
    assert polyglot.clean() == 1
    assert list(cases.iterdir()) == []
