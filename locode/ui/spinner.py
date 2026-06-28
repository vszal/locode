"""A tiny async spinner for the long, silent waits a local model imposes:
cold model load, a `/model` switch (stop + wired-memory wait + start), and the
gap before the first generated token. It animates on its own asyncio task and
erases itself when stopped. A short initial delay means fast operations (server
already up, first token arrives quickly) never flash a spinner at all.
"""

from __future__ import annotations

import asyncio
import sys

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(self, write=None, *, enabled: bool = True, interval: float = 0.1,
                 first_delay: float = 0.18):
        self._write = write or (lambda s: (sys.stdout.write(s), sys.stdout.flush()))
        self._enabled = enabled
        self._interval = interval
        self._first_delay = first_delay
        self._task: asyncio.Task | None = None
        self._text = ""

    @property
    def active(self) -> bool:
        return self._task is not None

    def start(self, text: str) -> None:
        if not self._enabled:
            return
        self.stop()
        self._text = text
        self._task = asyncio.ensure_future(self._run())

    def update(self, text: str) -> None:
        self._text = text

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._first_delay)  # let fast ops finish unseen
            i = 0
            while True:
                self._write(f"\r{_FRAMES[i % len(_FRAMES)]} {self._text}\033[K")
                i += 1
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None
        self._write("\r\033[K")  # clear the spinner line
