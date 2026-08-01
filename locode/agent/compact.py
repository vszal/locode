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
    progress), and the receipt of every file change (write_file/append_file/
    edit_file/move_file — the "uncommitted file changes" that matter most, and already
    short: "wrote path.py (12 lines)", not the file body).
  - Discard entirely: harness nudges once they're behind the model (pure
    process noise — "errant pathway exploration" that already resolved).
  - Shrink: stale read_file/ls/grep/glob dumps (already used; re-readable if
    needed again) collapse to a one-line summary of which tools ran; large
    tool-CALL argument bodies (e.g. the full file text passed to write_file)
    outside the recent window keep their shape (tool name, path) but not
    their bulk.
  - Collapse: a prose reply the model has already sent verbatim is kept ONCE
    and marked with how often it recurred (see _dedupe_stale_claims).

THE RATCHET (2026-08-01, user-reported session). The three rules above are
individually sensible and together produced the product's worst failure mode.
Shrinking a tool result to "output omitted" while keeping any assistant reply
under _MAX_MESSAGE_CHARS *verbatim* inverts the fidelity: the model's CLAIM
about the evidence outlived the evidence itself. And because a short confident
summary is always under the threshold, every restatement of it also survived —
so a wrong conclusion accumulated copies while the output that would refute it
was stripped. Measured on the reported session's shape: 44,591 -> 1,865 chars,
of which the survivors were six identical copies of one stale, wrong summary
interleaved with "output omitted" placeholders. The model then had no way back
— it was being fed its own error as the best-attested thing in context.

Hence the two rules that break it: repeated prose collapses to a single
annotated copy (accumulation is capped at one), and a collapsed tool result
now says its conclusions are stale rather than implying they still stand.
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
_FILE_CHANGE_MARKERS = ("[write_file]", "[append_file]", "[edit_file]",
                        "[move_file]")
_SHRINK_ARG_KEYS = ("content", "new", "old")

# An individual JSON string arg (a write_file's full file body, an edit's
# huge `old`/`new`) longer than this is replaced with a placeholder.
_MAX_FIELD_CHARS = 400
# A message that's still this large after arg-shrinking (long free-form
# prose, or a shape we don't recognize) is truncated outright.
_MAX_MESSAGE_CHARS = 800

# A single message inside the "recent" window this large is exactly the "one
# huge read balloons the context" case compaction must still catch, even
# though everything else in the window is left completely untouched. Well
# above _MAX_MESSAGE_CHARS so ordinary recent turns are never affected.
_RECENT_SHRINK_THRESHOLD = 4000

# Below this length a repeated reply is an acknowledgement ("Done.", "OK") and
# collapsing it would be noise-for-noise; the ratchet is driven by substantial
# prose summaries. The reported session's stale summary was ~450 chars.
_MIN_DEDUPE_CHARS = 120

# Appended to the single surviving copy of a repeated reply. Deliberately
# actionable rather than a bare "[x2]": the model that most needs this is the
# one stuck restating a stale conclusion, and the useful thing to tell it is
# what to do INSTEAD. Machine-strippable so a later pass can re-read the count
# and merge it (see _repeat_count) rather than nesting markers.
_REPEAT_MARKER_RE = re.compile(
    r"\n\[compacted: this exact reply was sent (\d+) times?[^\]]*\]\s*$")

_WS_RE = re.compile(r"\s+")


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

    shrunk_recent = [_shrink_if_oversized(m) for m in recent]

    # Deduped across the WHOLE body, recent window included, and AFTER shrinking
    # (so two copies truncated to the same head still compare equal).
    #
    # The recent window is otherwise left alone as "work in progress" — but a
    # verbatim-repeated prose reply is the exact opposite of progress, and the
    # recent window is where a stale claim does the MOST damage, being both the
    # most salient position and the one the model reads back first. Confining
    # the dedupe to `old` capped the ratchet's growth but still handed the model
    # keep_recent/2 fresh copies of its own stale conclusion every pass, which
    # is the reported failure verbatim. Precedent for reaching into the window
    # when a specific pathology demands it: _shrink_if_oversized.
    new_history = system + _dedupe_stale_claims(kept + shrunk_recent)
    after_n, after_chars = len(new_history), estimate_chars(new_history)
    if new_history == history:
        return history, (f"nothing to compact ({before_n} messages, "
                         f"{before_chars:,} chars — within the recent window)")
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


def _repeat_count(content: str) -> int:
    """How many sends this message already stands for — 1 unless a previous
    compaction pass already collapsed copies into it."""
    m = _REPEAT_MARKER_RE.search(content)
    return int(m.group(1)) if m else 1


def _strip_repeat_marker(content: str) -> str:
    return _REPEAT_MARKER_RE.sub("", content)


def _repeat_marker(n: int) -> str:
    return (f"\n[compacted: this exact reply was sent {n} times and never "
            "advanced the task — do not send it again. Re-read the file or "
            "re-run the command and base your next reply on that output.]")


def _dedupe_key(msg: dict) -> str | None:
    """The identity a repeated prose reply is collapsed on, or None when the
    message must be left alone.

    Two exclusions matter. A reply carrying a ```tool``` fence is never
    collapsed — dropping one would break the call/result pairing that the rest
    of the history is threaded on, and a genuinely repeated CALL is the repeat
    guard's job in loop.py, not compaction's. And a short reply is an
    acknowledgement, not a stale conclusion (see _MIN_DEDUPE_CHARS)."""
    if _kind(msg) != "assistant":
        return None
    content = msg.get("content") or ""
    if _FENCE_BLOCK_RE.search(content):
        return None
    body = _strip_repeat_marker(content)
    key = _WS_RE.sub(" ", body).strip().lower()
    return key if len(key) >= _MIN_DEDUPE_CHARS else None


def _dedupe_stale_claims(msgs: list[dict]) -> list[dict]:
    """Keep the FIRST copy of each repeated prose reply, annotated with the
    total send count; drop the rest.

    First rather than last, for two reasons: it keeps the claim at the point in
    the transcript where it was actually introduced (so it reads as old, which
    it is), and it keeps message positions stable across passes, which is what
    makes a second pass a no-op. Counts merge across passes because the marker
    is parsed back out by _repeat_count before comparison."""
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for i, m in enumerate(msgs):
        key = _dedupe_key(m)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + _repeat_count(m.get("content") or "")
        first.setdefault(key, i)

    out: list[dict] = []
    for i, m in enumerate(msgs):
        key = _dedupe_key(m)
        if key is None:
            out.append(m)
            continue
        if first[key] != i:
            continue  # a later copy of a claim already kept above
        n = counts[key]
        if n < 2:
            out.append(m)
            continue
        body = _strip_repeat_marker(m.get("content") or "")
        out.append({**m, "content": body + _repeat_marker(n)})
    return out


def _shrink_if_oversized(msg: dict) -> dict:
    """Applied to every message in the recent window — normally a pure no-op.
    Prompts and live nudges are never touched here regardless of size. A
    tool_result/assistant message is only touched if it individually exceeds
    _RECENT_SHRINK_THRESHOLD, so an ordinary recent turn is untouched but a
    single oversized dump (e.g. one big file read) can't hide behind
    recency and defeat compaction entirely."""
    kind = _kind(msg)
    if kind in ("user_prompt", "nudge"):
        return msg
    content = msg.get("content") or ""
    if len(content) <= _RECENT_SHRINK_THRESHOLD:
        return msg
    if kind == "tool_result":
        return _shrink_tool_result(msg)
    return _shrink_assistant(msg)


def _shrink_tool_result(msg: dict) -> dict:
    content = msg.get("content") or ""
    if any(marker in content for marker in _FILE_CHANGE_MARKERS):
        return msg  # a file-change receipt is already short — keep verbatim
    names = list(dict.fromkeys(_TOOL_NAME_RE.findall(content)))
    if not names:
        return _truncate(msg)
    # NOT "already used earlier in this session" — that phrasing reads as an
    # endorsement of whatever the model concluded from the output, which is
    # exactly the half of the ratchet that let a stale claim stand in for the
    # evidence it was supposed to rest on. Say it's gone and re-checkable.
    # "Re-read or re-run if you need it" used to lead this sentence, and in the
    # regime that triggers compaction at all — a corpus bigger than the budget —
    # that is an invitation into the loop: the re-read costs the same space
    # again and evicts something else. Keep the anti-ratchet half (don't trust
    # the stale conclusion) but make re-reading the deliberate choice, not the
    # suggested default.
    summary = ("Tool results (compacted): " + ", ".join(names) +
              " — output dropped to fit the context budget; you did run these, "
              "only the text is gone. Don't trust an earlier conclusion about "
              "them, and re-run one only if you need its output again.")
    return {**msg, "content": summary}


def _shrink_assistant(msg: dict) -> dict:
    content = msg.get("content") or ""
    # A repeat marker from an earlier pass is signal, not bulk. Hold it aside
    # so truncation can't eat it (which would lose the count AND un-annotate a
    # claim that is still stale), then reattach.
    marker = ""
    found = _REPEAT_MARKER_RE.search(content)
    if found:
        marker, content = found.group(0), content[:found.start()]
    shrunk = _FENCE_BLOCK_RE.sub(_shrink_fenced_block, content)
    if len(shrunk) > _MAX_MESSAGE_CHARS:
        shrunk = (f"{shrunk[:_MAX_MESSAGE_CHARS]}"
                  f"\n…[compacted: {len(shrunk):,} chars total]")
    if shrunk == content and not marker:
        return msg
    return {**msg, "content": shrunk + marker}


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
