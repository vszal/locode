"""Tests for evals/polyglot.py -- generating locode eval cases from Aider's
polyglot-benchmark (Exercism) clone.

`polyglot.CASES` is a module-level constant pointing at the real
`evals/cases/` directory. Every test that calls `build()` or `clean()`
monkeypatches it to a tmp_path directory first, so nothing here ever touches
the real repo.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import polyglot  # noqa: E402


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

    written = polyglot.build(tmp_path / "src")

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

    written = polyglot.build(tmp_path / "src")
    assert written == ["paasio"]

    seed = cases / "polyglot-paasio" / "seed"
    assert {p.name for p in seed.iterdir()} == {
        "paasio.py", "paasio_test.py", "test_utils.py"}

    check_mod = _load_check(cases / "polyglot-paasio" / "check.py")
    assert check_mod.TEST_FILE == "paasio_test.py"
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

    polyglot.build(tmp_path / "src")

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
    assert polyglot._files(ex, "leap") is None


def test_files_returns_none_when_test_file_is_missing(tmp_path):
    practice = _src_root(tmp_path)
    ex = _make_exercise(practice, "leap", test=False)
    assert polyglot._files(ex, "leap") is None


def test_build_skips_exercise_with_missing_stub_instead_of_raising(
        tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "broken", stub=False)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src")

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

    written = polyglot.build(tmp_path / "src")

    assert written == ["leap"]
    assert not (cases / "polyglot-broken").exists()


def test_build_skips_exercise_with_missing_instructions(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    _make_exercise(practice, "broken", instructions=False)
    _make_exercise(practice, "leap")

    written = polyglot.build(tmp_path / "src")

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

    written = polyglot.build(tmp_path / "src")

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

    written = polyglot.build(tmp_path / "src", limit=2)

    assert len(written) == 2
    assert sum(1 for d in cases.iterdir() if d.is_dir()) == 2


def test_only_generates_exactly_the_named_slug(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    monkeypatch.setattr(polyglot, "CASES", cases)
    practice = _src_root(tmp_path)
    for slug in ("leap", "affine-cipher", "paasio"):
        _make_exercise(practice, slug)

    written = polyglot.build(tmp_path / "src", only=["affine-cipher"])

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
    polyglot.build(tmp_path / "src")

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

    polyglot.build(tmp_path / "src")

    mod = _load_check(cases / "polyglot-leap" / "check.py")
    assert callable(mod.check)
    assert mod.GUARDS == {"spec_unmodified"}
    assert mod.TEST_FILE == "leap_test.py"
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

    polyglot.build(tmp_path / "src")

    prompt = (cases / "polyglot-paasio" / "prompt.md").read_text()
    assert "Solve paasio." in prompt
    assert "paasio.py" in prompt
    assert "paasio_test.py" in prompt
    assert "test_utils.py" in prompt
