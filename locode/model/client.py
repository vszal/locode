"""Async HTTP client for the local OpenAI-compatible server (mlx_lm.server).

Streams `/v1/chat/completions`, assembling content + tool_calls from the SSE
deltas and invoking an on_delta callback per token so the UI can render live.
Cancellation is checked between chunks. Talks only to the configured local
endpoint. A `transport` can be injected so tests never touch the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, Awaitable, Callable, Iterable

import httpx

from locode.agent.cancel import (CancelToken, CancelledByUser,
                                 DeadlineExceeded)

OnDelta = Callable[[str], Any]

# Sentinel returned by _next_line at end-of-stream (distinct from a "" SSE line).
_STREAM_DONE = object()


class ModelClient:
    def __init__(self, base_url: str, timeout: float = 600.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, timeout=self._timeout,
                                 transport=self._transport)

    async def list_models(self) -> list[str]:
        async with self._client() as c:
            r = await c.get("/v1/models")
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        cancel: CancelToken | None = None,
        on_delta: OnDelta | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Run one streamed completion. Returns the assembled assistant message
        {"role": "assistant", "content": str, "tool_calls": [...]}.

        Raises CancelledByUser if the cancel token fires mid-stream, and
        DeadlineExceeded if `deadline` (a `time.monotonic()` value) passes
        while still generating. The deadline matters because httpx's timeout is
        per-read: a model streaming steadily toward a 32k-token reply never
        trips it, so without this a single completion can outrun the whole
        turn's wallclock budget — which the agent loop can only check *between*
        iterations."""
        if model.startswith("mlx:"):
            model = model[4:]
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}

        async with self._client() as c:
            async with c.stream("POST", "/v1/chat/completions", json=body) as r:
                r.raise_for_status()
                line_iter = r.aiter_lines()
                # Wait on the cancel token alongside each read so an abort lands
                # even while the model is silent (prefill / first-token latency).
                # Raising here unwinds out of the stream context, closing the
                # connection — which is what signals the server to stop work.
                cancel_wait = asyncio.ensure_future(cancel.wait()) if cancel else None
                try:
                    while True:
                        line = await _next_line(line_iter, cancel, cancel_wait,
                                                deadline)
                        if line is _STREAM_DONE:
                            break
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        piece = delta.get("content")
                        if piece:
                            content_parts.append(piece)
                            if on_delta:
                                res = on_delta(piece)
                                if hasattr(res, "__await__"):
                                    await res  # type: ignore[func-returns-value]
                        rpiece = delta.get("reasoning_content") or delta.get("reasoning")
                        if rpiece:
                            reasoning_parts.append(rpiece)
                        for tc in delta.get("tool_calls") or []:
                            _accumulate_tool_call(tool_acc, tc)
                except DeadlineExceeded:
                    # Re-raise carrying what we already streamed, so the caller
                    # can report/keep the partial reply rather than losing
                    # minutes of generation to a bare exception.
                    raise DeadlineExceeded("".join(content_parts)
                                           or "".join(reasoning_parts)) from None
                finally:
                    if cancel_wait is not None and not cancel_wait.done():
                        cancel_wait.cancel()

        content = "".join(content_parts)
        if not content and reasoning_parts:
            # Reasoning model left thinking on and emitted no content — surface it.
            content = "".join(reasoning_parts)
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_acc:
            msg["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
        return msg


async def _next_line(line_iter, cancel: CancelToken | None, cancel_wait,
                     deadline: float | None = None):
    """Return the next SSE line, or `_STREAM_DONE` at end of stream.

    Races the read against the cancel token so an interrupt aborts promptly even
    while blocked awaiting bytes (a silent model still stops on Esc), instead of
    only being noticed between already-arriving lines. When `deadline` is set,
    the race also has a timeout, so a steadily-generating model is cut off at
    the turn's budget rather than running as long as it likes."""
    if cancel is not None and cancel.cancelled:
        raise CancelledByUser()
    if deadline is not None and time.monotonic() >= deadline:
        raise DeadlineExceeded()
    timeout = (deadline - time.monotonic()) if deadline is not None else None
    if cancel_wait is None:
        try:
            return await asyncio.wait_for(line_iter.__anext__(), timeout)
        except StopAsyncIteration:
            return _STREAM_DONE
        except asyncio.TimeoutError:
            raise DeadlineExceeded() from None
    line_task = asyncio.ensure_future(line_iter.__anext__())
    done, _ = await asyncio.wait({line_task, cancel_wait},
                                 return_when=asyncio.FIRST_COMPLETED,
                                 timeout=timeout)
    if not done:  # neither finished before the deadline
        line_task.cancel()
        with contextlib.suppress(BaseException):
            await line_task
        raise DeadlineExceeded()
    if cancel_wait in done:
        line_task.cancel()
        with contextlib.suppress(BaseException):  # let the cancelled read unwind
            await line_task
        raise CancelledByUser()
    try:
        return line_task.result()
    except StopAsyncIteration:
        return _STREAM_DONE


def _accumulate_tool_call(acc: dict[int, dict[str, Any]], tc: dict[str, Any]) -> None:
    idx = tc.get("index", 0)
    slot = acc.setdefault(idx, {"id": "", "type": "function",
                                "function": {"name": "", "arguments": ""}})
    if tc.get("id"):
        slot["id"] = tc["id"]
    fn = tc.get("function") or {}
    if fn.get("name"):
        slot["function"]["name"] += fn["name"]
    if fn.get("arguments"):
        slot["function"]["arguments"] += fn["arguments"]
