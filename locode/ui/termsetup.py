"""Teach the terminal emulator to send something for Shift+Enter.

A terminal transmits the *same byte* for Enter and Shift+Enter — CR, `\\r`. The
modifier is dropped before any program sees it, so no TUI can bind Shift+Enter
directly; prompt_toolkit's key table has no such key at all. The only fix is on
the terminal side: map Shift+Enter to ESC+CR (`\\x1b\\r`), which is exactly what
Esc+Enter already sends, and locode needs no change to understand it.

This module knows where each terminal keeps its keymap and what to put there.
Editing someone's editor config is not a thing to be casual about, so: every
write is preceded by a `.locode.bak` copy, every write is idempotent (a marker
comment makes a second run a no-op), and terminals whose config we can't edit
safely — plists, Lua — get printed instructions instead of a silent poke.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

# ESC then CR. Written as an escape in each terminal's own notation below.
SEQUENCE = "\x1b\r"
MARKER = "locode: Shift+Enter inserts a newline"


class NotAnArray(ValueError):
    """The keymap file exists but isn't the JSON array we know how to extend."""


@dataclass(frozen=True)
class Terminal:
    id: str
    name: str
    kind: str          # "json" (array file), "line" (append), "manual"
    snippet: str = ""  # what gets written, for json/line
    manual: str = ""   # what to tell the user, for manual
    note: str = ""     # follow-up, e.g. "restart the terminal"
    # Config path relative to $HOME, per platform. "" means unsupported there.
    mac: str = ""
    linux: str = ""


_ZED = Terminal(
    "zed", "Zed", "json",
    snippet="""  {
    // %s (sends ESC+CR)
    "context": "Terminal",
    "bindings": { "shift-enter": ["terminal::SendText", "\\u001b\\r"] }
  }""" % MARKER,
    mac=".config/zed/keymap.json", linux=".config/zed/keymap.json",
    note="Zed picks this up immediately — no restart needed.")

_VSCODE = Terminal(
    "vscode", "VS Code", "json",
    snippet="""  {
    // %s (sends ESC+CR)
    "key": "shift+enter",
    "command": "workbench.action.terminal.sendSequence",
    "when": "terminalFocus",
    "args": { "text": "\\u001b\\r" }
  }""" % MARKER,
    mac="Library/Application Support/Code/User/keybindings.json",
    linux=".config/Code/User/keybindings.json",
    note="VS Code picks this up immediately — no restart needed.")

_GHOSTTY = Terminal(
    "ghostty", "Ghostty", "line",
    snippet="# %s (sends ESC+CR)\nkeybind = shift+enter=text:\\x1b\\r" % MARKER,
    mac=".config/ghostty/config", linux=".config/ghostty/config",
    note="Reload with Cmd+Shift+, or restart Ghostty.")

_KITTY = Terminal(
    "kitty", "kitty", "line",
    snippet="# %s (sends ESC+CR)\nmap shift+enter send_text all \\x1b\\r" % MARKER,
    mac=".config/kitty/kitty.conf", linux=".config/kitty/kitty.conf",
    note="Reload with Ctrl+Shift+F5 or restart kitty.")

_ALACRITTY = Terminal(
    "alacritty", "Alacritty", "line",
    snippet=('# %s (sends ESC+CR)\n[[keyboard.bindings]]\n'
             'key = "Return"\nmods = "Shift"\nchars = "\\u001B\\r"' % MARKER),
    mac=".config/alacritty/alacritty.toml",
    linux=".config/alacritty/alacritty.toml",
    note="Alacritty reloads its config on save.")

# Below: config formats locode will not edit for you. An Apple plist and a Lua
# table are both easy to corrupt and hard to verify, and a wrong write costs the
# user their terminal settings. Instructions are the honest answer.
_ITERM2 = Terminal(
    "iterm2", "iTerm2", "manual",
    manual="Settings → Profiles → Keys → Key Mappings → +\n"
           "  Keyboard Shortcut: Shift+Enter\n"
           "  Action:            Send Text with \"vim\" Special Chars\n"
           "  Value:             \\e\\r")

_APPLE = Terminal(
    "apple", "Terminal.app", "manual",
    manual="Settings → Profiles → Keyboard → +\n"
           "  Key:      Return\n"
           "  Modifier: Shift\n"
           "  Action:   Send Text\n"
           "  Value:    \\033\\r")

_WEZTERM = Terminal(
    "wezterm", "WezTerm", "manual",
    manual="Add to the `keys` table in ~/.config/wezterm/wezterm.lua:\n"
           "  { key = 'Enter', mods = 'SHIFT',\n"
           "    action = wezterm.action.SendString '\\x1b\\r' },")

TERMINALS: tuple[Terminal, ...] = (_ZED, _VSCODE, _GHOSTTY, _KITTY, _ALACRITTY,
                                   _ITERM2, _APPLE, _WEZTERM)

# $TERM_PROGRAM values, lowercased. kitty and Alacritty are detected by their
# own env vars because older builds set no TERM_PROGRAM at all.
_BY_TERM_PROGRAM = {
    "zed": _ZED, "vscode": _VSCODE, "ghostty": _GHOSTTY, "wezterm": _WEZTERM,
    "iterm.app": _ITERM2, "apple_terminal": _APPLE, "kitty": _KITTY,
    "alacritty": _ALACRITTY,
}


# Per-terminal (key-substring, action-substring) pair used to spot a binding the
# user wrote themselves. The key half is matched against lowercased text.
_PROBES: dict[str, tuple[str, str]] = {
    "zed": ("shift-enter", "terminal::SendText"),
    "vscode": ("shift+enter", "workbench.action.terminal.sendSequence"),
    "ghostty": ("shift+enter", "text:"),
    "kitty": ("shift+enter", "send_text"),
    "alacritty": ('mods = "shift"', "chars"),
    "iterm2": ("", ""),
    "apple": ("", ""),
    "wezterm": ("", ""),
}


def detect(env: dict[str, str]) -> Terminal | None:
    """Identify the host terminal from the environment, or None if unknown."""
    prog = (env.get("TERM_PROGRAM") or "").strip().lower()
    if prog in _BY_TERM_PROGRAM:
        return _BY_TERM_PROGRAM[prog]
    term = (env.get("TERM") or "").lower()
    if env.get("KITTY_WINDOW_ID") or "kitty" in term:
        return _KITTY
    # NOT ALACRITTY_WINDOW_ID: Zed's terminal vendors the alacritty_terminal
    # crate and sets it too, so it identifies the emulation library, not the
    # app — trusting it writes the binding into the wrong program's config.
    # $TERM and the IPC socket are set by the real Alacritty only.
    if "alacritty" in term or env.get("ALACRITTY_SOCKET"):
        return _ALACRITTY
    if env.get("GHOSTTY_RESOURCES_DIR"):
        return _GHOSTTY
    return None


def config_path(term: Terminal, home: Path, system: str) -> Path | None:
    """Where this terminal keeps the keymap we'd edit, or None if we won't."""
    rel = term.mac if system == "Darwin" else term.linux
    return home / rel if rel else None


def is_configured(text: str, term: Terminal) -> bool:
    """True if Shift+Enter already sends something. Matches our own marker and
    a hand-rolled binding alike — someone who set this up themselves should get
    "already configured", not a duplicate entry.

    Both halves must appear: the key alone is not enough (Alacritty configs are
    full of unrelated `key = "Return"` lines), and the action alone even less so.
    """
    if MARKER in text:
        return True
    if term.kind == "manual":
        return False          # no file to read; never claim it's done
    key, action = _PROBES[term.id]
    return key in text.lower() and action in text


def apply(term: Terminal, path: Path) -> tuple[str, Path | None]:
    """Write the binding. Returns (outcome, backup_path); outcome is one of
    "written", "created", "already". Backs the file up before touching it."""
    if term.kind == "manual":
        raise ValueError(f"{term.name} must be configured by hand")
    text = path.read_text() if path.exists() else ""
    if text and is_configured(text, term):
        return "already", None
    # Build the new content first: if it can't be built (NotAnArray) nothing has
    # been touched and no stray backup is left behind.
    if term.kind == "json":
        updated = _append_to_json_array(text, term.snippet)
    else:
        updated = _append_lines(text, term.snippet)
    backup = None
    if path.exists():
        backup = path.with_suffix(path.suffix + ".locode.bak")
        shutil.copy2(path, backup)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated)
    return ("written" if backup else "created"), backup


def _append_to_json_array(text: str, block: str) -> str:
    """Insert `block` as the last element of a JSON array file.

    Textual, deliberately: parsing and re-dumping these files would strip the
    comments and reformatting that users keep there. An empty or missing file
    becomes a fresh array; a file that is *something else* raises, because
    overwriting a config we don't understand is worse than doing nothing.
    """
    stripped = text.strip()
    if not stripped:
        return f"[\n{block}\n]\n"
    if not stripped.endswith("]"):
        raise NotAnArray("not a JSON array")
    close = text.rindex("]")
    head = text[:close].rstrip()
    tail = text[close:]
    # An empty array takes no separating comma; a populated one does.
    sep = "" if head.rstrip().endswith("[") else ","
    return f"{head}{sep}\n{block}\n{tail.lstrip()}"


def _append_lines(text: str, block: str) -> str:
    if not text:
        return block + "\n"
    return text.rstrip("\n") + "\n\n" + block + "\n"


def instructions(term: Terminal) -> str:
    """The by-hand steps, for manual terminals and for --dry-run alike."""
    if term.kind == "manual":
        return term.manual
    return term.snippet


def unknown_help() -> str:
    """What to print when we can't tell what terminal this is."""
    names = ", ".join(t.name for t in TERMINALS)
    return ("Could not identify this terminal from $TERM_PROGRAM.\n"
            f"locode knows how to set up: {names}.\n"
            "Map Shift+Enter to send ESC then CR (\\x1b\\r) in your terminal's\n"
            "key settings — that is byte-identical to Esc+Enter, which already "
            "works.")
