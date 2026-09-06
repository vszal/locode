"""The workspace snapshot: what a results directory keeps after --clean.

Without it the only record of the agent's output is the event log, whose long
fields telemetry.py clips -- so a written module cannot be reconstructed. See
`_snapshot_workspace` for the reasoning.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from harness import _snapshot_workspace, _SNAPSHOT_MAX_FILE  # noqa: E402


def test_the_snapshot_keeps_the_files_the_agent_wrote(tmp_path):
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "envcfg.py").write_text("def load():\n    return {}\n")
    (work / "sub" / "notes.md").write_text("hello\n")
    out = tmp_path / "results"

    _snapshot_workspace(work, out, "case__model__r1")

    snap = out / "workspaces" / "case__model__r1"
    assert (snap / "envcfg.py").read_text() == "def load():\n    return {}\n"
    assert (snap / "sub" / "notes.md").read_text() == "hello\n"


def test_caches_and_vcs_metadata_are_left_behind(tmp_path):
    work = tmp_path / "work"
    (work / "__pycache__").mkdir(parents=True)
    (work / "__pycache__" / "envcfg.pyc").write_bytes(b"\x00\x01")
    (work / ".git").mkdir()
    (work / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (work / "keep.py").write_text("x = 1\n")
    out = tmp_path / "results"

    _snapshot_workspace(work, out, "s")

    snap = out / "workspaces" / "s"
    assert (snap / "keep.py").is_file()
    assert not (snap / "__pycache__").exists()
    assert not (snap / ".git").exists()


def test_an_oversized_file_is_named_rather_than_copied(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "huge.bin").write_bytes(b"x" * (_SNAPSHOT_MAX_FILE + 1))
    (work / "small.py").write_text("ok\n")
    out = tmp_path / "results"

    _snapshot_workspace(work, out, "s")

    snap = out / "workspaces" / "s"
    assert (snap / "small.py").is_file()
    assert not (snap / "huge.bin").exists()
    assert "huge.bin" in (snap / "_SKIPPED.txt").read_text()


def test_a_missing_workdir_does_not_raise(tmp_path):
    # A snapshot failure must never cost a run its score.
    _snapshot_workspace(tmp_path / "gone", tmp_path / "results", "s")
