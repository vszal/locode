"""Terminal rendering helpers for the REPL.

Two concerns, kept out of repl.py so they're unit-testable:

1. `StreamSink` — a stateful filter sitting between the model's token stream and
   the terminal. Fenced-path models emit their tool call AS content (```tool
   {...} ```); streaming that raw JSON at the user is the "very rough" tool
   formatting. The sink detects the ```tool marker (even when split across
   deltas), stops echoing at that point, and lets the loop's structured `run`
   event render a clean line instead. Normal prose and ordinary code fences pass
   through untouched.

2. `format_*` — compact, optionally-colored one-liners for tool run / result /
   denied / nudge events, replacing the previous truncated-first-line dump.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from pathlib import Path
from typing import Callable

_TOOL_MARKER = "```tool"

# --- ANSI (gated by a `color` flag at the call site) --------------------------
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_ITALIC = "\033[3m"
_UNDERLINE = "\033[4m"
_STRIKE = "\033[9m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"


def should_color(stream=None) -> bool:
    """True when ANSI is appropriate: a TTY and NO_COLOR is unset
    (https://no-color.org)."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _wrap(s: str, code: str, color: bool) -> str:
    return f"{code}{s}{_RESET}" if color else s


def _prefix_overlap(s: str, marker: str) -> int:
    """Largest k (< len(marker)) such that `s` ends with `marker[:k]` — i.e. how
    many trailing chars of `s` might be the start of `marker` and must be held
    back until the next delta disambiguates them."""
    for k in range(min(len(s), len(marker) - 1), 0, -1):
        if s.endswith(marker[:k]):
            return k
    return 0


class _MarkdownColorizer:
    """Light markdown styling for streamed answers. Line-buffered (emits on each
    newline) so it can color whole ```code blocks, bold # headings, and style
    list/quote/rule lines; inline **bold**, *italic*, ~~strike~~, `code`, and
    [links](url) are styled per line. Tokens within a line are held only until
    the newline, so prose still appears promptly line-by-line.

    This is a regex-based approximation, not a CommonMark parser — it's meant
    to make the common cases readable, not to be spec-correct on every edge
    case (nested emphasis, escaped markers, etc.)."""

    _BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _CODE_RE = re.compile(r"`([^`]+)`")
    _STRIKE_RE = re.compile(r"~~(.+?)~~")
    _LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    # Single * or _ emphasis — the (?!\*)/(?!_) guards keep this from firing
    # inside an already-consumed **bold** span or a literal snake_case_name.
    _ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)(\S(?:.*?\S)?)\*(?!\*)")
    _ITALIC_US_RE = re.compile(r"(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])")
    _HEADING_RE = re.compile(r"#{1,6}\s")
    _QUOTE_RE = re.compile(r"^(\s*)>\s?(.*)$")
    _BULLET_RE = re.compile(r"^(\s*)([-*+])(\s+)(.*)$")
    _NUMBERED_RE = re.compile(r"^(\s*)(\d+[.)])(\s+)(.*)$")
    _HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")

    def __init__(self, emit: Callable[[str], None]):
        self._emit = emit
        self.reset()

    def reset(self) -> None:
        self._line = ""
        self._in_code = False

    def feed(self, text: str) -> None:
        self._line += text
        while "\n" in self._line:
            line, self._line = self._line.split("\n", 1)
            self._emit(self._style(line) + "\n")

    def flush(self) -> None:
        if self._line:
            self._emit(self._style(self._line))
            self._line = ""

    def _style(self, line: str) -> str:
        if line.lstrip().startswith("```"):
            self._in_code = not self._in_code
            return _DIM + line + _RESET
        if self._in_code:
            return _CYAN + line + _RESET
        if self._HR_RE.match(line):
            return _DIM + "─" * 40 + _RESET
        if self._HEADING_RE.match(line):
            return _BOLD + line + _RESET
        m = self._QUOTE_RE.match(line)
        if m:
            indent, rest = m.groups()
            return _DIM + indent + "▏ " + self._inline(rest) + _RESET
        m = self._BULLET_RE.match(line)
        if m:
            indent, _marker, sp, rest = m.groups()
            return indent + _CYAN + "•" + _RESET + sp + self._inline(rest)
        m = self._NUMBERED_RE.match(line)
        if m:
            indent, marker, sp, rest = m.groups()
            return indent + _BOLD + marker + _RESET + sp + self._inline(rest)
        return self._inline(line)

    def _inline(self, text: str) -> str:
        text = self._BOLD_RE.sub(_BOLD + r"\1" + _RESET, text)
        text = self._CODE_RE.sub(_YELLOW + r"\1" + _RESET, text)
        text = self._STRIKE_RE.sub(_STRIKE + r"\1" + _RESET, text)
        text = self._LINK_RE.sub(
            _UNDERLINE + r"\1" + _RESET + _DIM + r" (\2)" + _RESET, text)
        text = self._ITALIC_STAR_RE.sub(_ITALIC + r"\1" + _RESET, text)
        text = self._ITALIC_US_RE.sub(_ITALIC + r"\1" + _RESET, text)
        return text


class StreamSink:
    """Filters a token stream, suppressing a ```tool fenced block. `emit` is the
    raw writer (e.g. RawWriter.write). Reset per model call, flush at its end.
    With `markdown=True` (and color), surviving text is line-buffered and styled
    by `_MarkdownColorizer`; otherwise it streams token-by-token unchanged."""

    def __init__(self, emit: Callable[[str], None], *, markdown: bool = False):
        self._emit = emit
        self._md = _MarkdownColorizer(emit) if markdown else None
        self.reset()

    def _out(self, text: str) -> None:
        if not text:
            return
        if self._md is not None:
            self._md.feed(text)
        else:
            self._emit(text)

    def reset(self) -> None:
        self._buf = ""
        self._suppressing = False
        self.suppressed_any = False
        if self._md is not None:
            self._md.reset()

    def feed(self, piece: str) -> None:
        if self._suppressing:
            return  # inside a tool fence — drop the rest of this turn's content
        self._buf += piece
        idx = self._buf.find(_TOOL_MARKER)
        if idx != -1:
            self._out(self._buf[:idx])   # flush any prose before the fence
            self._buf = ""
            self._suppressing = True
            self.suppressed_any = True
            return
        hold = _prefix_overlap(self._buf, _TOOL_MARKER)
        if hold:
            self._out(self._buf[:-hold])
            self._buf = self._buf[-hold:]
        else:
            self._out(self._buf)
            self._buf = ""

    def flush(self) -> None:
        """Emit any held-back tail (it turned out not to be a tool fence)."""
        if not self._suppressing and self._buf:
            self._out(self._buf)
        self._buf = ""
        if self._md is not None:
            self._md.flush()


# --- event formatting ---------------------------------------------------------
def _summarize_args(name: str, args: dict) -> str:
    if name == "bash":
        return str(args.get("cmd", ""))
    if name in ("read_file", "write_file", "append_file", "edit_file", "ls"):
        return str(args.get("path", ""))
    if name == "glob":
        return str(args.get("pattern", ""))
    if name == "grep":
        pat = args.get("pattern", "")
        where = args.get("glob") or args.get("path") or ""
        return f"{pat}  {where}".rstrip()
    if name == "web_search":
        return str(args.get("query", ""))
    if name == "web_fetch":
        return str(args.get("url", ""))
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _result_summary(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return "(no output)"
    lines = text.splitlines()
    if len(lines) == 1:
        return _truncate(lines[0], 80)
    return f"{_truncate(lines[0], 56)}  (+{len(lines) - 1} more lines)"


def format_run(name: str, args: dict, *, color: bool = True) -> str:
    gear = _wrap("⚙", _CYAN, color)
    nm = _wrap(name, _BOLD, color)
    summ = _summarize_args(name, args)
    summ = _wrap(_truncate(summ, 100), _DIM, color) if summ else ""
    return f"  {gear} {nm} {summ}".rstrip()


def format_result(name: str, content: str, is_error: bool, *, color: bool = True) -> str:
    summ = _result_summary(content)
    if is_error:
        return f"    {_wrap('✗', _RED, color)} {_wrap(summ, _RED, color)}"
    return f"    {_wrap('✓', _GREEN, color)} {_wrap(summ, _DIM, color)}"


def format_denied(name: str, *, color: bool = True) -> str:
    return f"    {_wrap('⛔ ' + name + ' denied', _YELLOW, color)}"


def error(msg: str, *, color: bool = True) -> str:
    return _wrap("✗ " + msg, _RED, color)


# --- input-box rules ----------------------------------------------------------
def rule(width: int, *, lead: str = "─", label: str = "", color: bool = True) -> str:
    """A horizontal box rule, e.g. `╭─ qwen14 ─────…` (lead='╭', label='qwen14')
    or `╰─────…` (lead='╰'). Used to enclose the REPL input area."""
    start = f"{lead}─ {label} " if label else lead
    body = start + "─" * max(0, width - len(start))
    return _wrap(body, _DIM, color)


def format_nudge(reason: str, *, color: bool = True) -> str:
    return f"    {_wrap('… ' + _truncate(reason, 90), _DIM, color)}"


# --- approval diff preview ----------------------------------------------------
def _proposed_change(name: str, args: dict, cwd: str) -> tuple[str, str, str] | None:
    """Return (path, before, after) for a mutating tool, or None if not one /
    unreadable. write_file => existing vs new; append_file => file vs file plus
    the addition; edit_file => file vs replacement."""
    path = args.get("path")
    if not path or name not in ("write_file", "append_file", "edit_file"):
        return None
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        before = p.read_text("utf-8") if p.exists() else ""
    except OSError:
        return None
    if name == "write_file":
        return str(path), before, args.get("content", "")
    if name == "append_file":
        return str(path), before, before + args.get("content", "")
    # edit_file: resolve through the tool's own matcher so the previewed diff is
    # exactly what will be written (incl. whitespace-tolerant / fuzzy matches).
    from locode.tools.fs import try_edit
    after, _note, status, _count = try_edit(
        before, args.get("old", ""), args.get("new", ""), bool(args.get("replace_all")))
    if status != "ok" or after is None:
        return None
    return str(path), before, after


def format_change(name: str, args: dict, cwd: str, *, color: bool = True,
                  max_lines: int = 40) -> str:
    """A unified-diff preview of a write_file/edit_file proposal, for the ASK
    prompt — so the user approves a concrete change, not just a path. Returns ""
    when there's nothing previewable."""
    change = _proposed_change(name, args, cwd)
    if change is None:
        return ""
    path, before, after = change
    if before == after:
        return ""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                fromfile=path, tofile=path, lineterm="", n=2)
    out = []
    for ln in diff:
        if len(out) >= max_lines:
            out.append(_wrap("    … (diff truncated)", _DIM, color))
            break
        if ln.startswith("+") and not ln.startswith("+++"):
            out.append("    " + _wrap(ln, _GREEN, color))
        elif ln.startswith("-") and not ln.startswith("---"):
            out.append("    " + _wrap(ln, _RED, color))
        elif ln.startswith("@@"):
            out.append("    " + _wrap(ln, _CYAN, color))
        else:
            out.append("    " + _wrap(ln, _DIM, color))
    return "\n".join(out)


# --- per-turn timing / throughput --------------------------------------------
def format_timing(chars: int, elapsed: float, *, color: bool = True) -> str:
    """Dim trailer like `· ~318 tok · 4.1s · 78 tok/s` (tokens approximated from
    streamed characters at ~4 chars/token)."""
    toks = max(1, chars // 4)
    rate = toks / elapsed if elapsed > 0 else 0
    txt = f"· ~{toks} tok · {elapsed:.1f}s"
    if rate:
        txt += f" · {rate:.0f} tok/s"
    return _wrap(txt, _DIM, color)
