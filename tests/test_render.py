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
