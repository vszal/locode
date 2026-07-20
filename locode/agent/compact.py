"""Structural, deterministic context compaction — no model calls.

locode's history only shrinks via an explicit action; left alone it grows
without bound across a session (and can grow fast within a single stuck turn
— see AgentConfig.max_history_chars, and the incident that motivated it: a
stuck edit loop grew a local mlx server's prompt cache past 5GB and crashed
it). Compaction keeps a session usable without paying full price for its
history — and unlike Claude Code's own /compact, it never asks a (weak,
local) model to summarize itself: that class of model is exactly what this
whole harness exists to guard against (hallucinated completions, dropped
context, silent stalls — see loop.py's nudge detectors). Everything here is
regex/structural, the same style as toolparse.py and loop.py's stall
detectors.

What's kept vs. discarded loosely mirrors the judgment in CLAUDE.md's own
/compact guidance ("keep the current goal, subgoals, and uncommitted file
changes; discard errant exploration and unused logs"), mapped onto an
agentic tool-call transcript:
  - Keep verbatim: the system prompt, every genuine user prompt (the
    goal/subgoals), a trailing window of recent messages (current work in
    progress), and the receipt of every file change (write_file/edit_file/
    move_file — the "uncommitted file changes" that matter most, and already
    short: "wrote path.py (12 lines)", not the file body).
  - Discard entirely: harness nudges once they're behind the model (pure
    process noise — "errant pathway exploration" that already resolved).
  - Shrink: stale read_file/ls/grep/glob dumps (already used; re-readable if
    needed again) collapse to a one-line summary of which tools ran; large
    tool-CALL argument bodies (e.g. the full file text passed to write_file)
    outside the recent window keep their shape (tool name, path) but not
    their bulk.
"""

from __future__ import annotations

import json
import re

# Fixed substrings from loop.py's _nudge_* methods — used ONLY as a fallback
# to classify messages that predate the "kind" tag (e.g. a session saved by
# an older locode, or a hand-built history with no tag). Newly appended
# messages carry an explicit "kind" and never need this.
_NUDGE_MARKERS = (
    "You replied with an empty message",
    "was cut off before it finished",
    "could not be parsed",
    "issued the same",
    "have NOT changed the error",
    "no write_file or edit_file call",
    "burning the turn's time budget",
)

_FENCE_BLOCK_RE = re.compile(
    r"```(?:tool_call|tool|json)\b[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_TOOL_NAME_RE = re.compile(r"^\[(\w[\w_]*)\]", re.MULTILINE)
_FILE_CHANGE_MARKERS = ("[write_file]", "[edit_file]", "[move_file]")
_SHRINK_ARG_KEYS = ("content", "new", "old")

# An individual JSON string arg (a write_file's full file body, an edit's
# huge `old`/`new`) longer than this is replaced with a placeholder.
_MAX_FIELD_CHARS = 400
# A message that's still this large after arg-shrinking (long free-form
# prose, or a shape we don't recognize) is truncated outright.
_MAX_MESSAGE_CHARS = 800


def estimate_chars(history: list[dict]) -> int:
    """Total content size across all messages — the same cheap chars-as-a-
    token-proxy measure loop.py uses for its history-size budget."""
    return sum(len(m.get("content") or "") for m in history)


def compact_history(history: list[dict], *, keep_recent: int = 8) -> tuple[list[dict], str]:
    """Pure: returns a new list, the caller reassigns `self.history`. The
    system message and the trailing `keep_recent` messages are always left
    untouched; everything older is dropped (nudges), summarized (bulky
    tool-result dumps), or field-shrunk (bulky tool-call args) depending on
    its kind. Idempotent-ish: a second pass over an already-compacted history
    is a cheap no-op for anything it already shrank, since shrunk fields no
    longer exceed the size thresholds.

    Returns (new_history, report) where report is a short human-readable
    "N -> M messages, X -> Y chars" summary for the /compact command and the
    auto-compact log line.
    """
    if not history:
        return history, "nothing to compact (empty history)"
    before_n, before_chars = len(history), estimate_chars(history)

    system = [m for m in history if m.get("role") == "system"]
    body = [m for m in history if m.get("role") != "system"]
    if keep_recent >= len(body):
        return history, (f"nothing to compact ({before_n} messages, "
                         f"{before_chars:,} chars — within the recent window)")
    if keep_recent > 0:
        old, recent = body[:-keep_recent], body[-keep_recent:]
    else:
        old, recent = body, []

    kept: list[dict] = []
    for m in old:
        kind = _kind(m)
        if kind == "nudge":
            continue  # pure process noise once resolved — drop entirely
        if kind == "user_prompt":
            kept.append(m)  # the goal/subgoals — never shrink
        elif kind == "tool_result":
            kept.append(_shrink_tool_result(m))
        else:  # "assistant" (or an unrecognized kind — treat the same way)
            kept.append(_shrink_assistant(m))

    new_history = system + kept + recent
    after_n, after_chars = len(new_history), estimate_chars(new_history)
    report = (f"{before_n} -> {after_n} messages, "
             f"{before_chars:,} -> {after_chars:,} chars")
    return new_history, report


def _kind(msg: dict) -> str:
    """The message's role in the transcript, for compaction purposes. Prefers
    the explicit "kind" tag loop.py sets at append time; falls back to
    structural inference (content shape) for untagged/legacy messages."""
    explicit = msg.get("kind")
    if explicit:
        return explicit
    role = msg.get("role")
    if role != "user":
        return role or "assistant"
    content = msg.get("content") or ""
    if content.startswith("Tool results:"):
        return "tool_result"
    if any(marker in content for marker in _NUDGE_MARKERS):
        return "nudge"
    return "user_prompt"


def _shrink_tool_result(msg: dict) -> dict:
    content = msg.get("content") or ""
    if any(marker in content for marker in _FILE_CHANGE_MARKERS):
        return msg  # a file-change receipt is already short — keep verbatim
    names = list(dict.fromkeys(_TOOL_NAME_RE.findall(content)))
    if not names:
        return _truncate(msg)
    summary = ("Tool results (compacted): " + ", ".join(names) +
              " — output omitted, already used earlier in this session.")
    return {**msg, "content": summary}


def _shrink_assistant(msg: dict) -> dict:
    content = msg.get("content") or ""
    shrunk = _FENCE_BLOCK_RE.sub(_shrink_fenced_block, content)
    if shrunk != content:
        msg = {**msg, "content": shrunk}
        content = shrunk
    if len(content) > _MAX_MESSAGE_CHARS:
        return _truncate(msg)
    return msg


def _shrink_fenced_block(m: re.Match) -> str:
    raw = m.group(1).strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return m.group(0)  # leave malformed/foreign blocks untouched
    args = payload.get("args") if isinstance(payload, dict) else None
    if not isinstance(args, dict):
        return m.group(0)
    changed = False
    for key in _SHRINK_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and len(val) > _MAX_FIELD_CHARS:
            args[key] = f"<{len(val):,} chars omitted by /compact>"
            changed = True
    if not changed:
        return m.group(0)
    return "```tool\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _truncate(msg: dict) -> dict:
    content = msg.get("content") or ""
    if len(content) <= _MAX_MESSAGE_CHARS:
        return msg
    head = content[:_MAX_MESSAGE_CHARS]
    return {**msg, "content": f"{head}\n…[compacted: {len(content):,} chars total]"}
