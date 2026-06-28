"""Filesystem tools: read_file, ls, glob, grep (read-only) and write_file,
edit_file (mutating). Paths resolve relative to the agent's cwd. Permission
gating and path-scope policy live in permissions.py + the agent loop; these
tools just do the operation and report errors as ToolResults (never raise).
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

from locode.tools.base import ToolContext, ToolResult

_MAX_BYTES = 256 * 1024  # guard against dumping a huge file into context

# A read_file display prefix, e.g. "    12\t" — weak models sometimes copy it
# into `old`. Stripped during tolerant matching so it doesn't block an edit.
_LINENO_PREFIX = re.compile(r"^\s*\d+\t")


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(ctx.cwd) / p
    return p


def _norm_nl(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _match_key(line: str) -> str:
    """Normalize a line for tolerant matching: drop a copied read_file line-number
    prefix and surrounding whitespace, so indentation differences and pasted line
    numbers don't block an otherwise-correct edit."""
    return _LINENO_PREFIX.sub("", line).strip()


def _old_block(old: str) -> list[str]:
    """`old` normalized to its content lines, surrounding blank lines trimmed."""
    lines = _norm_nl(old).split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    while lines and lines[0].strip() == "":
        lines.pop(0)
    return lines


def _line_offsets(lines: list[str]) -> list[int]:
    offsets, pos = [], 0
    for l in lines:
        offsets.append(pos)
        pos += len(l) + 1  # +1 for the '\n' that split() removed
    return offsets


def _span_for(lines, offsets, s, span):
    """Char span for file lines [s, s+span), starting AFTER the first line's
    leading whitespace so the file's original indentation is preserved."""
    lead = len(lines[s]) - len(lines[s].lstrip())
    return offsets[s] + lead, offsets[s + span - 1] + len(lines[s + span - 1])


def _tolerant_spans(text: str, old: str, replace_all: bool):
    """Locate `old` ignoring per-line whitespace, line-ending style, and any
    copied read_file line-number prefix. Returns (start, end) char spans — one
    unless replace_all — or None when there's no/ambiguous match."""
    lines = text.split("\n")
    keys = [_match_key(l) for l in _old_block(old)]
    if not keys:
        return None
    file_keys = [_match_key(l) for l in lines]
    offsets = _line_offsets(lines)
    span = len(keys)
    starts = [s for s in range(len(lines) - span + 1)
              if file_keys[s:s + span] == keys]
    if not starts or (len(starts) > 1 and not replace_all):
        return None
    return [_span_for(lines, offsets, s, span) for s in starts]


def _fuzzy_span(text: str, old: str, threshold: float = 0.8):
    """Best similarity match for `old` when exact/whitespace matching fails (a
    paraphrased line, a tab→spaces line-number, minor drift). Returns
    (start, end, ratio) only when one block is clearly best and above
    `threshold`; None otherwise. Single-region only (never for replace_all)."""
    lines = text.split("\n")
    old_lines = _old_block(old)
    span = len(old_lines)
    if not old_lines or span > len(lines):
        return None
    old_key = "\n".join(_match_key(l) for l in old_lines)
    if not old_key.strip():
        return None
    file_keys = [_match_key(l) for l in lines]
    offsets = _line_offsets(lines)
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(old_key)
    best_r, best_s, second_r = 0.0, None, 0.0
    for s in range(len(lines) - span + 1):
        sm.set_seq1("\n".join(file_keys[s:s + span]))
        r = sm.ratio()
        if r > best_r:
            best_r, best_s, second_r = r, s, best_r
        elif r > second_r:
            second_r = r
    if best_s is None or best_r < threshold or best_r - second_r < 0.05:
        return None  # below bar, or too ambiguous to auto-pick
    start, end = _span_for(lines, offsets, best_s, span)
    return start, end, best_r


def try_edit(text: str, old: str, new: str, replace_all: bool):
    """Resolve an edit across all matching tiers. Returns
    (updated_text|None, note, status, count) with status in
    {'ok', 'ambiguous', 'not_found'}. Shared by edit_file and its diff preview
    so the approved diff is exactly what gets written."""
    count = text.count(old)
    if count > 1 and not replace_all:
        return None, "", "ambiguous", count
    if count >= 1:                                     # tier 1: exact
        return text.replace(old, new), "", "ok", count
    # Span replacements start AFTER the line's original indentation (which is
    # preserved), so drop any leading indentation the model put on `new`'s first
    # line — otherwise the two stack and the line is double-indented.
    new_ins = new.lstrip(" \t")
    spans = _tolerant_spans(text, old, replace_all)   # tier 2: whitespace-tolerant
    if spans is not None:
        updated = text
        for a, b in sorted(spans, reverse=True):
            updated = updated[:a] + new_ins + updated[b:]
        return updated, ", whitespace-tolerant", "ok", len(spans)
    if not replace_all:                               # tier 3: fuzzy (human-gated)
        fz = _fuzzy_span(text, old)
        if fz is not None:
            a, b, ratio = fz
            return text[:a] + new_ins + text[b:], f", fuzzy ~{round(ratio * 100)}%", "ok", 1
    return None, "", "not_found", 0


def _not_found_help(text: str, old: str, path: Path) -> str:
    lines = text.split("\n")
    first = next((l for l in _norm_nl(old).split("\n") if l.strip()), "")
    key = _match_key(first)
    snippet = ""
    if key:
        keyed = [_match_key(l) for l in lines]
        cand = difflib.get_close_matches(key, keyed, n=1, cutoff=0.4)
        if cand:
            idx = keyed.index(cand[0])
            # Show the nearby lines VERBATIM so the model can copy an exact `old`
            # on its next attempt instead of guessing again.
            lo, hi = max(0, idx - 1), min(len(lines), idx + 2)
            block = "\n".join(lines[lo:hi])
            snippet = (f" The nearest text is around line {idx + 1} — copy from "
                       f"here exactly:\n{block}")
    return (f"`old` not found in {path} ({len(lines)} lines). Copy the target text "
            "EXACTLY as it appears in the file — do NOT include read_file's "
            "line-number prefixes — or add more surrounding context to pin it down."
            + snippet)


class ReadFile:
    name = "read_file"
    description = "Read a UTF-8 text file. Returns line-numbered content."
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read."},
            "offset": {"type": "integer", "description": "1-based start line."},
            "limit": {"type": "integer", "description": "Max lines to return."},
        },
        "required": ["path"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args["path"])
        try:
            data = p.read_bytes()
        except FileNotFoundError:
            return ToolResult(f"no such file: {p}", is_error=True)
        except OSError as e:
            return ToolResult(f"cannot read {p}: {e}", is_error=True)
        if len(data) > _MAX_BYTES:
            data = data[:_MAX_BYTES]
            truncated = True
        else:
            truncated = False
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = args.get("limit")
        end = offset - 1 + int(limit) if limit else len(lines)
        chosen = lines[offset - 1:end]
        body = "\n".join(f"{offset + i:>6}\t{ln}" for i, ln in enumerate(chosen))
        if truncated:
            body += "\n… (truncated)"
        return ToolResult(body or "(empty file)")


class Ls:
    name = "ls"
    description = "List the entries of a directory."
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args.get("path", "."))
        if not p.exists():
            return ToolResult(f"no such path: {p}", is_error=True)
        if p.is_file():
            return ToolResult(str(p))
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        except OSError as e:
            return ToolResult(f"cannot list {p}: {e}", is_error=True)
        out = [f"{e.name}/" if e.is_dir() else e.name for e in entries]
        return ToolResult("\n".join(out) or "(empty directory)")


class Glob:
    name = "glob"
    description = "Find files matching a glob pattern (e.g. '**/*.py')."
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Root dir (default cwd)."},
        },
        "required": ["pattern"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        root = _resolve(ctx, args.get("path", "."))
        try:
            matches = sorted(str(m) for m in root.glob(args["pattern"]))
        except (OSError, ValueError) as e:
            return ToolResult(f"glob failed: {e}", is_error=True)
        if not matches:
            return ToolResult("(no matches)")
        return ToolResult("\n".join(matches[:500]))


class Grep:
    name = "grep"
    description = "Search file contents for a regular expression."
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "File or dir root (default cwd)."},
            "glob": {"type": "string", "description": "Restrict to a glob, e.g. '*.py'."},
        },
        "required": ["pattern"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            rx = re.compile(args["pattern"])
        except re.error as e:
            return ToolResult(f"bad regex: {e}", is_error=True)
        root = _resolve(ctx, args.get("path", "."))
        glob = args.get("glob", "**/*")
        files = [root] if root.is_file() else root.glob(glob)
        hits: list[str] = []
        for f in files:
            if not f.is_file():
                continue
            try:
                for n, line in enumerate(f.read_text("utf-8", "replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f}:{n}:{line.strip()[:200]}")
                        if len(hits) >= 200:
                            break
            except OSError:
                continue
            if len(hits) >= 200:
                break
        return ToolResult("\n".join(hits) if hits else "(no matches)")


class WriteFile:
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args["path"])
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], "utf-8")
        except OSError as e:
            return ToolResult(f"cannot write {p}: {e}", is_error=True)
        n = args["content"].count("\n") + 1
        return ToolResult(f"wrote {p} ({n} lines)")


class MoveFile:
    name = "move_file"
    description = "Move or rename a file from a source path to a destination path."
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Source file path."},
            "dst": {"type": "string", "description": "Destination file path."},
        },
        "required": ["src", "dst"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        src = _resolve(ctx, args["src"])
        dst = _resolve(ctx, args["dst"])
        try:
            if not src.exists():
                return ToolResult(f"no such file: {src}", is_error=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError as e:
            return ToolResult(f"cannot move {src} to {dst}: {e}", is_error=True)
        return ToolResult(f"moved {src} to {dst}")


class EditFile:
    name = "edit_file"
    description = (
        "Replace text in a file. `old` is the exact text to replace (copy it "
        "verbatim from the file — do NOT include the line-number prefixes that "
        "read_file prints) and must match once unless replace_all is true. "
        "Keep `old` to the SMALLEST unique snippet that needs changing (a few "
        "lines), NOT the whole file — large blocks waste tokens and risk being "
        "cut off; make several small edit_file calls instead of one giant one. "
        "Indentation/whitespace differences are tolerated; the file's original "
        "indentation is preserved."
    )
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Exact text to replace."},
            "new": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old", "new"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args["path"])
        old, new = args["old"], args["new"]
        try:
            text = p.read_text("utf-8")
        except FileNotFoundError:
            return ToolResult(f"no such file: {p}", is_error=True)
        except OSError as e:
            return ToolResult(f"cannot read {p}: {e}", is_error=True)
        if old == new:
            return ToolResult("old and new are identical; nothing to do", is_error=True)
        replace_all = bool(args.get("replace_all"))

        updated, note, status, count = try_edit(text, old, new, replace_all)
        if status == "ambiguous":
            return ToolResult(
                f"`old` appears {count} times in {p}; pass replace_all or add "
                "more surrounding context to make it unique", is_error=True)
        if status == "not_found":
            return ToolResult(_not_found_help(text, old, p), is_error=True)
        try:
            p.write_text(updated, "utf-8")
        except OSError as e:
            return ToolResult(f"cannot write {p}: {e}", is_error=True)
        return ToolResult(
            f"edited {p} ({count} replacement{'s' if count != 1 else ''}{note})")


def all_tools() -> list:
    """Instances of every fs tool, read-only first."""
    return [ReadFile(), Ls(), Glob(), Grep(), WriteFile(), EditFile(), MoveFile()]
