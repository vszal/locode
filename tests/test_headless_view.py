"""Tests for HeadlessView — the `locode -p --show-events` on-screen renderer.

The point of the view is that a captured headless run reads like the interactive
transcript: prose streams through, the ```tool fence is suppressed in favour of a
clean tool line, and results/nudges are labelled. These assert on plain-text
output (color off) so they pin the structure, not the ANSI.
"""

from __future__ import annotations

from locode.ui.headless import HeadlessView


def _view():
    out: list[str] = []
    v = HeadlessView(out.append, color=False, markdown=False, cwd=".")
    return v, out


def test_prose_streams_through():
    v, out = _view()
    v.on_event({"phase": "assistant_start"})
    v.on_delta("Let me fix ")
    v.on_delta("the bug.")
    v.on_event({"phase": "assistant_end"})
    assert "Let me fix the bug." in "".join(out)


def test_tool_fence_is_suppressed_from_prose():
    v, out = _view()
    v.on_event({"phase": "assistant_start"})
    v.on_delta('Calling it now ```tool\n{"name": "edit_file"}\n```')
    v.on_event({"phase": "assistant_end"})
    text = "".join(out)
    assert "Calling it now " in text
    assert "```tool" not in text          # raw fence suppressed
    assert '"name": "edit_file"' not in text


def test_run_and_result_render_clean_lines():
    v, out = _view()
    v.on_event({"phase": "run", "name": "read_file", "args": {"path": "f.py"}})
    v.on_event({"phase": "result", "name": "read_file",
                "content": "1\tdef f():", "error": False})
    text = "".join(out)
    assert "read_file" in text
    assert "f.py" in text
    assert "✓" in text


def test_error_result_is_marked():
    v, out = _view()
    v.on_event({"phase": "run", "name": "edit_file", "args": {"path": "f.py"}})
    v.on_event({"phase": "result", "name": "edit_file",
                "content": "`old` not found in f.py", "error": True})
    assert "✗" in "".join(out)


def test_nudge_is_rendered_and_labelled():
    v, out = _view()
    v.on_event({"phase": "nudge", "reason": "repeated call"})
    text = "".join(out)
    assert "⟳" in text
    assert "repeated call" in text


def test_update_plan_run_does_not_emit_a_generic_line():
    v, out = _view()
    v.on_event({"phase": "run", "name": "update_plan",
                "args": {"tasks": ["[ ] a"]}})
    # No ⚙ line for update_plan (its checklist renders off the result instead).
    assert "update_plan" not in "".join(out)


def test_diff_preview_shown_for_a_real_edit(tmp_path):
    f = tmp_path / "g.py"
    f.write_text("x = 1\n")
    v, out = _view()
    v._cwd = str(tmp_path)
    v.on_event({"phase": "run", "name": "edit_file",
                "args": {"path": "g.py", "old": "x = 1", "new": "x = 2"}})
    v.on_event({"phase": "result", "name": "edit_file",
                "content": "edited g.py", "error": False})
    text = "".join(out)
    assert "x = 2" in text          # the diff preview is shown after the result
