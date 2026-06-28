"""Cooperative cancellation for in-flight generation and tool calls.

A single CancelToken threads through the model client and the tool executor.
The UI sets it on Esc / first Ctrl-C; long-running work checks it between
stream chunks (model) or registers a hard-cancel callback (e.g. kill a
subprocess group). Setting the token never tears down the session — the agent
loop unwinds to the prompt and the next turn can proceed.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List


class CancelledByUser(Exception):
    """Raised when work is abandoned because the user interrupted it."""


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        # Hard-cancel hooks (e.g. os.killpg of a running bash) run on cancel().
        self._hooks: List[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        """Mark cancelled and fire any registered hard-cancel hooks (once)."""
        if self._event.is_set():
            return
        self._event.set()
        for hook in self._hooks:
            try:
                hook()
            except Exception:
                # A failing cleanup hook must not mask the cancellation.
                pass

    def reset(self) -> None:
        """Clear state for the next turn. Hooks are dropped."""
        self._event.clear()
        self._hooks.clear()

    def add_cancel_hook(self, hook: Callable[[], None]) -> Callable[[], None]:
        """Register a callback to run if cancel() fires. Returns a deregister fn.

        If already cancelled, the hook runs immediately.
        """
        if self._event.is_set():
            try:
                hook()
            except Exception:
                pass
            return lambda: None
        self._hooks.append(hook)

        def _remove() -> None:
            try:
                self._hooks.remove(hook)
            except ValueError:
                pass

        return _remove

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledByUser()

    async def wait(self) -> None:
        await self._event.wait()
