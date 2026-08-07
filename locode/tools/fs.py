"""Filesystem tools: read_file, ls, glob, grep (read-only) and write_file,
append_file, edit_file, move_file (mutating). Paths resolve relative to the agent's cwd. Permission
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


def _best_block(lines: list[str], old_lines: list[str]):
    """Scan the file for the region most similar to `old`, with NO threshold.

    Returns (start_index, best_ratio, runner_up_ratio) or None when there is
    nothing to score against. Two callers with opposite needs share this:
    `_fuzzy_span` applies the accept/ambiguity gate on top and only edits when
    it passes, while `_not_found_help` wants the raw winner — on a failed match
    the model needs to be shown where the text most likely lives *precisely
    when* the ratio was too low to act on."""
    span = len(old_lines)
    if not old_lines or span > len(lines):
        return None
    old_key = "\n".join(_match_key(l) for l in old_lines)
    if not old_key.strip():
        return None
    file_keys = [_match_key(l) for l in lines]
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
    if best_s is None:
        return None
    return best_s, best_r, second_r


def _fuzzy_span(text: str, old: str, threshold: float = 0.8):
    """Best similarity match for `old` when exact/whitespace matching fails (a
    paraphrased line, a tab→spaces line-number, minor drift). Returns
    (start, end, ratio) only when one block is clearly best and above
    `threshold`; None otherwise. Single-region only (never for replace_all)."""
    lines = text.split("\n")
    old_lines = _old_block(old)
    hit = _best_block(lines, old_lines)
    if hit is None:
        return None
    best_s, best_r, second_r = hit
    if best_r < threshold or best_r - second_r < 0.05:
        return None  # below bar, or too ambiguous to auto-pick
    offsets = _line_offsets(lines)
    start, end = _span_for(lines, offsets, best_s, len(old_lines))
    return start, end, best_r


def try_edit(text: str, old: str, new: str, replace_all: bool):
    """Resolve an edit across all matching tiers. Returns
    (updated_text|None, note, status, count) with status in
    {'ok', 'ambiguous', 'not_found', 'empty_old', 'noop'}. Shared by edit_file and
    its diff preview so the approved diff is exactly what gets written.

    'noop' means a tier matched but the replacement leaves the file byte-for-byte
    unchanged — the usual cause is `old`/`new` differing ONLY in leading
    indentation, which the whitespace-tolerant tier strips (it preserves each
    matched line's original indent). Reporting that as success makes a model
    believe it fixed something and loop; surfacing it lets the caller steer."""
    if old == "":
        # `"".count` is len(text)+1 and `text.replace("", new)` inserts `new`
        # between EVERY character — a ~len(new)x blowup per call. A model that
        # means "add this function" reaches for old="" naturally, so this is
        # reachable input, not a hypothetical. Refuse it before any tier runs.
        return None, "", "empty_old", 0
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
        note = ", whitespace-tolerant"
        if updated == text:                           # indent-only "change" -> no change
            return None, note, "noop", len(spans)
        return updated, note, "ok", len(spans)
    if not replace_all:                               # tier 3: fuzzy (human-gated)
        fz = _fuzzy_span(text, old)
        if fz is not None:
            a, b, ratio = fz
            updated = text[:a] + new_ins + text[b:]
            note = f", fuzzy ~{round(ratio * 100)}%"
            if updated == text:
                return None, note, "noop", 1
            return updated, note, "ok", 1
    return None, "", "not_found", 0


def _already_applied(text: str, new: str) -> bool:
    """True when `new` is already present in `text` — i.e. an edit that "can't
    find `old`" can't find it because the change has ALREADY been made and `old`
    is gone. The classic weak-model loop: it applies a fix, verifies it works,
    then re-submits the identical edit; `old` no longer exists, a plain not-found
    reads as a fixable error, and the model reverts its own working fix. Detecting
    this lets edit_file answer "already done" (non-error) instead.

    Guarded by a minimum content length so a trivial `new` (a bracket, a short
    token) that happens to occur elsewhere can't mask a genuine not-found. Checks
    exact presence first, then whitespace-tolerant (the fix may have been written
    at a different indent than `new` carries)."""
    if len(new.strip()) < 3:
        return False
    if new in text:
        return True
    return _tolerant_spans(text, new, False) is not None


def _same_content(a: str, b: str) -> bool:
    """True when `a` and `b` differ only in per-line whitespace or a copied
    read_file line-number prefix — the indent-only case edit_file can't serve.
    Used to tell an indent-only no-op (keep steering to replace_lines) apart from
    an already-applied edit (old was the OLD content, new is the fix, and the
    file now holds new): the latter has genuinely different content."""
    return [_match_key(l) for l in _old_block(a)] == \
        [_match_key(l) for l in _old_block(b)]


def _match_locations(text: str, old: str, limit: int = 6) -> str:
    """List where `old` occurs, as `line N: <first line of the match>`, so a
    model that matched several places can add context to pin the one it means.
    Capped at `limit` so a pattern that appears everywhere can't flood the reply."""
    first = old.split("\n", 1)[0]
    out, start, shown = [], 0, 0
    while shown < limit:
        i = text.find(old, start)
        if i < 0:
            break
        line_no = text.count("\n", 0, i) + 1
        out.append(f"  line {line_no}: {first.strip()[:100]}")
        start = i + max(1, len(old))
        shown += 1
    if text.count(old) > shown:
        out.append(f"  … and {text.count(old) - shown} more")
    return "\n".join(out)


# When byte-exact matching is the wrong tool. edit_file can't target text a weak
# model can't reproduce verbatim — a line with a literal backslash, stray quotes,
# or odd whitespace (observed: a malformed docstring `""\"` made every `old`
# collapse to `old == new`). The escape hatch is replace_lines, which needs only
# the line NUMBER, not the bytes. Appended to the not-found / no-op errors so the
# model switches tools instead of re-guessing the same `old`.
_TRY_REPLACE_LINES = (
    " If the target text is hard to reproduce EXACTLY — it has backslashes, "
    "quotes, or unusual whitespace — stop guessing at `old`: use replace_lines "
    "instead, giving the line NUMBER (from read_file) rather than the text."
)


# Lines of real file content shown each side of the located region.
#
# This was 12 in build 90 and is 1 again as of build 92, because the wide
# window MEASURED HARMFUL. On b90-editwindow's only fully-exposed case
# (exec-bugfix, 5/5 runs hit it) qwencoder14 went from 0 to 4 surrenders out of
# 5 — replying "I cannot make progress... I do not have enough context" and
# ending the turn, having landed zero edits. The baseline kept working the
# problem for 13 iterations; the wide-window arm quit after 7. A 60-line code
# block inside an ERROR seems to read to a 14B model as a listing to discuss
# rather than a target to copy from.
#
# Kept as a parameter, not inlined, because the hypothesis is still live — the
# right width may just be smaller. `_not_found_help(..., window=N)` takes it,
# so an A/B is a one-line edit. What is NOT reverted: block-level location
# (`_best_block`) and the wrong-file branch, which are better targeting at any
# width and were not implicated.
_HELP_WINDOW = 1
_HELP_MAX_LINES = 60   # ... and never flood the reply with a whole file


def _not_found_help(text: str, old: str, path: Path, *,
                    window: int = _HELP_WINDOW) -> str:
    """The reply when `old` didn't match — always hands back the file's ACTUAL
    text around the likeliest region.

    A model told only "not found" has two moves: spend a `read_file`
    round-trip, or guess again — and guessing again is how the no-op edit loops
    start. Showing real content is what turns the retry into a copy. Two
    deliberate choices: the block is located by scoring `old` as a *block*
    (`_best_block`, the same scan the fuzzy tier uses) rather than by its first
    line alone, which is unreliable when that line is something generic like a
    bare `return`; and it is printed VERBATIM and unnumbered, because this same
    message tells the model not to put line-number prefixes in `old`, so it has
    to be able to copy straight out of what we show it."""
    lines = text.split("\n")
    old_lines = _old_block(old)
    idx = span = None
    confident = False
    hit = _best_block(lines, old_lines)
    if hit is not None:
        idx, ratio, _second = hit
        span, confident = len(old_lines), ratio >= 0.5
    else:
        # `old` outruns the file, or is blank once normalized — fall back to its
        # first content line so we still point somewhere useful.
        first = next((l for l in _norm_nl(old).split("\n") if l.strip()), "")
        key = _match_key(first)
        if key:
            keyed = [_match_key(l) for l in lines]
            cand = difflib.get_close_matches(key, keyed, n=1, cutoff=0.4)
            if cand:
                idx, span, confident = keyed.index(cand[0]), 1, True
    snippet = ""
    if idx is not None:
        lo = max(0, idx - window)
        hi = min(len(lines), idx + span + window)
        block, tail = lines[lo:hi], ""
        if len(block) > _HELP_MAX_LINES:
            tail = f"\n… ({len(block) - _HELP_MAX_LINES} more lines)"
            block = block[:_HELP_MAX_LINES]
        lead = ("The closest match is" if confident
                else "No close match. The most similar region is")
        snippet = (f" {lead} at lines {lo + 1}-{lo + len(block)} — this is what "
                   "the file ACTUALLY contains there. Copy your `old` out of it "
                   "verbatim:\n" + "\n".join(block) + tail)
    else:
        # Nothing scored above zero: `old` doesn't resemble ANY region. That is
        # a different failure from "close but drifted" and deserves a different
        # instruction — it usually means the wrong file, or one already changed.
        snippet = (" NOTHING in this file resembles `old` — you are probably "
                   "editing the wrong file, or it has already changed. Re-read "
                   "it with read_file before trying again.")
    return (f"`old` not found in {path} ({len(lines)} lines). Copy the target text "
            "EXACTLY as it appears in the file — do NOT include read_file's "
            "line-number prefixes — or add more surrounding context to pin it down."
            + snippet + _TRY_REPLACE_LINES)


def _mark_seen(ctx: ToolContext, p: Path) -> None:
    """Record that the model has now seen this file's contents."""
    if ctx.seen_files is not None:
        ctx.seen_files.add(str(p))


def _unseen_help(ctx: ToolContext, p: Path) -> "ToolResult | None":
    """Refuse a content-anchored edit to a file the model has never read.

    The measured root cause behind the edit-failure loops (b90 corpus,
    exec-bugfix): the model reconstructs the target function from a pytest
    traceback and edits from memory, so `old` never matches anything. Across
    all 10 runs of both arms it landed at most ONE successful edit and fixed
    none of the three seeded bugs; one run went pytest, pytest, edit, edit with
    no read_file at all. Every downstream lever — better failure messages, a
    wider window, a laxer repeat guard — was treating a symptom of this.

    A read costs one iteration. The guess-loop cost five to seven and ended in
    surrender. Returns None when the gate is off, the file was already seen, or
    it doesn't exist (in which case the plain "no such file" is clearer)."""
    if ctx.seen_files is None or str(p) in ctx.seen_files or not p.exists():
        return None
    return ToolResult(
        f"You have NOT read {p} yet, so you cannot know the exact text it "
        "contains. Call read_file on it FIRST, then copy `old` verbatim from "
        "what read_file returns. Do not reconstruct the code from a traceback, "
        "from the tests, or from memory — text you did not copy is why edits "
        "fail to match.", is_error=True)


def _edit_snippet(before: str, after: str, *, context: int = 3,
                  max_lines: int = 24) -> str:
    """A line-numbered view of the region an edit changed, in read_file's format.

    A model that only hears "edited (1 replacement)" is blind to the file's new
    state: on its next turn it re-targets text that has already changed (a
    not-found loop) or believes its edit was a no-op (observed in eval). Echoing
    the changed lines back — numbered exactly like read_file — lets it build an
    accurate follow-up `old` from what is actually on disk. Capped so a large
    edit can't flood the reply."""
    b, a = before.split("\n"), after.split("\n")
    ops = difflib.SequenceMatcher(None, b, a, autojunk=False).get_opcodes()
    changed = [(j1, j2) for tag, _i1, _i2, j1, j2 in ops if tag != "equal"]
    if not changed:
        return ""
    lo = max(0, changed[0][0] - context)
    hi = min(len(a), changed[-1][1] + context)
    window = a[lo:hi]
    out = []
    for i, ln in enumerate(window):
        if len(out) >= max_lines:
            out.append(f"       … ({len(window) - i} more lines)")
            break
        out.append(f"{lo + i + 1:>6}\t{ln}")
    return "\n".join(out)


def _syntax_warning(path: Path, text: str) -> str:
    """A one-line SyntaxError note to append to a successful .py write, or "".

    A model that writes malformed Python only learns of it later, as an opaque
    pytest *collection* traceback (`<frozen importlib>` … ERROR collecting), which
    is far harder to act on than "line 42: invalid syntax". Compiling the file the
    instant it lands surfaces the real location while the fix is one call away.
    Advisory only — the write already succeeded, and a partial file mid-build may
    legitimately not parse yet, so this never turns the result into an error.
    """
    if path.suffix != ".py":
        return ""
    try:
        compile(text, str(path), "exec")
    except SyntaxError as e:
        where = f"line {e.lineno}" if e.lineno else "an unknown line"
        bad = (e.text or "").strip()
        detail = f"\n    {bad}" if bad else ""
        return (f"\n⚠ warning: {path.name} has a SyntaxError at {where}: "
                f"{e.msg}.{detail}\nThe file was saved, but Python cannot import "
                "it until this is fixed — correct this line before running tests.")
    except ValueError:
        # e.g. source with null bytes; compile() raises ValueError, not SyntaxError.
        return (f"\n⚠ warning: {path.name} could not be parsed as Python. The file "
                "was saved, but check it for stray/invalid characters.")
    return ""


def _parses_py(text: str, path: Path) -> bool:
    try:
        compile(text, str(path), "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def _changed_span(before: str, after: str) -> tuple[int, int]:
    """1-based inclusive line span of `after` holding the text the edit supplied.

    Found by trimming the common prefix and suffix, which works for every editing
    tool without threading their differing arguments (old/new, start/end) down
    here. When the edit was a pure deletion the span is empty and `hi < lo`, with
    `lo` marking where the removed text used to begin.
    """
    b, a = before.split("\n"), after.split("\n")
    p = 0
    while p < len(b) and p < len(a) and b[p] == a[p]:
        p += 1
    s = 0
    while (s < len(b) - p and s < len(a) - p
           and b[len(b) - 1 - s] == a[len(a) - 1 - s]):
        s += 1
    return p + 1, len(a) - s


def _stranded_run(lines: list[str], err_line: int) -> int:
    """Last line of the indented run starting at `err_line` (1-based, inclusive).

    Used only to say *how much* looks left over. The run is the maximal block of
    lines at least as indented as the offending one, blanks included, which is
    what the tail of a half-replaced function looks like.
    """
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip())

    if not 1 <= err_line <= len(lines):
        return err_line
    base, end = indent(lines[err_line - 1]), err_line
    for i in range(err_line, len(lines)):
        if not lines[i].strip():
            continue
        if indent(lines[i]) < base:
            break
        end = i + 1
    return end


def _seam_window(lines: list[str], hi: int, err_line: int) -> str:
    """Render the junction between the supplied text and what follows it."""
    out = []
    # A trailing newline leaves an empty final element; showing it as a numbered
    # line is noise in a window whose whole job is to be read closely.
    last = len(lines) - 1 if lines and not lines[-1].strip() else len(lines)
    for n in range(max(1, min(hi, err_line) - 2), min(last, err_line + 2) + 1):
        mark = ("   <- your text ends here" if n == hi else
                "   <- SyntaxError here" if n == err_line else "")
        out.append(f"  {n:>4} | {lines[n - 1]}{mark}")
    return "\n".join(out)


def _syntax_reject(path: Path, before: str, after: str) -> str | None:
    """A rejection message if this edit would turn PARSEABLE Python into a
    SyntaxError, else None. The guard behind it: a malformed edit (unmatched
    bracket, stray paren, bad indent) that merely *warned* and still landed
    corrupted the file, and weak models then spent the whole turn fighting a
    broken file they couldn't dig out of (gemmacoder12, 2026-07-26). Refusing to
    apply it keeps the file in the last-good state the model already read.

    Scoped tight so it never blocks legitimate work: only .py files, and only the
    valid→invalid transition. If the file did NOT parse *before* the edit, the
    model is presumably fixing a syntax error (the empty-with-block case), so any
    edit is allowed through — the advisory `_syntax_warning` covers that path.
    """
    if path.suffix != ".py":
        return None
    if not _parses_py(before, path) or _parses_py(after, path):
        return None
    lineno = None
    try:
        compile(after, str(path), "exec")
    except SyntaxError as e:
        lineno = e.lineno
        where = f"line {e.lineno}" if e.lineno else "an unknown line"
        bad = (e.text or "").strip()
        detail = f"\n    {bad}" if bad else ""
        msg = e.msg
    except ValueError:
        where, detail, msg = "an unknown line", "", "invalid characters"

    head = (f"NOT applied — this edit would introduce a SyntaxError at {where}: "
            f"{msg}.{detail}\nThe file is UNCHANGED (still the version you read).")

    # Where the break actually is decides what to tell the model, and getting
    # this wrong is expensive. The old message blamed `new` unconditionally.
    # Across the archived rejections whose span is knowable, 31 of 58 broke
    # OUTSIDE the supplied text — always at the line immediately after it — so
    # the model was sent to re-inspect text that was fine, found nothing wrong
    # (correctly), and resent it byte-identical until the repeat guard killed
    # the turn. 12 of 68 recent repeat-stop deaths start here.
    lo, hi = _changed_span(before, after)
    lines = after.split("\n")
    if lineno and not (lo <= lineno <= hi):
        last = _stranded_run(lines, lineno)
        extent = (f"Lines {lineno}-{last} look like the leftover tail"
                  if last > lineno else f"Line {lineno} looks like a leftover")
        return (head + "\n\nThe error is NOT inside the text you supplied"
                + (f" (lines {lo}-{hi})" if hi >= lo else "")
                + f" — it is at line {lineno}, "
                + ("just after it" if lineno > hi else "just before it") + ". "
                "The region you replaced ended in the middle of a block, so part "
                "of the old code is now stranded with nothing to attach to.\n\n"
                + _seam_window(lines, hi, lineno)
                + f"\n\n{extent} of the block you were replacing. Re-read the "
                "file, then either extend the region so it covers that whole "
                "block or include those lines in your replacement. Changing "
                "only the text you already sent will NOT fix this — do not "
                "resend the same edit.")
    return (head + " Your `new` text is malformed — most often an unmatched "
            "bracket or paren, or a broken indent. Re-read the file, correct "
            "`new`, and try one more time. Do NOT resend the same broken edit.")


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
        # A truncated or windowed read still counts: the gate is a floor
        # against editing text the model never saw, not a guarantee that it saw
        # the right part.
        _mark_seen(ctx, p)
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
    # The size sentence is not style advice, it is the only brake that acts
    # BEFORE generation. A model asked for a long document generates flat into
    # the token ceiling, the JSON is cut mid-string, and ~450 seconds are gone
    # before any nudge can say so. Round 8 shipped append_file and a truncation
    # nudge naming it; across 36 eval runs append_file was called zero times,
    # because by the time the nudge fires the turn is over. The instruction has
    # to be in the tool the model is about to call.
    #
    # Keep the flat number. What is actually known, and what is not:
    #
    # qythos9's reply length on design-doc is BIMODAL — it either obeys and
    # writes 11-14k chars, or ignores the cap and writes 33-42k, in which case
    # the reply is truncated and no document lands at all. Longest reply per
    # run, by round:
    #     r9  "about 6000", flat     11,994 / 12,271 / 13,911   <- short x3
    #     r10 softened to 8000       36,563 / 41,560 / 33,774   <- long x3
    #     r11 "about 6000", flat     39,969 / 32,718 / 11,206   <- short x1
    #
    # r11 repeated r9 EXACTLY and drew the short mode once. So the flat number
    # has 4/6 short and the softened branch 0/3 — suggestive (p ~ 0.19, Fisher)
    # but not significant. Round 10's write-up called this settled and told the
    # next reader not to revisit it; that was wrong, and r11 is why.
    #
    # Note the model never obeys 6000 either way (its "short" mode is ~12k), so
    # if the number helps it is by pulling the target down, not by being read as
    # a limit. Treat that as the working hypothesis, not a result.
    #
    # Before changing this sentence, raise n — six runs cannot separate a 40/60
    # split from an effect. See LOG.md D44, superseded by D49 and D51.
    description = (
        "Create or overwrite a file with the given content. Keep `content` "
        "under about 6000 characters. If the document you are writing is "
        "longer than that, write_file only its first section now and add each "
        "remaining section with a separate append_file call — never try to "
        "emit the whole document in one call."
    )
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {
                "type": "string",
                "description": "File body. Keep under ~6000 characters; "
                               "continue longer documents with append_file.",
            },
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
        # Authoring the whole body counts as seeing it — the model has the exact
        # text it just sent, so a follow-up edit_file is anchored, not guessed.
        # Only write_file earns this; append_file does not, because the model
        # knows what it added and nothing about the lines above it.
        _mark_seen(ctx, p)
        n = args["content"].count("\n") + 1
        return ToolResult(f"wrote {p} ({n} lines)" + _syntax_warning(p, args["content"]))


class AppendFile:
    name = "append_file"
    description = (
        "Append content to the END of an existing file. Use this to write a "
        "long document in pieces: write_file the first section, then "
        "append_file each following section. The file must already exist — "
        "create it with write_file first."
    )
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string",
                        "description": "Text to add at the end of the file."},
        },
        "required": ["path", "content"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args["path"])
        # Deliberately NOT create-if-missing, unlike shell `>>`. A model that
        # appends to PLAN.md having written plan.md would otherwise end up with
        # two half-documents and no error, and the deliverable check would fail
        # for a reason nothing in the transcript explains. Failing here names
        # the mistake while it is still one call old.
        if not p.exists():
            return ToolResult(
                f"no such file: {p} — append_file only adds to a file that "
                "already exists. Create it with write_file first.",
                is_error=True)
        content = args["content"]
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            return ToolResult(f"cannot append to {p}: {e}", is_error=True)
        added = content.count("\n") + (0 if content.endswith("\n") else 1)
        try:
            full = p.read_text("utf-8")
            total = full.count("\n") + 1
        except OSError:
            full, total = "", added
        warn = _syntax_warning(p, full) if full else ""
        return ToolResult(
            f"appended {added} lines to {p} ({total} lines total)" + warn)


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
        "Content-anchored editor — matches on the text itself, so it can't drift "
        "the way line-number edits do. Best for CONTENT changes (renaming, "
        "rewording, fixing an expression) AND the PREFERRED way to DELETE code. "
        "Replace text in a file. `old` is the "
        "exact text to replace (copy it verbatim from the file — do NOT include "
        "the line-number prefixes that read_file prints) and must match once "
        "unless replace_all is true. "
        "`new` is the REPLACEMENT and must DIFFER from `old`. To DELETE lines, set "
        "`new` to an empty string and put the exact line(s) to remove — including "
        "their trailing newline — in `old`; deleting this way is SHIFT-IMMUNE: "
        "removing one block leaves the next block's anchor text intact, so several "
        "separate deletes each still match (line-number deletes renumber every "
        "line below and drift). Otherwise `new` has to carry your actual change; "
        "an edit whose `new` equals `old` does nothing and is "
        "rejected. Keep `old` to the SMALLEST unique snippet that needs changing "
        "(a few lines), NOT the whole file — large blocks waste tokens and risk "
        "being cut off; make several small edit_file calls instead of one giant "
        "one. IMPORTANT: this editor PRESERVES the file's existing indentation "
        "and tolerates whitespace differences, so it CANNOT make an "
        "indentation-only or whitespace-only change — such an edit collapses to a "
        "no-op and is rejected. To re-indent a line or fix a broken indent, use "
        "replace_lines instead."
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
        # A missing `old`/`new` used to raise KeyError, which the loop surfaced as
        # the opaque "edit_file failed: KeyError: 'new'" — a call the model can't
        # learn from. Name the missing field and what it's for so the retry is
        # informed. (Observed: a model sending edit_file with `old` but no `new`.)
        if "old" not in args or "new" not in args:
            missing = "old" if "old" not in args else "new"
            return ToolResult(
                f"edit_file is missing the required `{missing}` field. Provide "
                "both `old` (the exact text to replace) and `new` (the replacement "
                "text, which must DIFFER from `old`).", is_error=True)
        if (blocked := _unseen_help(ctx, p)) is not None:
            return blocked
        old, new = args["old"], args["new"]
        try:
            text = p.read_text("utf-8")
        except FileNotFoundError:
            return ToolResult(f"no such file: {p}", is_error=True)
        except OSError as e:
            return ToolResult(f"cannot read {p}: {e}", is_error=True)
        if old == new:
            return ToolResult(
                "This edit does NOTHING: `new` is identical to `old`. To change "
                "the file, `new` must be DIFFERENT text — put the corrected code "
                "there. If you are fixing a bug, re-read the exact failure (the "
                "test or traceback names the wrong line and what is wrong with "
                "it), then submit an edit whose `new` carries that correction. If "
                "this line is already correct, the bug is on a DIFFERENT line — "
                "stop editing this one and look elsewhere. Do NOT resend this "
                "same no-op edit." + _TRY_REPLACE_LINES,
                is_error=True, no_change=True)
        replace_all = bool(args.get("replace_all"))

        updated, note, status, count = try_edit(text, old, new, replace_all)
        if status == "empty_old":
            return ToolResult(
                "`old` is empty, so there is nothing to replace. To ADD text, "
                "use append_file to put it at the end of the file, or write_file "
                "to replace the whole file. To CHANGE text, copy the exact "
                "existing lines into `old`.", is_error=True)
        if status == "ambiguous":
            return ToolResult(
                f"`old` appears {count} times in {p}, so it is not clear which "
                "one to change. Either add more surrounding lines to `old` so it "
                "matches exactly ONE place, or pass replace_all to change every "
                "one. The matches are at:\n" + _match_locations(text, old),
                is_error=True)
        # `old` didn't produce a real change (it's not in the file, or a tolerant/
        # fuzzy match landed on a line that's byte-identical to `new`). Two shapes
        # of "already done" are answered as a NON-error so the model finishes
        # instead of reading a fixable error and thrashing:
        #  1. REPLACEMENT already applied — `new` is already present AND differs in
        #     content from `old` (an earlier step made this change; a plain
        #     not-found would drive the model to revert its own working fix).
        #     `_same_content` keeps a genuine indent-only no-op on its steer below.
        #  2. DELETION already done — `new` is empty and `old` (non-empty) has no
        #     exact/tolerant/FUZZY match, i.e. the lines to remove are already gone
        #     (deleting absent content is a no-op whose goal already holds). Left as
        #     an error, its _not_found_help even suggests replace_lines — and a
        #     line-number re-delete lands on SHIFTED lines and corrupts the file
        #     (observed: remove-block, gemma re-deleted an already-gone DEBUG line,
        #     escalated to replace_lines, and over-deleted the return).
        deletion_done = new.strip() == "" and old.strip() != ""
        replacement_done = _already_applied(text, new) and not _same_content(old, new)
        if status in ("not_found", "noop") and (deletion_done or replacement_done):
            return ToolResult(
                "This edit is ALREADY DONE: the file already reflects it — either "
                "`new` is already present, or (for a deletion) the lines in `old` "
                "are already gone. Nothing to do. Do NOT re-apply or re-delete it, "
                "do NOT revert it, and do NOT switch to line-number edits to force "
                "it. Move on to the next step, or if the task is done, finish.",
                no_change=True)
        if status == "not_found":
            return ToolResult(_not_found_help(text, old, p), is_error=True)
        if status == "noop":
            return ToolResult(
                "This edit changed NOTHING: `old` matched, but after "
                "whitespace-tolerant matching the file is byte-for-byte identical "
                "to before. Almost always `old` and `new` differ ONLY in leading "
                "indentation — and edit_file preserves each matched line's "
                "ORIGINAL indentation, so an indent-only change can't be made this "
                "way. To re-indent a block, use replace_lines (give the line "
                "numbers from read_file). If you "
                "meant to change the code, make `new` differ from the file in more "
                "than whitespace. If the line is already correct, stop editing it "
                "and look elsewhere. Do NOT resend this same edit."
                + _TRY_REPLACE_LINES, is_error=True, no_change=True)
        broke = _syntax_reject(p, text, updated)
        if broke:
            return ToolResult(broke, is_error=True)
        try:
            p.write_text(updated, "utf-8")
        except OSError as e:
            return ToolResult(f"cannot write {p}: {e}", is_error=True)
        snippet = _edit_snippet(text, updated)
        body = f"edited {p} ({count} replacement{'s' if count != 1 else ''}{note})"
        if snippet:
            body += "\nThe file now reads (changed region):\n" + snippet
        return ToolResult(body + _syntax_warning(p, updated))


def try_replace_lines(text: str, start, end, new: str):
    """Replace 1-based lines [start, end] (inclusive) with `new`, returning
    (updated_text|None, status) with status in {"ok", "bad_range"}.

    The line numbers are read_file's — the counterpart to edit_file's exact-text
    match, for the case a model can SEE the target line but cannot reproduce its
    exact bytes (a malformed/odd-whitespace line). `new` == "" deletes the range;
    otherwise its lines are inserted verbatim. A trailing newline is preserved.
    Factored out so the approval-diff preview computes the same result the tool
    will write. `start`/`end` are coerced from str for tolerance."""
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return None, "bad_range"
    lines = text.splitlines()
    n = len(lines)
    if start < 1 or start > n or end < start:
        return None, "bad_range"
    end = min(end, n)
    new_lines = new.split("\n") if new != "" else []
    result = lines[: start - 1] + new_lines + lines[end:]
    out = "\n".join(result)
    if text.endswith("\n") and out:
        out += "\n"
    return out, "ok"


class ReplaceLines:
    name = "replace_lines"
    description = (
        "Replace a RANGE OF LINES by their line numbers. This is the RIGHT tool "
        "for an indentation or whitespace fix (edit_file preserves the file's "
        "existing indentation and would no-op) and for text edit_file cannot "
        "match exactly (a malformed line, odd bytes, or a snippet that isn't "
        "unique). To DELETE code, PREFER edit_file with an empty `new` — deleting "
        "by line number is the classic trap: each delete renumbers every line "
        "below it, so removing several blocks by number lands on the wrong lines "
        "and over-deletes or duplicates content. `start` and `end` are 1-based "
        "inclusive "
        "line numbers exactly "
        "as read_file prints them (do NOT include the number prefixes in `new`). "
        "`new` is the replacement text for that whole range; an empty string "
        "deletes the range, but only reach for that on a SINGLE hard-to-match "
        "line and re-read immediately before the call. CRITICAL: line numbers go "
        "STALE the instant any "
        "edit shifts the file — re-read the file immediately before EACH call to "
        "get current numbers, and never re-issue the same start/end after an "
        "edit, or you will hit different text and DUPLICATE content. Do NOT put a "
        "trailing newline in `new` unless you mean to add a blank line."
    )
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start": {"type": "integer", "description": "1-based first line to replace."},
            "end": {"type": "integer", "description": "1-based last line, inclusive."},
            "new": {"type": "string", "description": "Replacement text ('' deletes)."},
        },
        "required": ["path", "start", "end", "new"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = _resolve(ctx, args["path"])
        # Gated before anything else: `start`/`end` are meaningless unless they
        # were copied from a read_file of THIS file. A line number invented from
        # a traceback is the same guess edit_file makes with `old`, minus the
        # exact-match check that would have caught it.
        if (blocked := _unseen_help(ctx, p)) is not None:
            return blocked
        try:
            text = p.read_text("utf-8")
        except FileNotFoundError:
            return ToolResult(f"no such file: {p}", is_error=True)
        except OSError as e:
            return ToolResult(f"cannot read {p}: {e}", is_error=True)
        try:
            start, end = int(args["start"]), int(args["end"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(
                "replace_lines needs integer `start` and `end` line numbers (1-based, "
                "inclusive) copied from read_file's output.", is_error=True)
        # `new` is required — defaulting a missing one to "" would silently DELETE
        # the range, which a model that merely forgot the field never intended.
        if "new" not in args:
            return ToolResult(
                "replace_lines is missing the required `new` field (the replacement "
                "text for the range; pass an empty string only if you truly mean to "
                "DELETE those lines).", is_error=True)
        new = args["new"]
        n = len(text.splitlines())
        updated, status = try_replace_lines(text, start, end, new)
        if status != "ok" or updated is None:
            return ToolResult(
                f"can't replace lines {start}–{end}: {p} has {n} line"
                f"{'s' if n != 1 else ''}. `start` and `end` are 1-based inclusive "
                f"line numbers from read_file, needing 1 ≤ start ≤ end and start ≤ "
                f"{n}. Re-read the file to get current line numbers (a prior edit "
                "may have shifted them).", is_error=True)
        if updated == text:
            return ToolResult(
                "This replacement is ALREADY IN PLACE: `new` is identical to what "
                "is already on those lines, so there is nothing to change — the "
                "code is already what you want. Nothing to do. Do NOT resend this "
                "same replacement and do NOT revert it. Move on to the next step, "
                "or if the task is done, finish.",
                no_change=True)
        broke = _syntax_reject(p, text, updated)
        if broke:
            return ToolResult(broke, is_error=True)
        try:
            p.write_text(updated, "utf-8")
        except OSError as e:
            return ToolResult(f"cannot write {p}: {e}", is_error=True)
        snippet = _edit_snippet(text, updated)
        body = f"replaced lines {start}–{min(end, n)} in {p}"
        if snippet:
            body += "\nThe file now reads (changed region):\n" + snippet
        return ToolResult(body + _syntax_warning(p, updated))


def all_tools() -> list:
    """Instances of every fs tool, read-only first."""
    return [ReadFile(), Ls(), Glob(), Grep(), WriteFile(), AppendFile(),
            EditFile(), ReplaceLines(), MoveFile()]
