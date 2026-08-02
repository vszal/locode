"""Terminal keymap setup: detection, safe file surgery, idempotency.

The thing under test edits somebody's editor config, so the bar is: never
corrupt a file, never write twice, always leave a backup.
"""

import json
from pathlib import Path

import pytest

from locode.ui import termsetup as ts


def _term(tid):
    return next(t for t in ts.TERMINALS if t.id == tid)


# --- detection ---------------------------------------------------------------
@pytest.mark.parametrize("env,expect", [
    ({"TERM_PROGRAM": "zed"}, "zed"),
    ({"TERM_PROGRAM": "vscode"}, "vscode"),
    ({"TERM_PROGRAM": "iTerm.app"}, "iterm2"),
    ({"TERM_PROGRAM": "Apple_Terminal"}, "apple"),
    ({"TERM_PROGRAM": "ghostty"}, "ghostty"),
    ({"TERM_PROGRAM": "WezTerm"}, "wezterm"),
])
def test_detect_from_term_program(env, expect):
    assert ts.detect(env).id == expect


def test_detect_is_case_insensitive():
    # TERM_PROGRAM casing varies by version ("Ghostty" vs "ghostty").
    assert ts.detect({"TERM_PROGRAM": "ITERM.APP"}).id == "iterm2"


@pytest.mark.parametrize("env,expect", [
    ({"TERM": "xterm-kitty"}, "kitty"),
    ({"KITTY_WINDOW_ID": "1"}, "kitty"),
    ({"TERM": "alacritty"}, "alacritty"),
    ({"ALACRITTY_SOCKET": "/tmp/s"}, "alacritty"),
    ({"GHOSTTY_RESOURCES_DIR": "/x"}, "ghostty"),
])
def test_detect_falls_back_to_terminal_specific_vars(env, expect):
    # Older builds of these set no TERM_PROGRAM at all.
    assert ts.detect(env).id == expect


def test_detect_returns_none_when_unknown():
    assert ts.detect({"TERM": "xterm-256color"}) is None
    assert ts.detect({}) is None


def test_alacritty_window_id_alone_is_not_alacritty():
    # Zed's terminal vendors the alacritty_terminal crate and exports
    # ALACRITTY_WINDOW_ID; trusting it would write the binding into
    # ~/.config/alacritty/ for a user who has never run Alacritty.
    assert ts.detect({"ALACRITTY_WINDOW_ID": "4294967343",
                      "TERM": "xterm-256color"}) is None
    assert ts.detect({"TERM_PROGRAM": "zed",
                      "ALACRITTY_WINDOW_ID": "4294967343"}).id == "zed"


def test_unknown_help_names_the_escape_sequence():
    # Someone on an unlisted terminal still needs to know what to map it to.
    assert "x1b" in ts.unknown_help() and "Esc+Enter" in ts.unknown_help()


# --- paths -------------------------------------------------------------------
def test_config_path_differs_by_platform():
    vscode = _term("vscode")
    mac = ts.config_path(vscode, Path("/h"), "Darwin")
    linux = ts.config_path(vscode, Path("/h"), "Linux")
    assert mac == Path("/h/Library/Application Support/Code/User/keybindings.json")
    assert linux == Path("/h/.config/Code/User/keybindings.json")


def test_manual_terminals_have_no_config_path():
    assert ts.config_path(_term("iterm2"), Path("/h"), "Darwin") is None


# --- JSON array surgery ------------------------------------------------------
def test_creates_a_fresh_keymap_when_none_exists(tmp_path):
    path = tmp_path / "zed" / "keymap.json"
    outcome, backup = ts.apply(_term("zed"), path)
    assert outcome == "created" and backup is None
    data = json.loads(_strip_comments(path.read_text()))
    assert data[0]["context"] == "Terminal"
    assert data[0]["bindings"]["shift-enter"] == ["terminal::SendText", "\x1b\r"]


def test_appends_to_an_existing_keymap_without_losing_entries(tmp_path):
    path = tmp_path / "keymap.json"
    path.write_text('[\n  {"context": "Editor", "bindings": {"ctrl-k": "x"}}\n]\n')
    ts.apply(_term("zed"), path)
    data = json.loads(_strip_comments(path.read_text()))
    assert len(data) == 2
    assert data[0]["context"] == "Editor"      # existing entry survives
    assert data[1]["context"] == "Terminal"


def test_appends_into_an_empty_array(tmp_path):
    # `[]` needs no separating comma; `[{...}]` does. Getting this backwards
    # writes invalid JSON into the user's config.
    path = tmp_path / "keymap.json"
    path.write_text("[]\n")
    ts.apply(_term("zed"), path)
    assert len(json.loads(_strip_comments(path.read_text()))) == 1


def test_comments_in_the_existing_file_are_preserved(tmp_path):
    # These files are JSONC and people keep notes in them; parse-and-redump
    # would silently delete them, so the append is textual.
    path = tmp_path / "keymap.json"
    path.write_text('[\n  // my careful note\n  {"context": "Editor"}\n]\n')
    ts.apply(_term("zed"), path)
    assert "// my careful note" in path.read_text()


def test_a_file_we_dont_understand_is_left_completely_alone(tmp_path):
    # Overwriting a config we can't parse is worse than doing nothing, even
    # with a backup — and a refused run must not litter a .bak either.
    path = tmp_path / "keymap.json"
    path.write_text("this is not json at all")
    with pytest.raises(ts.NotAnArray):
        ts.apply(_term("zed"), path)
    assert path.read_text() == "this is not json at all"
    assert list(tmp_path.iterdir()) == [path]


def test_vscode_binding_targets_the_terminal_only(tmp_path):
    path = tmp_path / "keybindings.json"
    ts.apply(_term("vscode"), path)
    entry = json.loads(_strip_comments(path.read_text()))[0]
    assert entry["when"] == "terminalFocus"       # don't hijack the editor
    assert entry["args"]["text"] == "\x1b\r"


# --- line-based configs ------------------------------------------------------
@pytest.mark.parametrize("tid,needle", [
    ("ghostty", "keybind = shift+enter=text:"),
    ("kitty", "map shift+enter send_text all"),
    ("alacritty", "[[keyboard.bindings]]"),
])
def test_line_configs_append_their_own_syntax(tmp_path, tid, needle):
    path = tmp_path / "conf"
    path.write_text("existing = 1\n")
    ts.apply(_term(tid), path)
    text = path.read_text()
    assert "existing = 1" in text and needle in text


def test_append_does_not_glue_onto_an_unterminated_last_line(tmp_path):
    path = tmp_path / "conf"
    path.write_text("font-size = 14")          # no trailing newline
    ts.apply(_term("ghostty"), path)
    assert "font-size = 14\n" in path.read_text()


# --- idempotency + safety ----------------------------------------------------
def test_running_twice_changes_nothing(tmp_path):
    path = tmp_path / "keymap.json"
    ts.apply(_term("zed"), path)
    first = path.read_text()
    outcome, backup = ts.apply(_term("zed"), path)
    assert outcome == "already" and backup is None
    assert path.read_text() == first


def test_a_hand_written_binding_is_respected(tmp_path):
    # Someone who already mapped this themselves must not get a duplicate.
    path = tmp_path / "keymap.json"
    path.write_text('[{"context": "Terminal",'
                    ' "bindings": {"shift-enter": ["terminal::SendText", "\\u001b\\r"]}}]')
    outcome, _ = ts.apply(_term("zed"), path)
    assert outcome == "already"


def test_an_unrelated_return_binding_is_not_mistaken_for_ours(tmp_path):
    # Alacritty configs are full of `key = "Return"`; the key alone must not
    # count as configured or the setup silently does nothing.
    path = tmp_path / "alacritty.toml"
    path.write_text('[[keyboard.bindings]]\nkey = "Return"\nmods = "Control"\n'
                    'chars = "\\u000C"\n')
    outcome, _ = ts.apply(_term("alacritty"), path)
    assert outcome == "written"


def test_the_original_is_backed_up_before_any_write(tmp_path):
    path = tmp_path / "keymap.json"
    path.write_text("[]\n")
    _, backup = ts.apply(_term("zed"), path)
    assert backup.exists() and backup.read_text() == "[]\n"
    assert backup.name.endswith(".locode.bak")


def test_manual_terminals_refuse_to_be_written(tmp_path):
    with pytest.raises(ValueError, match="by hand"):
        ts.apply(_term("iterm2"), tmp_path / "x")


@pytest.mark.parametrize("tid", ["iterm2", "apple", "wezterm"])
def test_manual_terminals_give_real_instructions(tid):
    text = ts.instructions(_term(tid))
    assert len(text.splitlines()) >= 3
    assert any(esc in text for esc in ("\\e\\r", "\\033\\r", "\\x1b\\r"))


def test_every_terminal_is_either_writable_or_documented():
    for t in ts.TERMINALS:
        if t.kind == "manual":
            assert t.manual and not t.mac
        else:
            assert t.snippet and t.mac and ts.MARKER in t.snippet


# --- REPL wiring -------------------------------------------------------------
def _repl(monkeypatch, home, term="zed"):
    from locode.config import Config
    from locode.model.client import ModelClient
    from locode.server.manager import SingleGpuManager
    from locode.tools import build_registry
    from locode.ui.repl import Repl
    monkeypatch.setenv("TERM_PROGRAM", term)
    monkeypatch.setenv("TERM", "xterm-256color")
    for var in ("KITTY_WINDOW_ID", "ALACRITTY_SOCKET", "ALACRITTY_WINDOW_ID",
                "GHOSTTY_RESOURCES_DIR"):
        monkeypatch.delenv(var, raising=False)   # the test host is a terminal too
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    cfg = Config()
    return Repl(cfg, ModelClient(cfg.base_url), SingleGpuManager(cfg),
                build_registry(cfg))


def test_slash_terminal_setup_writes_the_keymap(tmp_path, monkeypatch, capsys):
    r = _repl(monkeypatch, tmp_path)
    r._slash_terminal_setup("")
    out = capsys.readouterr().out
    assert "Zed" in out and "✓" in out
    assert (tmp_path / ".config/zed/keymap.json").exists()


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    r = _repl(monkeypatch, tmp_path)
    r._slash_terminal_setup("--dry-run")
    out = capsys.readouterr().out
    assert "would add" in out and "shift-enter" in out
    assert not (tmp_path / ".config/zed/keymap.json").exists()


def test_manual_terminal_prints_steps_and_touches_nothing(tmp_path, monkeypatch,
                                                          capsys):
    r = _repl(monkeypatch, tmp_path, term="iTerm.app")
    r._slash_terminal_setup("")
    out = capsys.readouterr().out
    assert "iTerm2" in out and "Key Mappings" in out
    assert list(tmp_path.iterdir()) == []


def test_unknown_terminal_explains_rather_than_guessing(tmp_path, monkeypatch,
                                                        capsys):
    r = _repl(monkeypatch, tmp_path, term="something-else")
    r._slash_terminal_setup("")
    assert "Could not identify" in capsys.readouterr().out


def test_command_is_in_the_catalog_and_dispatches():
    from locode.ui import slash
    assert "terminal-setup" in slash.command_names()
    assert "terminal-setup" in slash.help_text()


def test_ctrl_j_and_esc_enter_both_insert_a_newline(tmp_path, monkeypatch):
    # Shift+Enter can't be bound at all (the terminal sends a bare CR for it),
    # so these two are the portable newline keys.
    from prompt_toolkit.keys import Keys
    r = _repl(monkeypatch, tmp_path)
    bound = {tuple(b.keys) for b in r._keybindings().bindings}
    assert (Keys.ControlJ,) in bound
    assert (Keys.Escape, Keys.ControlM) in bound


def _strip_comments(text: str) -> str:
    """Drop //-comments so a JSONC file can go through json.loads in tests."""
    return "\n".join(l for l in text.splitlines()
                     if not l.strip().startswith("//"))
