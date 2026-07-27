#!/usr/bin/env python3
"""Replay a recorded locode session as the user saw it on screen — plus flags.

The problem this solves: when I (the agent building locode) test with headless
`locode -p`, tool calls / results / nudges are only written to the `--log-events`
JSONL, never rendered to stdout — so I was scraping the log into "compile=PASS
finish=STOPPED" and losing the turn-by-turn detail where repeats and failed edits
actually live. The interactive user, meanwhile, watches the full transcript
scroll by. This tool closes that gap: it feeds a recorded event log back through
the *same* `locode.ui.render` formatters the REPL uses, so I read what the user
read — then overlays the pathology flags (🔁 repeat call, ✗ failed edit, ∅ no-op,
🛡 syntax-guard save) and a loud VERDICT header so a flaily turn is impossible to
miss.

What is NOT reconstructable: the model's own prose is streamed to the screen via
`on_delta` and is never logged, so it doesn't appear here. Everything structural
— every tool call with its args, every result, every nudge, the stop reason — is.

    python evals/replay.py <events.jsonl | dir-of-jsonl> [--quiet] [--no-color]

    --quiet   verdict header only, skip the transcript
    --no-color  plain text (default auto-detects a TTY)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from locode.ui import render  # noqa: E402

# A green pytest/unittest tally with no failures — mirrors the loop's own
# _saw_green_test gate closely enough to report "did a test actually pass?".
_GREEN_RE = re.compile(r"\b([1-9]\d*)\s+passed\b", re.IGNORECASE)
_ANYFAIL_RE = re.compile(r"\b[1-9]\d*\s+(failed|errors?)\b", re.IGNORECASE)


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def classify_result(content: str, is_error: bool) -> str:
    """Bucket a tool result. Categories used for the verdict counts and the
    transcript flags. Signatures track the messages in locode/tools/fs.py."""
    c = (content or "").lower()
    if not is_error:
        if _GREEN_RE.search(content or "") and not _ANYFAIL_RE.search(content or ""):
            return "ok-green"
        return "ok"
    # build 47 syntax-reject guard — a *save*, not a flail: corruption refused.
    if "would introduce a syntaxerror" in c or "not applied" in c:
        return "syntax-guard"
    if "changed nothing" in c or "byte-for-byte identical" in c or "no change" in c:
        return "noop"
    if "must differ" in c or ("`new`" in (content or "") and "identical" in c):
        return "identical"
    if "identical" in c:
        return "identical"
    if "ambiguous" in c or ("matches" in c and "line" in c):
        return "ambiguous"
    if "not find" in c or "not found" in c or "no match" in c:
        return "not_found"
    if "empty" in c:
        return "empty_old"
    return "error"


# Categories that mean the model wasted the call (as opposed to a real failure it
# can learn from, or a guard save). Used only for the "no-op" verdict count.
_NOOP_CATS = {"noop", "identical"}


def call_key(ev: dict) -> tuple:
    """Canonical identity of a tool call, for repeat detection: name + args with
    keys sorted, so a byte-identical re-issue collides regardless of dict order."""
    args = ev.get("args") or {}
    return (ev.get("name", "?"), json.dumps(args, sort_keys=True, default=str))


def summarize(events: list[dict]) -> dict:
    by_tool: Counter = Counter()
    result_cats: Counter = Counter()
    nudges: Counter = Counter()
    seen_keys: set = set()
    repeats = 0
    fails = 0
    noops = 0
    saves = 0
    saw_green = False
    iterations = 0
    stop_reason = None
    model = None
    wall = 0.0

    for ev in events:
        ph = ev.get("phase")
        if "t" in ev:
            try:
                wall = max(wall, float(ev["t"]))
            except (TypeError, ValueError):
                pass
        if ph == "turn_start":
            model = ev.get("model") or model
        elif ph == "iteration":
            iterations += 1
        elif ph == "run":
            by_tool[ev.get("name", "?")] += 1
            k = call_key(ev)
            if k in seen_keys:
                repeats += 1
            seen_keys.add(k)
        elif ph == "result":
            cat = classify_result(ev.get("content", ""), bool(ev.get("error")))
            result_cats[cat] += 1
            if cat == "ok-green":
                saw_green = True
            elif cat == "syntax-guard":
                saves += 1
            elif ev.get("error"):
                fails += 1
                if cat in _NOOP_CATS:
                    noops += 1
        elif ph == "nudge":
            nudges[ev.get("reason", "?")] += 1
        elif ph == "denied":
            result_cats["denied"] += 1
        elif ph == "stopped":
            stop_reason = ev.get("reason", "?")

    return {
        "model": model or "?",
        "iterations": iterations,
        "tool_calls": sum(by_tool.values()),
        "by_tool": by_tool,
        "result_cats": result_cats,
        "fails": fails,
        "noops": noops,
        "repeats": repeats,
        "syntax_saves": saves,
        "nudges": nudges,
        "saw_green": saw_green,
        "stop_reason": stop_reason,
        "wall": wall,
    }


def _c(s: str, code: str, color: bool) -> str:
    return f"{code}{s}\033[0m" if color else s


def verdict_lines(name: str, s: dict, *, color: bool = False) -> list[str]:
    """The loud header: the counts that make a flaily turn impossible to miss."""
    red, yellow, green, dim = "\033[31m", "\033[33m", "\033[32m", "\033[2m"
    head = _c(f"VERDICT {name} · {s['model']}", "\033[1m", color)
    l2 = (f"  {s['iterations']} iters · {s['wall']:.0f}s · "
          f"{s['tool_calls']} tool calls")
    flags = []
    if s["fails"]:
        flags.append(_c(f"✗ {s['fails']} failed", red, color))
    if s["noops"]:
        flags.append(_c(f"∅ {s['noops']} no-op", yellow, color))
    if s["repeats"]:
        flags.append(_c(f"🔁 {s['repeats']} repeat", yellow, color))
    if s["syntax_saves"]:
        flags.append(_c(f"🛡 {s['syntax_saves']} syntax-guard save", green, color))
    if s["saw_green"]:
        flags.append(_c("✓ saw green test", green, color))
    l3 = "  " + ("  ".join(flags) if flags else _c("clean — no repeats/fails/no-ops", green, color))
    lines = [head, l2, l3]
    if s["nudges"]:
        nd = " ".join(f"{r}×{n}" for r, n in s["nudges"].most_common())
        lines.append("  " + _c(f"nudges: {nd}", yellow, color))
    by = " ".join(f"{t}×{n}" for t, n in s["by_tool"].most_common())
    if by:
        lines.append("  " + _c(f"tools: {by}", dim, color))
    stop = s["stop_reason"]
    if stop:
        lines.append("  " + _c(f"STOP: {render._truncate(stop, 80)}", red, color))
    else:
        lines.append("  " + _c("ended: answered (self-terminated)", green, color))
    return lines


def _edit_preview(name: str, args: dict) -> str:
    """A short peek at what an edit is trying to do, so a repeated edit is
    visible as the same old→new (format_run alone only shows the path)."""
    if name in ("edit_file",):
        old = render._truncate(str(args.get("old", "")), 32)
        new = render._truncate(str(args.get("new", "")), 32)
        return f"      {old!r} ⟶ {new!r}"
    if name == "replace_lines":
        return f"      lines {args.get('start')}-{args.get('end')}"
    return ""


def transcript_lines(events: list[dict], *, color: bool = False) -> list[str]:
    """Reconstruct the on-screen transcript from the log, using the REPL's own
    render formatters, with pathology flags overlaid."""
    lines: list[str] = []
    seen_keys: set = set()
    for ev in events:
        ph = ev.get("phase")
        t = ev.get("t", 0)
        if ph == "run":
            name, args = ev.get("name", "?"), ev.get("args", {})
            k = call_key(ev)
            repeat = k in seen_keys
            seen_keys.add(k)
            row = f"{t:7.1f}s " + render.format_run(name, args, color=color)
            if repeat:
                row += "  " + _c("🔁 REPEAT (identical call already made)", "\033[33m", color)
            lines.append(row)
            prev = _edit_preview(name, args)
            if prev:
                lines.append(_c(prev, "\033[2m", color))
        elif ph == "result":
            name = ev.get("name", "?")
            content, err = ev.get("content", ""), bool(ev.get("error"))
            cat = classify_result(content, err)
            row = "        " + render.format_result(name, content, err, color=color)
            tag = {"noop": "∅ NO-OP", "identical": "∅ NO-OP (old==new)",
                   "syntax-guard": "🛡 SYNTAX-GUARD SAVE (corruption refused)",
                   "not_found": "✗ match not found", "ambiguous": "✗ ambiguous match",
                   "ok-green": "✓ GREEN TEST"}.get(cat)
            if tag:
                row += "  " + _c(f"[{tag}]", "\033[36m", color)
            lines.append(row)
        elif ph == "nudge":
            lines.append(f"{t:7.1f}s " + render.format_nudge(ev.get("reason", ""), color=color))
        elif ph == "denied":
            lines.append(f"{t:7.1f}s " + render.format_denied(
                ev.get("name", "?"), ev.get("reason", ""), color=color))
        elif ph == "stopped":
            lines.append(f"{t:7.1f}s " + _c("⏹ " + str(ev.get("reason", "")), "\033[31m", color))
    return lines


def render_report(path: Path, *, quiet: bool, color: bool) -> str:
    events = load(path)
    s = summarize(events)
    out = list(verdict_lines(path.stem, s, color=color))
    if not quiet:
        out.append(_c("── transcript " + "─" * 30, "\033[2m", color))
        out.extend(transcript_lines(events, color=color))
    return "\n".join(out)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    quiet = "--quiet" in argv or "-q" in argv
    color = render.should_color() and "--no-color" not in argv
    if not args:
        print(__doc__)
        return 2
    targets: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            targets.extend(sorted(p.glob("*.jsonl")))
        elif p.exists():
            targets.append(p)
        else:
            print(f"no such file: {a}", file=sys.stderr)
    for i, p in enumerate(targets):
        if i:
            print()
        print(render_report(p, quiet=quiet, color=color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
