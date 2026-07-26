import io

from locode.ui import render, slash
from locode.ui.render import StreamSink


def _sink():
    out = []
    return StreamSink(out.append), out


def test_plain_text_passes_through():
    s, out = _sink()
    s.feed("hello ")
    s.feed("world")
    s.flush()
    assert "".join(out) == "hello world"
    assert s.suppressed_any is False


def test_tool_fence_suppressed_inline():
    s, out = _sink()
    s.feed('Sure.\n```tool\n{"name": "ls", "args": {}}\n```')
    s.flush()
    assert "".join(out) == "Sure.\n"   # prose kept, fence dropped
    assert s.suppressed_any is True


def test_tool_marker_split_across_deltas_does_not_leak():
    s, out = _sink()
    for piece in ["I will ", "``", "`to", "ol\n{}\n```"]:
        s.feed(piece)
    s.flush()
    assert "".join(out) == "I will "   # no stray backticks before the fence


def test_ordinary_code_fence_is_not_suppressed():
    s, out = _sink()
    s.feed("```python\nprint(1)\n```")
    s.flush()
    assert "".join(out) == "```python\nprint(1)\n```"
    assert s.suppressed_any is False


def test_reset_clears_state():
    s, out = _sink()
    s.feed("```tool\n{}\n```")     # enters suppression
    s.reset()
    out.clear()
    s.feed("fresh answer")
    s.flush()
    assert "".join(out) == "fresh answer"


def test_format_run_plain_and_color():
    plain = render.format_run("bash", {"cmd": "ls -la"}, color=False)
    assert plain == "  ⚙ bash ls -la"
    colored = render.format_run("bash", {"cmd": "ls -la"}, color=True)
    assert "\033[" in colored and "bash" in colored


def test_format_result_summarizes_multiline():
    out = render.format_result("ls", "a\nb\nc", is_error=False, color=False)
    assert "✓" in out and "+2 more lines" in out


def test_format_result_error_marker():
    out = render.format_result("bash", "boom", is_error=True, color=False)
    assert "✗" in out and "boom" in out


def test_format_result_surfaces_pytest_verdict_not_the_banner():
    """The verdict lives at the end under a banner; a first-line summary would
    show 'test session starts' and bury the outcome. Regression guard for the
    2026-07-25 'can't tell pass from fail' finding."""
    out = render.format_result(
        "bash",
        "===== test session starts =====\ncollected 3 items\n\n"
        "test_cart.py ...\n\n===== 3 passed in 0.00s =====",
        is_error=False, color=False)
    assert "3 passed" in out and "session starts" not in out
    assert "✓" in out and "✗" not in out


def test_format_result_marks_a_failing_command_even_when_the_tool_ran():
    """pytest can exit nonzero as data (is_error False). A green ✓ next to
    '1 failed' reads as success — the marker must flip."""
    out = render.format_result(
        "bash",
        "===== test session starts =====\ntest_cart.py F..\n\n"
        "FAILED test_cart.py::test_plain\n===== 1 failed, 2 passed in 0.01s =====",
        is_error=False, color=False)
    assert "1 failed" in out and "✗" in out


def test_format_result_surfaces_exception_from_a_traceback():
    out = render.format_result(
        "bash",
        "Traceback (most recent call last):\n  File \"x.py\", line 3\n"
        "    foo()\nValueError: bad thing",
        is_error=False, color=False)
    assert "ValueError: bad thing" in out and "✗" in out


def test_format_result_leaves_plain_output_on_the_first_line():
    """No verdict pattern → unchanged behaviour: first line, green ✓."""
    out = render.format_result("ls", "cart.py\ntest_cart.py\nutil.py",
                               is_error=False, color=False)
    assert out.strip().startswith("✓") and "cart.py" in out and "+2 more lines" in out


# --- markdown streaming -------------------------------------------------------
def test_markdown_styles_code_heading_and_inline():
    out = []
    s = StreamSink(out.append, markdown=True)
    for piece in ["# Title\n", "```python\n", "x = 1\n", "```\n", "**b** and `c`\n"]:
        s.feed(piece)
    s.flush()
    text = "".join(out)
    assert "\033[1m# Title" in text          # heading bold
    assert "\033[2m```python" in text        # fence dim
    assert "\033[36mx = 1" in text           # code body cyan
    assert "\033[1mb\033[0m" in text         # inline **bold**
    assert "\033[33mc\033[0m" in text        # inline `code`


def test_markdown_off_streams_raw_tokens():
    out = []
    s = StreamSink(out.append, markdown=False)
    s.feed("# Title")        # no newline -> token streams immediately, unstyled
    s.flush()
    assert "".join(out) == "# Title"


def test_markdown_still_suppresses_tool_fence():
    out = []
    s = StreamSink(out.append, markdown=True)
    s.feed("ok\n```tool\n{}\n```")
    s.flush()
    text = "".join(out)
    assert "```tool" not in text and "ok" in text


def test_markdown_styles_lists_quote_link_strike_italic_hr():
    out = []
    s = StreamSink(out.append, markdown=True)
    for piece in [
        "- bullet one\n", "* bullet two\n", "1. first\n", "2) second\n",
        "> a quote\n", "[a link](https://example.com)\n", "~~gone~~\n",
        "*it* and _also_\n", "---\n",
    ]:
        s.feed(piece)
    s.flush()
    text = "".join(out)
    assert "\033[36m•\033[0m bullet one" in text
    assert "\033[36m•\033[0m bullet two" in text
    assert "\033[1m1.\033[0m first" in text
    assert "\033[1m2)\033[0m second" in text
    assert "\033[2m▏ a quote\033[0m" in text
    assert "\033[4ma link\033[0m\033[2m (https://example.com)\033[0m" in text
    assert "\033[9mgone\033[0m" in text
    assert "\033[3mit\033[0m" in text and "\033[3malso\033[0m" in text
    assert "\033[2m" + "─" * 40 + "\033[0m" in text


def test_markdown_bold_not_mistaken_for_italic():
    out = []
    s = StreamSink(out.append, markdown=True)
    s.feed("**bold only**\n")
    s.flush()
    text = "".join(out)
    assert "\033[1mbold only\033[0m" in text
    assert "\033[3m" not in text  # no stray italic from the leftover single *


# --- diff preview for approvals -----------------------------------------------
def test_format_change_write_new_file(tmp_path):
    out = render.format_change("write_file", {"path": "new.txt", "content": "a\nb"},
                               str(tmp_path), color=False)
    assert "+a" in out and "+b" in out


def test_format_change_edit_shows_diff(tmp_path):
    (tmp_path / "f.txt").write_text("hello world\n")
    out = render.format_change("edit_file",
                               {"path": "f.txt", "old": "world", "new": "there"},
                               str(tmp_path), color=False)
    assert "-hello world" in out and "+hello there" in out


def test_format_change_none_for_readonly_tool(tmp_path):
    assert render.format_change("read_file", {"path": "x"}, str(tmp_path)) == ""


def test_format_change_previews_replace_lines(tmp_path):
    # The approval diff (and the auto-approve visibility echo) both go through
    # format_change, so a line-number edit must render as a real -/+ diff.
    (tmp_path / "c.py").write_text("a = 1\nb = 2\nc = 3\n")
    out = render.format_change("replace_lines",
                               {"path": "c.py", "start": 2, "end": 2, "new": "b = 22"},
                               str(tmp_path), color=False)
    assert "-b = 2" in out and "+b = 22" in out


def test_format_change_replace_lines_bad_range_is_blank(tmp_path):
    (tmp_path / "c.py").write_text("a\nb\n")
    out = render.format_change("replace_lines",
                               {"path": "c.py", "start": 9, "end": 9, "new": "z"},
                               str(tmp_path), color=False)
    assert out == ""


def test_format_change_previews_tolerant_edit(tmp_path):
    # The approval diff must reflect a whitespace-tolerant edit (not just exact),
    # so the user approves the real change.
    (tmp_path / "c.py").write_text("class A:\n    x = 1\n    y = 2\n")
    out = render.format_change("edit_file",
                               {"path": "c.py", "old": "x = 1\ny = 2",
                                "new": "x = 10\n    y = 20"},
                               str(tmp_path), color=False)
    assert "-    x = 1" in out and "+    x = 10" in out


def test_format_change_empty_when_no_op(tmp_path):
    (tmp_path / "f.txt").write_text("same\n")
    out = render.format_change("write_file", {"path": "f.txt", "content": "same\n"},
                               str(tmp_path), color=False)
    assert out == ""


# --- timing / color / error helpers -------------------------------------------
def test_format_timing():
    out = render.format_timing(400, 2.0, color=False)
    assert "~100 tok" in out and "2.0s" in out and "50 tok/s" in out


# --- live plan checklist ------------------------------------------------------
from locode.agent.plan import Plan  # noqa: E402


def _plan(rows):
    p = Plan()
    p.replace(rows)
    return p


def test_format_plan_renders_checklist_with_current_task():
    p = _plan(["[x] read the spec", "[>] write DESIGN.md", "[ ] write PLAN.md"])
    out = render.format_plan(p, color=False)
    assert "1/3 done" in out
    assert "☑ read the spec" in out
    assert "▶ write DESIGN.md" in out          # current task arrowed
    assert "☐ write PLAN.md" in out


def test_format_plan_empty_is_blank():
    assert render.format_plan(Plan(), color=False) == ""


def test_format_plan_current_falls_to_first_open_when_none_doing():
    # No [>] task: current = first not-done, so it should still be arrowed.
    p = _plan(["[x] a", "[ ] b", "[ ] c"])
    out = render.format_plan(p, color=False)
    assert "▶ b" in out and "☐ c" in out


def test_format_plan_colors_the_current_task():
    p = _plan(["[>] do it"])
    assert "\033[" in render.format_plan(p, color=True)


# --- end-of-turn summary ------------------------------------------------------
def test_format_turn_summary_counts_and_pluralizes():
    out = render.format_turn_summary(
        {"iterations": 16, "tool_calls": 8, "files_changed": 3, "nudges": 2},
        color=False)
    assert "16 iterations" in out
    assert "8 tool calls" in out
    assert "3 files changed" in out
    assert "2 nudges" in out


def test_format_turn_summary_singular_forms():
    out = render.format_turn_summary(
        {"iterations": 1, "tool_calls": 1, "files_changed": 1, "nudges": 1},
        color=False)
    assert "1 iteration " in out + " "  # not "iterations"
    assert "1 tool call " in out + " "
    assert "1 file changed" in out
    assert "1 nudge" in out and "nudges" not in out


def test_format_turn_summary_empty_when_nothing_happened():
    assert render.format_turn_summary(
        {"iterations": 0, "tool_calls": 0, "files_changed": 0, "nudges": 0},
        color=False) == ""


def test_format_turn_summary_suppressed_for_plain_chat_reply():
    # One iteration, no tools/nudges = a conversational answer; no trailer.
    assert render.format_turn_summary(
        {"iterations": 1, "tool_calls": 0, "files_changed": 0, "nudges": 0},
        color=False) == ""


def test_format_turn_summary_omits_zero_fields():
    out = render.format_turn_summary(
        {"iterations": 0, "tool_calls": 4, "files_changed": 0, "nudges": 0},
        color=False)
    assert "4 tool calls" in out
    assert "iteration" not in out and "file" not in out and "nudge" not in out


def test_format_nudge_is_a_visible_warning():
    out = render.format_nudge("repeated the same tool call", color=False)
    assert "⟳" in out and "repeated the same tool call" in out


def test_should_color_respects_no_color(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setenv("NO_COLOR", "1")
    assert render.should_color(TTY()) is False
    monkeypatch.delenv("NO_COLOR")
    assert render.should_color(TTY()) is True


def test_should_color_non_tty():
    assert render.should_color(io.StringIO()) is False


def test_error_helper():
    assert render.error("boom", color=False) == "✗ boom"


def test_rule_plain_titled_and_bottom():
    assert render.rule(20, color=False) == "─" * 20
    top = render.rule(20, lead="╭", label="qwen14", color=False)
    assert top.startswith("╭─ qwen14 ") and len(top) == 20
    assert render.rule(20, lead="╰", color=False) == "╰" + "─" * 19


# --- slash command catalog ----------------------------------------------------
def test_slash_has_retry_and_describe():
    assert "retry" in slash.command_names()
    assert slash.describe("retry")
    assert slash.describe("nope") == ""
