import pytest

from locode.config import EditorConfig
from locode.tools.ask import AskUser
from locode.tools.base import ToolContext
from locode.ui import banner, choice, editor, slash


# --- slash parsing -------------------------------------------------------
def test_slash_parse_basic():
    assert slash.parse("/model qwen14") == ("model", "qwen14")
    assert slash.parse("/help") == ("help", "")
    assert slash.parse("/") == ("help", "")
    assert slash.parse("not a command") is None


def test_help_text_lists_commands():
    t = slash.help_text()
    assert "/model" in t and "/quit" in t


# --- choice helpers ------------------------------------------------------
def test_normalize_dedupes_and_strips():
    assert choice.normalize_options([" a ", "a", "b", ""]) == ["a", "b"]


def test_parse_answer_number_and_text():
    opts = ["Yes", "No"]
    assert choice.parse_answer("1", opts) == "Yes"
    assert choice.parse_answer("no", opts) == "No"
    assert choice.parse_answer("9", opts) is None
    assert choice.parse_answer("maybe", opts) is None


# --- editor argv ---------------------------------------------------------
def test_build_open_argv_plain():
    assert editor.build_open_argv("vim", "a.py") == ["vim", "a.py"]


def test_build_open_argv_with_line():
    assert editor.build_open_argv("code -w", "a.py", 12) == ["code", "-w", "-g", "a.py:12"]
    assert editor.build_open_argv("nvim", "a.py", 5) == ["nvim", "+5", "a.py"]


def test_resolve_editor_prefers_config_then_env():
    cfg = EditorConfig(command="myedit")
    assert editor.resolve_editor(cfg, {}) == "myedit"
    assert editor.resolve_editor(EditorConfig(), {"EDITOR": "nano"}) == "nano"
    assert editor.resolve_editor(EditorConfig(),
                                 {"VISUAL": "v", "EDITOR": "e"}) == "v"


def test_build_diff_argv_with_tool():
    assert editor.build_diff_argv("kdiff3", "a", "b") == ["kdiff3", "a", "b"]


# --- banner --------------------------------------------------------------
def test_banner_renders_without_color():
    out = banner.render("qwen14", True, "/work", "0.1.0", color=False)
    assert "qwen14" in out and "/work" in out and "v0.1.0" in out
    assert "\033[" not in out  # no ANSI codes when color disabled


# --- ask_user tool -------------------------------------------------------
async def test_ask_user_returns_selection():
    async def select(q, opts):
        return opts[1]

    ctx = ToolContext(cwd="/tmp", select=select)
    res = await AskUser().run({"question": "Pick", "options": ["a", "b"]}, ctx)
    assert res.ok and "User selected: b" in res.content


async def test_ask_user_headless_declines():
    ctx = ToolContext(cwd="/tmp", select=None)
    res = await AskUser().run({"question": "Pick", "options": ["a"]}, ctx)
    assert res.is_error


# --- repl: auto-approved edits SHOW their diff ---------------------------
class _StubSpinner:
    active = False

    def start(self, *a, **k):
        pass

    def stop(self, *a, **k):
        pass


def _bare_repl(cwd):
    """A Repl with only the state `_on_event` touches — no client/model/loop
    construction."""
    from locode.ui.repl import Repl

    r = Repl.__new__(Repl)
    r._color = False
    r._spinner = _StubSpinner()
    r._tally = {"iterations": 0, "tool_calls": 0, "nudges": 0}
    r._files_changed = set()
    r._pending_path = None
    r._pending_diff = None
    r._diff_shown = False
    r._loop = type("L", (), {"_cwd": str(cwd)})()
    return r


def _edit_run_result(r, path, old, new, *, error=False):
    """Drive one edit_file through run -> (apply) -> result, as the loop would."""
    args = {"path": str(path), "old": old, "new": new}
    r._on_event({"phase": "run", "name": "edit_file", "args": args})
    if not error:
        path.write_text(path.read_text().replace(old, new))  # the tool's write
    content = "edited (1 replacement)" if not error else "`old` not found"
    r._on_event({"phase": "result", "name": "edit_file",
                 "error": error, "content": content})


def test_auto_approved_edit_prints_the_diff(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    r = _bare_repl(tmp_path)
    _edit_run_result(r, f, "b = 2", "b = 22")
    out = capsys.readouterr().out
    assert "-b = 2" in out and "+b = 22" in out  # the change is visible as a diff


def test_ask_approved_edit_does_not_double_print_diff(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("a = 1\nb = 2\n")
    r = _bare_repl(tmp_path)
    r._diff_shown = True  # the ASK prompt already showed it (via _confirm)
    _edit_run_result(r, f, "b = 2", "b = 22")
    out = capsys.readouterr().out
    assert "+b = 22" not in out  # not repeated after the result line


def test_failed_edit_prints_no_diff(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("a = 1\n")
    r = _bare_repl(tmp_path)
    _edit_run_result(r, f, "nope", "x", error=True)
    out = capsys.readouterr().out
    assert "+" not in out.replace("+ ", "")  # no diff body on a failed edit


def test_diff_shown_flag_does_not_leak_past_a_denial(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("a = 1\nb = 2\n")
    r = _bare_repl(tmp_path)
    r._diff_shown = True  # a prior ASK tool showed its diff, then was denied
    r._on_event({"phase": "denied", "name": "edit_file", "reason": "user declined"})
    _edit_run_result(r, f, "b = 2", "b = 22")  # next tool is auto-approved
    out = capsys.readouterr().out
    assert "+b = 22" in out  # the flag was reset, so the auto edit still shows
