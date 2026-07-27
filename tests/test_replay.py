"""Tests for evals/replay.py — the session-replay / pathology-verdict tool.

This tool exists so the agent building locode *sees* what the interactive user
sees (repeats, failed edits, no-ops) instead of scraping the event log into a
pass/fail scalar. Its detection logic is therefore the thing that must not be
wrong — if it under-reports a loop, the blind spot it was built to close reopens.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import replay  # noqa: E402


def _run(name, **args):
    return {"phase": "run", "name": name, "args": args, "t": 1.0}


def _result(name, content, error=False):
    return {"phase": "result", "name": name, "content": content, "error": error}


# --- classify_result ----------------------------------------------------------
def test_classify_ok_and_green():
    assert replay.classify_result("wrote 10 lines", False) == "ok"
    assert replay.classify_result("3 passed in 0.1s", False) == "ok-green"
    # a tally with failures is not green
    assert replay.classify_result("2 passed, 1 failed", False) == "ok"


def test_classify_syntax_guard_save():
    msg = ("NOT applied — this edit would introduce a SyntaxError at line 3: "
           "'(' was never closed. The file is UNCHANGED.")
    assert replay.classify_result(msg, True) == "syntax-guard"


def test_classify_noop_variants():
    assert replay.classify_result("This edit changed NOTHING", True) == "noop"
    assert replay.classify_result("`new` is identical to `old`", True) == "identical"


def test_classify_not_found_and_ambiguous():
    assert replay.classify_result("`old` not found in foo.py", True) == "not_found"
    assert replay.classify_result("ambiguous: matches 3 lines", True) == "ambiguous"


# --- call_key / repeat detection ----------------------------------------------
def test_call_key_is_order_independent():
    a = _run("edit_file", path="f.py", old="x", new="y")
    b = _run("edit_file", new="y", old="x", path="f.py")
    assert replay.call_key(a) == replay.call_key(b)


def test_call_key_distinguishes_different_args():
    a = _run("edit_file", path="f.py", old="x", new="y")
    b = _run("edit_file", path="f.py", old="x", new="z")
    assert replay.call_key(a) != replay.call_key(b)


# --- summarize ----------------------------------------------------------------
def test_summarize_counts_repeats_fails_noops_and_saves():
    events = [
        {"phase": "turn_start", "model": "qythos9"},
        {"phase": "iteration", "n": 1},
        _run("edit_file", path="f.py", old="a", new="b"),
        _result("edit_file", "`old` not found in f.py", error=True),      # fail
        {"phase": "iteration", "n": 2},
        _run("edit_file", path="f.py", old="a", new="b"),                 # repeat
        _result("edit_file", "This edit changed NOTHING", error=True),    # fail + noop
        _run("edit_file", path="f.py", old="c", new="c"),
        _result("edit_file",
                "NOT applied — this edit would introduce a SyntaxError", error=True),  # save
        {"phase": "nudge", "reason": "repeated call"},
        {"phase": "stopped", "reason": "no progress", "t": 9.0},
    ]
    s = replay.summarize(events)
    assert s["model"] == "qythos9"
    assert s["iterations"] == 2
    assert s["tool_calls"] == 3
    assert s["repeats"] == 1
    assert s["fails"] == 2          # not_found + noop; the syntax-guard is NOT a fail
    assert s["noops"] == 1
    assert s["syntax_saves"] == 1
    assert s["nudges"]["repeated call"] == 1
    assert s["stop_reason"] == "no progress"
    assert s["wall"] == 9.0


def test_summarize_detects_green_test():
    events = [
        _run("bash", cmd="pytest -q"),
        _result("bash", "5 passed in 0.2s", error=False),
    ]
    s = replay.summarize(events)
    assert s["saw_green"] is True
    assert s["fails"] == 0


def test_summarize_clean_run_has_no_flags():
    events = [
        {"phase": "turn_start", "model": "m"},
        _run("edit_file", path="f.py", old="a", new="b"),
        _result("edit_file", "file now reads …", error=False),
    ]
    s = replay.summarize(events)
    assert s["repeats"] == 0 and s["fails"] == 0 and s["noops"] == 0
    assert s["stop_reason"] is None


# --- rendering smoke ----------------------------------------------------------
def test_verdict_lines_surface_the_pathologies():
    s = replay.summarize([
        _run("edit_file", path="f.py", old="a", new="b"),
        _result("edit_file", "changed nothing", error=True),
        _run("edit_file", path="f.py", old="a", new="b"),
        _result("edit_file", "changed nothing", error=True),
    ])
    text = "\n".join(replay.verdict_lines("case", s, color=False))
    assert "no-op" in text
    assert "repeat" in text


def test_transcript_flags_repeat_and_noop():
    events = [
        _run("edit_file", path="f.py", old="a", new="a"),
        _result("edit_file", "`new` is identical to `old`", error=True),
        _run("edit_file", path="f.py", old="a", new="a"),
        _result("edit_file", "`new` is identical to `old`", error=True),
    ]
    text = "\n".join(replay.transcript_lines(events, color=False))
    assert "REPEAT" in text
    assert "NO-OP" in text


def test_render_report_roundtrips_a_written_log(tmp_path):
    log = tmp_path / "s.jsonl"
    import json
    log.write_text("\n".join(json.dumps(e) for e in [
        {"phase": "turn_start", "model": "m"},
        _run("bash", cmd="pytest"),
        _result("bash", "1 passed", error=False),
        {"phase": "stopped", "reason": "done", "t": 2.0},
    ]))
    report = replay.render_report(log, quiet=False, color=False)
    assert "VERDICT" in report
    assert "saw green test" in report
    assert "transcript" in report
