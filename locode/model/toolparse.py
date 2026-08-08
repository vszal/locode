"""Tolerant extraction of tool calls from a model response.

Local models tool-call unreliably: some emit native `tool_calls`, some wrap a
JSON call in a ```tool fence, some emit bare JSON in prose, and some emit
malformed attempts. This parser converges all of that onto ToolCall objects and,
crucially, *never raises* on bad model output — a malformed attempt becomes a
`malformed` note the agent loop turns into a corrective nudge.

Priority (fallback tiers, not a union, to avoid double-executing the same call):
  1. native `tool_calls`
  2. fenced ```tool / ```tool_call / ```json blocks
  2b. a fence tagged with the TOOL NAME itself (```update_plan) holding bare args
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
# A fence whose language tag is the TOOL NAME — ```update_plan holding bare
# `{"tasks": [...]}`. Matched only against the LIVE tool names (see
# _named_tool_fences), so a ```python or ```diff illustration is never opened.
_NAMED_FENCE_RE = re.compile(r"```([A-Za-z_][\w.-]*)[ \t]*\r?\n")
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


# name -> (required arg keys, accepted arg keys). See Registry.signatures().
_Signatures = dict[str, tuple[frozenset[str], frozenset[str]]]

# Shown when a fenced object carries no name and its keys don't identify one
# tool. Names the missing field rather than just calling the block unparseable —
# a bare "missing a name" got re-emitted byte-identically three times in a row.
_NO_NAME_HELP = (
    'tool object missing a name: put the tool name in a "name" field, as '
    '{"name": "<tool>", "args": {...}}')


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
    tool_signatures: _Signatures | None = None,
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
                call, cerr = _coerce_obj(obj, "fenced", known,
                                         signatures=tool_signatures)
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
    # --- tier 2b: the tool name used AS the fence tag -------------------
    # Runs before the return below so a recovered call beats a sibling block's
    # nudge. Silent on failure: a tag-named fence whose body isn't JSON is prose,
    # not a broken call, and must not be reported malformed (or executed).
    if known:
        for tag, block in _named_tool_fences(content, known):
            before = len(out.calls)
            parsed, ferr = _loads(block)
            objs = _as_objects(parsed) if ferr is None else _iter_json_objects(block)
            for obj in objs:
                call, _ = _coerce_obj(_with_fence_name(obj, tag), "fence-tag",
                                      known, strict=True,
                                      signatures=tool_signatures)
                if call:
                    out.calls.append(call)
            if len(out.calls) == before:
                call = _loose_tool_call(block, known, arg_keys, default_name=tag)
                if call is not None:
                    out.calls.append(call)

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


# Tools whose truncated `content` is worth landing anyway. Scoped on purpose: a
# half-formed edit_file `new`, bash `command`, or web call is unsafe to run
# partially, but a partial DOCUMENT is strictly better on disk than lost — the
# model appends the remainder next turn. See salvage_truncated_write.
_FILE_WRITE_TOOLS = frozenset({"write_file", "append_file"})
# Below this many recovered characters a partial write isn't worth a disk write:
# it's likely a fence the model opened but never filled, not a real document.
_SALVAGE_MIN_CONTENT = 200


def salvage_truncated_write(
    content: str,
    known_names: Iterable[str] | None = None,
    known_arg_keys: Iterable[str] | None = None,
) -> ToolCall | None:
    """Recover a write_file/append_file whose `content` was cut off at the token
    limit, so the partial document can be LANDED instead of lost.

    This is the deliberate complement to _fence_blocks, which *skips* an unclosed
    fence — the right call for extract(), because an incomplete tool call must not
    be run as if complete. But a large document written in one shot truncates
    exactly this way: the JSON string never closes, extract() returns nothing, and
    the whole partial reply evaporates (the qythos9 design-doc "long mode writes
    40k and nothing lands" failure). Here we target precisely that skipped body —
    the LAST opened-but-unclosed ```tool/```json fence — and loose-parse it,
    reusing _loose_string's run-off-the-end handling.

    Returns the call ONLY when it is a write_file/append_file carrying a real
    `path` and a substantive partial `content`; None otherwise (every fence
    closed, the truncated call was some other tool, or too little was recovered),
    in which case the caller falls back to the truncation nudge. The returned
    call is tagged source='salvage-truncated' so the loop knows to steer the
    model to APPEND the rest rather than treat the file as finished."""
    arg_keys = set(known_arg_keys) if known_arg_keys else set(_DEFAULT_ARG_KEYS)
    known = set(known_names) if known_names is not None else None
    body = _last_unclosed_fence_body(content)
    if body is None:
        return None
    call = _loose_tool_call(body, known, arg_keys)
    if call is None or call.name not in _FILE_WRITE_TOOLS:
        return None
    text = call.args.get("content")
    if not isinstance(text, str) or len(text) < _SALVAGE_MIN_CONTENT:
        return None
    if not call.args.get("path"):
        return None
    return ToolCall(name=call.name, args=call.args, id="",
                    source="salvage-truncated")


def _last_unclosed_fence_body(content: str) -> str | None:
    """The body after the first ```tool/```json fence that is never closed — the
    call the token limit cut off. Returns None if every fence is closed. (A
    truncated stream can only have one unclosed fence, and it is the last thing
    in the content, so the first unclosed one we reach is it.)"""
    pos = 0
    while True:
        m = _FENCE_OPEN_RE.search(content, pos)
        if not m:
            return None
        close = _closing_fence(content, m.end())
        if close is None:
            return content[m.end():]
        pos = close + 3


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


def infer_tool_name(arg_keys: Iterable[str], signatures: _Signatures) -> str | None:
    """Name the tool a nameless call must have meant, from its argument keys.

    A tool is a candidate when the object's keys are all keys it accepts AND it
    carries every argument that tool requires. The answer counts only when
    exactly ONE tool qualifies — `{"tasks": [...]}` is unmistakably
    `update_plan`, while `{"path": "x"}` could be read_file or ls and stays
    unresolved. Conservative on purpose: a wrong guess RUNS the wrong tool,
    which is far worse than the nudge it replaces.
    """
    keys = frozenset(arg_keys)
    if not keys:
        return None
    hits = [n for n, (req, props) in signatures.items()
            if keys <= props and req <= keys]
    return hits[0] if len(hits) == 1 else None


def _coerce_obj(obj: Any, source: str, known: set[str] | None,
                strict: bool = False, signatures: _Signatures | None = None):
    if not isinstance(obj, dict):
        return None, None
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
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

    if not name:
        # No name — but the argument keys may still identify the tool beyond
        # doubt. Only from an explicit tool fence (never in strict salvage mode,
        # where a bare object in prose is more likely to be data than a call).
        if strict or signatures is None or err:
            return None, None if strict else _NO_NAME_HELP
        name = infer_tool_name(args, signatures)
        if not name:
            return None, _NO_NAME_HELP
        source = f"{source}+inferred"
    if known is not None and name not in known:
        # In strict (salvage) mode an unknown name is just not-a-call, silently.
        return None, None if strict else f"unknown tool {name!r}"
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


def _named_tool_fences(content: str, known: set[str]) -> Iterable[tuple[str, str]]:
    """Yield (tool_name, body) for each fence tagged with a live tool's name.

    Models routinely put the tool name where Markdown expects a language tag and
    the bare arguments inside — ```update_plan holding `{"tasks": [...]}`. Every
    other tier misses it: the tag is not an envelope tag, and the body names no
    tool, so a perfectly well-formed call is read as the turn's final answer and
    the run ends. Measured at 46 of the archive's turn-ending messages and 20 of
    20 across the two b98 sweeps, which is what made them unmeasurable (5.23).

    Only tags that ARE known tool names are opened. That keeps _closing_fence —
    which tracks JSON string state — off ```python bodies it cannot track, and
    keeps the scan from ever looking at an illustrative code block.
    """
    pos = 0
    while True:
        m = _NAMED_FENCE_RE.search(content, pos)
        if not m:
            return
        if m.group(1).lower() not in known:
            pos = m.end()          # not a tool fence: step over the tag only
            continue
        close = _closing_fence(content, m.end())
        if close is None:
            return                 # unclosed (truncated) — left for the nudge
        yield m.group(1).lower(), content[m.end():close]
        pos = close + 3


def _with_fence_name(obj: Any, name: str) -> Any:
    """Supply the fence tag's tool name to a call object that has none.

    The tag only ever fills a gap. When the body DOES name a tool the body wins:
    the 13 archived ```bash fences each carry a correct {"name": "edit_file", …}
    inside, where the tag is just a wrong guess at the language.
    """
    if not isinstance(obj, dict):
        return obj
    if any(isinstance(obj.get(k), str) for k in _NAME_KEYS):
        return obj
    inner = next((obj[k] for k in _ARG_KEYS if k in obj), obj)
    return {"name": name, "args": inner}


def _closing_fence(content: str, i: int) -> int | None:
    """Index of the ``` that closes a fenced body starting at i, or None if the
    body is never closed. A ``` is only a closer when it lies OUTSIDE the string
    context of the body, so interior code fences (inside a quoted value) are
    ignored.

    String context tracks BOTH `"` and `'` delimiters: a value opened with one
    quote keeps the other literal until its own matching close. Weak models
    (qythos9) emit Python-style single-quoted values whenever the value contains
    a `"`, so a value like `'x = \"\"\"doc\"\"\"'` carries an ODD number of
    interior double-quotes. Tracking only `"` desynced on those and mistook the
    real closing ``` for string interior — dropping the whole call. See the
    single-quote loose-recovery path (`_loose_string`) for the sibling fix.

    Resilience to an UNTERMINATED string: a weak model sometimes drops the
    closing quote of the last value (e.g. `"new": 'code…"}}` with no closing
    `'`). Tracking string state then runs to EOF still "inside" that string and
    swallows the real closing ``` — the whole call vanishes silently (neither a
    call nor a malformed retry, so the loop reads it as a finished answer). We
    guard that by remembering the first ``` seen inside the currently-open
    string; if we reach EOF still in a string, that string never closed and the
    remembered ``` was really the fence. A properly closed string clears the
    memo on close and never reaches EOF still-quoted; a genuinely truncated
    stream (no interior ```) leaves it None and we still report None."""
    n = len(content)
    quote: str | None = None   # active string delimiter, or None outside a string
    esc = False
    fence_in_string: int | None = None  # first ``` inside the current open string
    while i < n:
        c = content[i]
        if quote is not None:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
                fence_in_string = None   # closed cleanly — forget its interior ```
            elif (fence_in_string is None and c == "`"
                  and content.startswith("```", i)):
                fence_in_string = i      # remember, but keep scanning the string
            i += 1
        elif c in ('"', "'"):
            quote = c
            i += 1
        elif c == "`" and content.startswith("```", i):
            return i
        else:
            i += 1
    if quote is not None and fence_in_string is not None:
        return fence_in_string           # unterminated string swallowed the fence
    return None


_NAME_RE = re.compile(r'"(?:name|tool|function)"\s*:\s*"([A-Za-z0-9_.\-]+)"')
_KEY_RE = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*')
_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
             "/": "/", "b": "\b", "f": "\f"}


def _loose_tool_call(block: str, known: set[str] | None,
                     arg_keys: set[str],
                     default_name: str | None = None) -> ToolCall | None:
    """Recover a tool call from a block whose strict JSON failed because the model
    left quotes/newlines unescaped inside code-bearing string values. Anchors on
    the known argument keys to find where each value ends; never raises, returns
    None when it can't confidently identify a known tool.

    `default_name` is the fence tag's tool name (tier 2b) — used only when the
    block itself names no tool, so a body-supplied name always wins."""
    nm = _NAME_RE.search(block)
    name = nm.group(1) if nm else default_name
    if name is None:
        return None
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
    if text[i] in ("'", '"'):
        # A single-quote delimiter is a weak model reaching for Python/JS string
        # syntax (qythos9 switches to it whenever the value itself contains "),
        # so the body follows string-escape rules — `\n` MEANS a newline. Left as
        # a bare token it fed a literal `'…\n…'` into write_file/edit_file and
        # corrupted every multi-line arg. Parse it like a double-quoted string,
        # using its own quote as the terminator.
        return _loose_string(text, i + 1, arg_keys, quote=text[i])
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


def _loose_string(text: str, i: int, arg_keys: set[str], quote: str = '"'):
    """Read a string body from i (just past the opening `quote`) to its real
    close, treating `quote` as the terminator only when what follows is a
    structural boundary (a comma+known-key, or a closing brace) — so interior
    unescaped quotes in code are kept literal. `quote` is '"' for JSON strings
    and "'" for the Python-style single-quoted values weak models emit; either
    way `\\`-escapes are decoded. Returns (string, end_index)."""
    n = len(text)
    buf: list[str] = []
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            buf.append(_UNESCAPE.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if c == quote:
            if _is_value_end(text, i + 1, arg_keys):
                return "".join(buf), i + 1
            buf.append(quote)    # interior unescaped quote — part of the value
            i += 1
            continue
        buf.append(c)
        i += 1
    # Ran off the end without the closing quote. Two cases share this exit: a
    # stream truncated mid-value (salvage keeps the partial as-is) and a value
    # whose closing quote the model simply dropped, leaving the JSON's own
    # closers glued on (`f"…{h}"}}`). Trim only a trailing run of closers that
    # unbalances the value's OWN brackets — that is leaked structure, never the
    # string's content. A truncated partial has no such trailing unmatched
    # closers, so it is returned untouched.
    return _strip_structural_tail("".join(buf)), n


def _strip_structural_tail(s: str) -> str:
    """Remove trailing closing brackets that leaked from the enclosing JSON into
    an unterminated string value. `d` = the value's net bracket balance; if it is
    negative the last `-d` closers (and any whitespace among/after them) are the
    args/object closers the missing quote let through — strip exactly those,
    leaving balanced interior brackets (f-string `{h}`, dict/list literals)
    intact. `f"…{h}"}}` -> `f"…{h}"`; `return {"a": 1}}}` -> `return {"a": 1}`."""
    depth = s.count("{") + s.count("[") - s.count("}") - s.count("]")
    if depth >= 0:
        return s
    remove = -depth
    i = len(s)
    while i > 0 and remove > 0:
        c = s[i - 1]
        if c in "}]":
            remove -= 1
            i -= 1
        elif c in " \t\r\n":
            i -= 1
        else:
            break
    return s[:i].rstrip()


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
