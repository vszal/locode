"""Esc / Ctrl-C interruption, scoped to model streaming only.

The key-listener owns the terminal (raw mode) ONLY while the model is
generating. It must NOT be active during interactive confirm/select prompts — those
open their own prompt_toolkit dialog and the two cannot share stdin/raw-mode at
once (doing so wedges the terminal). So the agent loop wraps just the streaming
call in `interrupt_scope`; tool-approval prompts run outside it with a clean
terminal.

Because raw mode disables the terminal's newline translation, streamed output
goes through `RawWriter` so "\n" renders as "\r\n" while the scope is active.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from locode.agent.cancel import CancelToken


class RawWriter:
    """stdout writer that translates newlines while raw mode is active."""
    def __init__(self) -> None:
        self.raw = False

    def write(self, text: str) -> None:
        if self.raw:
            text = text.replace("\n", "\r\n")
        sys.stdout.write(text)
        sys.stdout.flush()


def make_key_handler(inp, cancel: CancelToken):
    """Build the readable-callback that cancels on Esc / Ctrl-C.

    A lone Esc is held in prompt_toolkit's parser (it may begin an escape
    sequence) and is NOT returned by read_keys() until flushed — so we drain
    read_keys() AND flush_keys() every time, or Esc would never register.
    """
    from prompt_toolkit.keys import Keys

    def on_keys() -> None:
        keys = list(inp.read_keys()) + list(inp.flush_keys())
        for kp in keys:
            if kp.key in (Keys.Escape, Keys.ControlC):
                cancel.cancel()

    return on_keys


@asynccontextmanager
async def interrupt_scope(cancel: CancelToken, writer: RawWriter | None = None):
    """While active: Esc/Ctrl-C set `cancel`, and `writer` is put in raw mode.
    No-op when stdin isn't a TTY (piped) or prompt_toolkit is unavailable."""
    if not sys.stdin.isatty():
        yield
        return
    try:
        from prompt_toolkit.input import create_input
    except Exception:
        yield
        return

    inp = create_input()
    on_keys = make_key_handler(inp, cancel)
    if writer is not None:
        writer.raw = True
    try:
        with inp.raw_mode():
            with inp.attach(on_keys):
                yield
    finally:
        if writer is not None:
            writer.raw = False


@asynccontextmanager
async def null_scope():
    """A no-op interrupt scope (headless / tests)."""
    yield
