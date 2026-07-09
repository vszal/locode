"""Tolerant extraction of tool calls from a model response.

Local models tool-call unreliably: some emit native `tool_calls`, some wrap a
JSON call in a ```tool fence, some emit bare JSON in prose, and some emit
malformed attempts. This parser converges all of that onto ToolCall objects and,
crucially, *never raises* on bad model output — a malformed attempt becomes a
`malformed` note the agent loop turns into a corrective nudge.

Priority (fallback tiers, not a union, to avoid double-executing the same call):
  1. native `tool_calls`
  2. fenced ```tool / ```tool_call / ```json blocks
  3. best-effort salvage of a bare top-level JSON object naming a known tool
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from locode.tools.base import ToolCall

# A *required* language tag is the key signal. A plain ``` code fence — which a
# code model loves to emit to ILLUSTRATE a change — must NOT be parsed as a tool
# call (that produced spurious "unparseable tool block" errors). Real tool calls
# in a bare/other fence are still recovered by the tier-3 salvage scan.
#
# Only the OPENING fence is matched by regex; the closing ``` is located by a
# JSON-string-aware scan (see _fence_blocks). A naive non-greedy `(.*?)```` would
# stop at the FIRST ``` it sees — but a write_file/edit_file whose `content` is a
# Markdown doc carries its own ```lang code fences inside the JSON string, so the
# naive match truncated the call at the first interior fence (the "DESIGN.md
# stops at 22 lines" bug). Scanning with string awareness keeps interior fences
# literal and ends the block only at a ``` that sits OUTSIDE the JSON payload.
_FENCE_OPEN_RE = re.compile(
    r"```(?:tool_call|tool|json)[ \t]*\r?\n",
    re.IGNORECASE,
)
_NAME_KEYS = ("name", "tool", "function")
_ARG_KEYS = ("args", "arguments", "parameters", "input")
# Structural keys that name the call/args envelope — never treated as arguments.
_STRUCTURAL_KEYS = frozenset(_NAME_KEYS + _ARG_KEYS)
# Fallback argument vocabulary when the caller doesn't pass the live tool schemas
# (keeps the relaxed recovery working in isolation / tests). Anchoring on these
# lets us find where one mis-escaped string value ends and the next key begins.
_DEFAULT_ARG_KEYS = frozenset({
    "path", "old", "new", "content", "replace_all", "pattern", "glob",
    "offset", "limit", "cmd", "command", "query", "root", "regex", "url",
    "recursive", "line", "lines", "name",
})


@dataclass
class ParseOutcome:
    calls: list[ToolCall] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)

    @property
    def found_anything(self) -> bool:
        return bool(self.calls or self.malformed)


def extract(
    message: dict[str, Any],
    known_names: Iterable[str] | None = None,
    known_arg_keys: Iterable[str] | None = None,
) -> ParseOutcome:
    known = set(known_names) if known_names is not None else None
    arg_keys = set(known_arg_keys) if known_arg_keys else set(_DEFAULT_ARG_KEYS)
    out = ParseOutcome()

    # --- tier 1: native tool_calls --------------------------------------
    native = message.get("tool_calls") or []
    if native:
        for tc in native:
            call, err = _coerce_native(tc, known)
            if call:
                out.calls.append(call)
            elif err:
                out.malformed.append(err)
        # Native channel is authoritative when it yielded anything usable.
        if out.calls:
            return out

    content = message.get("content") or ""

    # --- tier 2: explicit tool fences (```tool / ```tool_call / ```json) -
    fenced_seen = False
    for block in _fence_blocks(content):
        fenced_seen = True
        parsed, err = _loads(block)
        if err is None:
            for obj in _as_objects(parsed):
                call, cerr = _coerce_obj(obj, "fenced", known)
                if call:
                    out.calls.append(call)
                elif cerr:
                    out.malformed.append(cerr)
            continue
        # Strict JSON failed: salvage JSON object(s) embedded in the block (a tool
        # name prefix, a // comment, or trailing prose around the call) before
        # giving up and nudging.
        salvaged = False
        for obj in _iter_json_objects(block):
            call, _ = _coerce_obj(obj, "fenced", known, strict=True)
            if call:
                out.calls.append(call)
                salvaged = True
        if salvaged:
            continue
        # Last resort: relaxed, key-anchored recovery for the dominant weak-model
        # failure — code in `old`/`new`/`content` with UNESCAPED quotes/newlines
        # that breaks strict JSON. Recovers the call by reading each value up to
        # the next known argument key, keeping interior quotes literal.
        call = _loose_tool_call(block, known, arg_keys)
        if call is not None:
            out.calls.append(call)
        else:
            out.malformed.append(f"unparseable tool block: {err}")
    if out.calls or (fenced_seen and out.malformed):
        return out

    # --- tier 3: salvage bare JSON naming a known tool ------------------
    # Only when we can match against real tool names (avoids false positives).
    if known:
        for obj in _iter_json_objects(content):
            call, _ = _coerce_obj(obj, "salvage", known, strict=True)
            if call:
                out.calls.append(call)

    return out


# --- helpers -------------------------------------------------------------

def _coerce_native(tc: dict[str, Any], known: set[str] | None):
    fn = tc.get("function") or {}
    name = fn.get("name") or tc.get("name")
    if not name:
        return None, "native tool_call missing a function name"
    if known is not None and name not in known:
        return None, f"unknown tool {name!r} in native tool_call"
    raw_args = fn.get("arguments", tc.get("arguments", {}))
    args, err = _loads_args(raw_args)
    if err:
        return None, f"bad arguments for {name!r}: {err}"
    return ToolCall(name=name, args=args, id=tc.get("id", ""), source="native"), None


def _coerce_obj(obj: Any, source: str, known: set[str] | None, strict: bool = False):
    if not isinstance(obj, dict):
        return None, None
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if not name:
        return None, None if strict else "tool object missing a name"
    if known is not None and name not in known:
        # In strict (salvage) mode an unknown name is just not-a-call, silently.
        return None, None if strict else f"unknown tool {name!r}"
    args_key = next((k for k in _ARG_KEYS if k in obj), None)
    if args_key is not None:
        raw_args = obj[args_key]
    else:
        # Flat schema: some models inline the arguments at the top level next to
        # "name" (e.g. {"name":"read_file","path":"..."}) instead of nesting them
        # under "args". Lift the non-envelope keys into the argument dict.
        raw_args = {k: v for k, v in obj.items()
                    if k not in _STRUCTURAL_KEYS and k != "id"}
    args, err = _loads_args(raw_args)
    if err:
        return None, f"bad arguments for {name!r}: {err}"
    return ToolCall(name=name, args=args, id=str(obj.get("id", "")), source=source), None


def _loads_args(raw: Any):
    """Coerce a tool-arguments value (dict, or JSON string) to a dict."""
    if isinstance(raw, dict):
        return raw, None
    if raw in (None, ""):
        return {}, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, str(e)
        if isinstance(parsed, dict):
            return parsed, None
        return None, "arguments did not decode to an object"
    return None, f"arguments has unexpected type {type(raw).__name__}"


def _loads(text: str):
    try:
        return json.loads(text.strip()), None
    except json.JSONDecodeError as e:
        return None, str(e)


def _as_objects(parsed: Any) -> list[Any]:
    """A fenced block may hold one object or a JSON array of calls."""
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _fence_blocks(content: str) -> Iterable[str]:
    """Yield the body of each ```tool / ```tool_call / ```json fence.

    The closing ``` is found by _closing_fence, which tracks JSON-string state,
    so a ``` code fence *inside* a write_file/edit_file string value (a Markdown
    document being written to disk) is kept literal instead of ending the block.
    That is what lets a whole file — with its own ```lang blocks — round-trip as
    one tool call rather than truncating at the first interior fence.

    An OPENED-but-unclosed fence (a call cut off by the token limit) is skipped,
    not yielded, so it flows through to the loop's truncation nudge unchanged.
    """
    pos = 0
    while True:
        m = _FENCE_OPEN_RE.search(content, pos)
        if not m:
            return
        close = _closing_fence(content, m.end())
        if close is None:
            return  # unclosed fence (truncated) — leave for the loop to nudge
        yield content[m.end():close]
        pos = close + 3


def _closing_fence(content: str, i: int) -> int | None:
    """Index of the ``` that closes a fenced body starting at i, or None if the
    body is never closed. A ``` is only a closer when it lies OUTSIDE the JSON
    string context of the body, so interior code fences (inside a quoted value)
    are ignored."""
    n = len(content)
    in_str = esc = False
    while i < n:
        c = content[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            i += 1
        elif c == "`" and content.startswith("```", i):
            return i
        else:
            i += 1
    return None


_NAME_RE = re.compile(r'"(?:name|tool|function)"\s*:\s*"([A-Za-z0-9_.\-]+)"')
_KEY_RE = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*')
_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
             "/": "/", "b": "\b", "f": "\f"}


def _loose_tool_call(block: str, known: set[str] | None,
                     arg_keys: set[str]) -> ToolCall | None:
    """Recover a tool call from a block whose strict JSON failed because the model
    left quotes/newlines unescaped inside code-bearing string values. Anchors on
    the known argument keys to find where each value ends; never raises, returns
    None when it can't confidently identify a known tool."""
    nm = _NAME_RE.search(block)
    if not nm:
        return None
    name = nm.group(1)
    if known is not None and name not in known:
        return None
    args: dict[str, Any] = {}
    pos = 0
    while True:
        km = _KEY_RE.search(block, pos)
        if not km:
            break
        key = km.group(1)
        if key in _STRUCTURAL_KEYS or key not in arg_keys:
            pos = km.end()
            continue
        value, end = _loose_value(block, km.end(), arg_keys)
        if value is not _MISSING:
            args[key] = value
        pos = max(end, km.end())
    # Only fire when we actually recovered an argument. A valid no-arg call is
    # valid JSON and is caught by the strict tier, so it never reaches here;
    # reaching here with empty args means the block is genuinely garbage (e.g.
    # `{"name": "ls", oops}`) and must be reported malformed, not conjured.
    if not args:
        return None
    return ToolCall(name=name, args=args, id="", source="salvage")


_MISSING = object()


def _loose_value(text: str, i: int, arg_keys: set[str]):
    """Read one JSON-ish value starting at i; return (value, end_index)."""
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return _MISSING, i
    if text[i] == '"':
        return _loose_string(text, i + 1, arg_keys)
    # bool / null / number: read to the next structural delimiter.
    j = i
    while j < n and text[j] not in ",}\n":
        j += 1
    token = text[i:j].strip().rstrip("}],")
    low = token.lower()
    if low in ("true", "false"):
        return low == "true", j
    if low == "null":
        return None, j
    try:
        return (float(token) if "." in token else int(token)), j
    except ValueError:
        return (token, j) if token else (_MISSING, j)


def _loose_string(text: str, i: int, arg_keys: set[str]):
    """Read a string body from i (just past the opening quote) to its real close,
    treating a quote as the terminator only when what follows is a structural
    boundary (a comma+known-key, or a closing brace) — so interior unescaped
    quotes in code are kept literal. Returns (string, end_index)."""
    n = len(text)
    buf: list[str] = []
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            buf.append(_UNESCAPE.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if c == '"':
            if _is_value_end(text, i + 1, arg_keys):
                return "".join(buf), i + 1
            buf.append('"')      # interior unescaped quote — part of the value
            i += 1
            continue
        buf.append(c)
        i += 1
    return "".join(buf), n       # ran off the end (truncated) — return what we got


def _is_value_end(text: str, i: int, arg_keys: set[str]) -> bool:
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return True
    c = text[i]
    if c in "}]":
        return True
    if c == ",":
        rest = text[i + 1:]
        km = _KEY_RE.match(rest.lstrip())
        if km:
            nxt = km.group(1)
            return nxt in arg_keys or nxt in _STRUCTURAL_KEYS
        return bool(re.match(r"\s*[}\]]", rest))
    return False


def _iter_json_objects(text: str) -> Iterable[dict[str, Any]]:
    """Yield top-level JSON objects embedded in free text (brace-balanced)."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        chunk = text[i:j + 1]
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                yield obj
        except json.JSONDecodeError:
            pass
        i = j + 1
