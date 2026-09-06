"""The offline mirror of loop.py's revert detector."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from revert_exposure import firings  # noqa: E402


def _log(tmp_path, events):
    p = tmp_path / "run.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(p)


def _edit(path, old, new):
    return {"phase": "run", "name": "edit_file",
            "args": {"path": path, "old": old, "new": new}}


_RED = {"phase": "result", "content": "[exit 1]\n1 failed in 0.01s"}
_GREEN = {"phase": "result", "content": "13 passed in 0.00s"}


def test_an_edit_restoring_a_tested_version_counts(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _RED,
                          _edit("a.py", "2", "1")])
    assert firings(log) == 1


def test_nothing_counts_before_a_test_has_failed(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _GREEN,
                          _edit("a.py", "2", "1")])
    assert firings(log) == 0


def test_moving_forward_through_versions_is_not_a_revert(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "A", "B"), _RED,
                          _edit("a.py", "B", "C")])
    assert firings(log) == 0


def test_the_same_pair_on_a_different_file_does_not_count(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _RED,
                          _edit("b.py", "2", "1")])
    assert firings(log) == 0


def test_repeated_cycling_counts_each_return(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _RED,
                          _edit("a.py", "2", "1"),
                          _edit("a.py", "1", "2"), _RED,
                          _edit("a.py", "2", "1")])
    assert firings(log) == 3
