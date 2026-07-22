"""Structured event logging, for eval harnesses and post-mortem debugging.

The agent loop already narrates itself through `on_event` callbacks so the UI
can render progress. This tees those same events to a JSONL file, so a harness
can *measure* a run — iterations used, nudges by reason, repeat/stall
detections, tool-call outcomes, why the turn ended — instead of scraping
human-readable stdout. One JSON object per line; the schema is "whatever the
loop emitted" plus a sequence number and a seconds-since-turn-start stamp.

Nothing here may raise into the agent loop: telemetry failing must never kill
a turn, so every I/O path degrades to a silent no-op.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# Tool args and results can be enormous (a whole file read, a 10k-line test
# log). The log exists to measure behaviour, not to archive payloads.
MAX_FIELD_CHARS = 2000


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"…<clipped {len(value) - MAX_FIELD_CHARS} chars>"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


class EventLog:
    """Append-only JSONL sink for agent events."""

    def __init__(self, path: str):
        self.path = path
        self._seq = 0
        self._t0 = time.monotonic()
        self._fh = None
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._fh = None

    @property
    def enabled(self) -> bool:
        return self._fh is not None

    def mark_turn_start(self) -> None:
        """Rebase the `t` stamp so each turn's timings start at zero."""
        self._t0 = time.monotonic()

    def emit(self, event: dict) -> None:
        if self._fh is None:
            return
        self._seq += 1
        rec: dict[str, Any] = {"seq": self._seq,
                               "t": round(time.monotonic() - self._t0, 3)}
        for k, v in event.items():
            rec[k] = _clip(v)
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def tee(event_log: "EventLog | None", on_event):
    """Compose a UI `on_event` callback with an EventLog, returning a single
    callback that feeds both. Either side may be None."""
    if event_log is None:
        return on_event or (lambda e: None)
    if on_event is None:
        return event_log.emit

    def _both(event: dict):
        event_log.emit(event)
        return on_event(event)

    return _both
