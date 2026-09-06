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


_RED = {"phase": "result", "name": "bash", "content": "[exit 1]\n1 failed in 0.01s"}
_GREEN = {"phase": "result", "name": "bash", "content": "13 passed in 0.00s"}
_OK = {"phase": "result", "name": "edit_file", "content": "edited a.py (1 replacement)"}
_REJECTED = {"phase": "result", "name": "edit_file", "error": True,
             "content": "no match for `old` in a.py"}


def test_an_edit_restoring_a_tested_version_counts(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _OK, _RED,
                          _edit("a.py", "2", "1"), _OK])
    assert firings(log) == 1


def test_nothing_counts_before_a_test_has_failed(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _OK, _GREEN,
                          _edit("a.py", "2", "1"), _OK])
    assert firings(log) == 0


def test_moving_forward_through_versions_is_not_a_revert(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "A", "B"), _OK, _RED,
                          _edit("a.py", "B", "C"), _OK])
    assert firings(log) == 0


def test_the_same_pair_on_a_different_file_does_not_count(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _OK, _RED,
                          _edit("b.py", "2", "1"), _OK])
    assert firings(log) == 0


def test_repeated_cycling_counts_each_return(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _OK, _RED,
                          _edit("a.py", "2", "1"), _OK,
                          _edit("a.py", "1", "2"), _OK, _RED,
                          _edit("a.py", "2", "1"), _OK])
    assert firings(log) == 3


def test_a_rejected_edit_never_enters_the_history(tmp_path):
    # loop.py records the pair only under `not res.is_error`, so an edit the
    # tool refused cannot later be reverted to. Counting attempts instead
    # inflated the first ARM F/G numbers by about 30%.
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _REJECTED, _RED,
                          _edit("a.py", "2", "1"), _OK])
    assert firings(log) == 0


def test_a_rejected_revert_does_not_count_either(tmp_path):
    log = _log(tmp_path, [_edit("a.py", "1", "2"), _OK, _RED,
                          _edit("a.py", "2", "1"), _REJECTED])
    assert firings(log) == 0
