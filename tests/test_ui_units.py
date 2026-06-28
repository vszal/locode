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
