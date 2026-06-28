import asyncio

from locode.ui.spinner import Spinner


def test_disabled_spinner_is_noop():
    out = []
    sp = Spinner(write=out.append, enabled=False)
    sp.start("loading")
    assert sp.active is False
    assert out == []
    sp.stop()  # safe even when never started


async def test_spinner_animates_then_clears():
    out = []
    sp = Spinner(write=out.append, enabled=True, interval=0.01, first_delay=0.01)
    sp.start("loading model")
    assert sp.active is True
    await asyncio.sleep(0.05)        # let a few frames render
    sp.stop()
    assert sp.active is False
    joined = "".join(out)
    assert "loading model" in joined
    assert joined.endswith("\r\033[K")   # last write clears the line


async def test_spinner_first_delay_suppresses_fast_op():
    out = []
    sp = Spinner(write=out.append, enabled=True, interval=0.01, first_delay=0.2)
    sp.start("blip")
    await asyncio.sleep(0.02)         # stop well before first_delay elapses
    sp.stop()
    # nothing animated; only the clear write happened
    assert "blip" not in "".join(out)
