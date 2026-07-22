import asyncio
import json
import time

import httpx
import pytest

from locode.agent.cancel import (CancelToken, CancelledByUser,
                                 DeadlineExceeded)
from locode.model.client import ModelClient


def sse(*objs) -> bytes:
    out = []
    for o in objs:
        out.append(f"data: {json.dumps(o)}\n")
    out.append("data: [DONE]\n")
    return "".join(out).encode()


def delta(**d):
    return {"choices": [{"delta": d}]}


def make_client(handler) -> ModelClient:
    return ModelClient("http://127.0.0.1:8081",
                       transport=httpx.MockTransport(handler))


async def test_content_streaming_and_assembly():
    seen = []

    def handler(req):
        return httpx.Response(200, content=sse(
            delta(content="Hel"), delta(content="lo"), delta(content="!")))

    msg = await make_client(handler).complete(
        [{"role": "user", "content": "hi"}], "qwen14", on_delta=seen.append)
    assert msg["content"] == "Hello!"
    assert seen == ["Hel", "lo", "!"]
    assert "tool_calls" not in msg


async def test_tool_call_deltas_assembled():
    def handler(req):
        return httpx.Response(200, content=sse(
            delta(tool_calls=[{"index": 0, "id": "c1",
                               "function": {"name": "read_", "arguments": '{"pa'}}]),
            delta(tool_calls=[{"index": 0,
                               "function": {"name": "file", "arguments": 'th": "a.py"}'}}]),
        ))

    msg = await make_client(handler).complete(
        [{"role": "user", "content": "read it"}], "qwen14", tools=[{"x": 1}])
    tc = msg["tool_calls"][0]
    assert tc["id"] == "c1"
    assert tc["function"]["name"] == "read_file"
    assert json.loads(tc["function"]["arguments"]) == {"path": "a.py"}


async def test_reasoning_fallback_when_content_empty():
    def handler(req):
        return httpx.Response(200, content=sse(
            delta(reasoning="thinking..."), delta(reasoning=" done")))

    msg = await make_client(handler).complete(
        [{"role": "user", "content": "hi"}], "gemma27")
    assert msg["content"] == "thinking... done"


async def test_strips_mlx_prefix(monkeypatch):
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse(delta(content="ok")))

    await make_client(handler).complete(
        [{"role": "user", "content": "hi"}], "mlx:mlx-community/Qwen3-14B-4bit")
    assert captured["body"]["model"] == "mlx-community/Qwen3-14B-4bit"


async def test_cancel_mid_stream_raises():
    cancel = CancelToken()

    def handler(req):
        return httpx.Response(200, content=sse(
            delta(content="a"), delta(content="b"), delta(content="c")))

    def on_delta(_):
        cancel.cancel()  # cancel after the first token

    with pytest.raises(CancelledByUser):
        await make_client(handler).complete(
            [{"role": "user", "content": "hi"}], "qwen14",
            cancel=cancel, on_delta=on_delta)


async def test_next_line_aborts_while_blocked_on_silent_stream():
    # The core of "abort during a dead-silent wait": a read that never returns
    # must still raise CancelledByUser the moment the token fires.
    import asyncio
    from locode.model.client import _next_line

    cancel = CancelToken()

    class BlockingIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)  # model is silent; never yields a line

    cancel_wait = asyncio.ensure_future(cancel.wait())
    asyncio.get_event_loop().call_later(0.02, cancel.cancel)
    with pytest.raises(CancelledByUser):
        await _next_line(BlockingIter(), cancel, cancel_wait)


async def test_next_line_yields_then_done():
    import asyncio
    from locode.model.client import _next_line, _STREAM_DONE

    cancel = CancelToken()
    cancel_wait = asyncio.ensure_future(cancel.wait())

    class Iter:
        def __init__(self):
            self._it = iter(["a", "b"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    it = Iter()
    assert await _next_line(it, cancel, cancel_wait) == "a"
    assert await _next_line(it, cancel, cancel_wait) == "b"
    assert await _next_line(it, cancel, cancel_wait) is _STREAM_DONE
    cancel_wait.cancel()


async def test_list_models():
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})

    assert await make_client(handler).list_models() == ["m1", "m2"]


# --- wallclock deadline during streaming ----------------------------------
# httpx's timeout is per-read, so a model that keeps emitting tokens never
# trips it. Without an explicit deadline one completion can outrun the whole
# turn's budget, which the agent loop only re-checks between iterations.


async def test_deadline_cuts_off_a_long_stream():
    def handler(req):
        async def body():
            for i in range(1000):
                await asyncio.sleep(0.01)
                yield f"data: {json.dumps(delta(content=f'tok{i} '))}\n".encode()
            yield b"data: [DONE]\n"
        return httpx.Response(200, content=body())

    deadline = time.monotonic() + 0.2
    with pytest.raises(DeadlineExceeded) as ei:
        await make_client(handler).complete(
            [{"role": "user", "content": "hi"}], "qwen14", deadline=deadline)
    # The partial reply survives the cut-off rather than being thrown away.
    assert "tok0" in ei.value.partial


async def test_deadline_already_passed_raises_immediately():
    def handler(req):
        return httpx.Response(200, content=sse(delta(content="hi")))

    with pytest.raises(DeadlineExceeded):
        await make_client(handler).complete(
            [{"role": "user", "content": "hi"}], "qwen14",
            deadline=time.monotonic() - 1)


async def test_stream_finishing_inside_the_deadline_is_unaffected():
    def handler(req):
        return httpx.Response(200, content=sse(
            delta(content="all "), delta(content="done")))

    msg = await make_client(handler).complete(
        [{"role": "user", "content": "hi"}], "qwen14",
        deadline=time.monotonic() + 30)
    assert msg["content"] == "all done"


async def test_cancel_still_wins_over_deadline():
    """A user interrupt and a generous deadline together must still report the
    interrupt — the two paths race in the same wait()."""
    token = CancelToken()

    def handler(req):
        async def body():
            token.cancel()
            for i in range(100):
                await asyncio.sleep(0.05)
                yield f"data: {json.dumps(delta(content='x'))}\n".encode()
        return httpx.Response(200, content=body())

    with pytest.raises(CancelledByUser):
        await make_client(handler).complete(
            [{"role": "user", "content": "hi"}], "qwen14", cancel=token,
            deadline=time.monotonic() + 30)
