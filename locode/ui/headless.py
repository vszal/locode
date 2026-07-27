"""Render a headless (`locode -p`) turn to stdout the way the REPL renders it on
screen — the model's prose interleaved with clean tool / result / nudge lines.

Plain `-p` only streams the model's raw tokens; every tool call, result, and
nudge goes solely to `--log-events` and is invisible on stdout. That makes a
captured headless run unlike what the interactive user watches — and the prose,
which is exactly where a stuck model repeats itself, scrolls past unlabeled. With
`--show-events` this view reproduces the on-screen transcript in one capturable
stream, reusing the REPL's own `render` formatters and `StreamSink` so there is
no second rendering to drift: the ```tool fence is suppressed (the clean `⚙` line
stands in for it) while ordinary prose passes through.

No spinner and no cursor tricks — those are TTY-only and would corrupt a captured
log; this is deliberately a flat, append-only stream.
"""

from __future__ import annotations

from typing import Callable

from locode.ui import render
from locode.ui.render import StreamSink

_MUTATING = {"write_file", "append_file", "edit_file", "replace_lines"}


class HeadlessView:
    """Drives stdout for a headless turn. Wire `on_delta` and `on_event` into the
    AgentLoop; set `loop` afterwards so the live plan checklist can be rendered."""

    def __init__(self, emit: Callable[[str], None], *, color: bool,
                 markdown: bool, cwd: str):
        self._emit = emit
        self._color = color
        self._cwd = cwd
        # Markdown styling only makes sense with color (a TTY); a captured log
        # wants plain text. The sink also filters the ```tool fence out of prose.
        self._sink = StreamSink(emit, markdown=markdown and color)
        self._pending_path: str | None = None
        self._pending_diff: str | None = None
        self.loop = None  # set by the caller; used for the update_plan checklist

    # -- model token stream -----------------------------------------------------
    def on_delta(self, s: str) -> None:
        self._sink.feed(s)

    # -- structured events ------------------------------------------------------
    def on_event(self, ev: dict) -> None:
        phase = ev.get("phase")
        if phase == "assistant_start":
            self._sink.reset()
        elif phase == "assistant_end":
            self._sink.flush()
        elif phase == "run":
            self._on_run(ev)
        elif phase == "result":
            self._on_result(ev)
        elif phase == "denied":
            self._emit(render.format_denied(ev.get("name", "?"),
                                            ev.get("reason", ""),
                                            color=self._color) + "\n")
        elif phase == "nudge":
            self._emit(render.format_nudge(ev.get("reason", ""),
                                           color=self._color) + "\n")
        elif phase == "info":
            self._emit(render.format_nudge(ev.get("text", ""),
                                           color=self._color) + "\n")
        elif phase == "stopped":
            self._emit("\n" + render.format_nudge("⏹ " + str(ev.get("reason", "")),
                                                  color=self._color) + "\n")

    def _on_run(self, ev: dict) -> None:
        name, args = ev.get("name", "?"), ev.get("args", {})
        is_mut = name in _MUTATING
        self._pending_path = args.get("path") if is_mut else None
        # Capture the diff now — the file is still pre-edit at the `run` event
        # (execution happens before `result`), mirroring the REPL's auto-approve
        # path. Showing WHAT changed makes a repeated identical edit obvious.
        self._pending_diff = None
        if is_mut:
            diff = render.format_change(name, args, self._cwd, color=self._color)
            self._pending_diff = diff or None
        # update_plan renders as a checklist off its result, not a generic ⚙ line.
        if name != "update_plan":
            self._emit("\n" + render.format_run(name, args, color=self._color) + "\n")

    def _on_result(self, ev: dict) -> None:
        name = ev.get("name", "?")
        error = bool(ev.get("error", False))
        if name == "update_plan" and not error:
            plan = getattr(self.loop, "plan", None)
            view = render.format_plan(plan, color=self._color) if plan else ""
            if view:
                self._emit("\n" + view + "\n")
        else:
            self._emit(render.format_result(name, ev.get("content", ""), error,
                                            color=self._color) + "\n")
            if self._pending_diff and not error:
                self._emit(self._pending_diff + "\n")
        self._pending_path = None
        self._pending_diff = None
