import asyncio

import pytest

from locode.agent.cancel import CancelToken
from locode.tools.base import ToolContext
from locode.tools.shell import Bash


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path), cancel=CancelToken())


async def test_bash_success(ctx):
    res = await Bash().run({"cmd": "printf 'hello'"}, ctx)
    assert res.ok and res.content == "hello"


async def test_bash_silent_success_is_explicit(ctx):
    # rc 0 with no output must read unambiguously, not as a bare "(no output)"
    # that a weak model re-runs to a repeat-stop. It has to carry BOTH readings:
    # ran-fine (a passing py_compile) and nothing-matched (an empty query). The
    # earlier wording said only "command succeeded", which is misleading for a
    # query and sent the model round the same git call four times.
    res = await Bash().run({"cmd": "true"}, ctx)
    assert res.ok and not res.is_error
    assert "exit 0" in res.content
    assert "ran fine" in res.content        # the verify reading
    assert "nothing matched" in res.content  # the query reading
    assert "unchanged" in res.content        # and don't just re-run it


async def test_bash_nonzero_exit_is_error(ctx):
    res = await Bash().run({"cmd": "exit 3"}, ctx)
    assert res.is_error and "[exit 3]" in res.content


async def test_bash_cwd_is_honored(ctx, tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    res = await Bash().run({"cmd": "ls"}, ctx)
    assert "marker.txt" in res.content


async def test_bash_timeout(ctx):
    res = await Bash().run({"cmd": "sleep 5", "timeout": 1}, ctx)
    assert res.is_error and "timed out" in res.content


async def test_bash_cancel_kills_process(ctx):
    task = asyncio.create_task(Bash().run({"cmd": "sleep 10"}, ctx))
    await asyncio.sleep(0.2)
    ctx.cancel.cancel()  # fires the kill hook
    res = await asyncio.wait_for(task, timeout=3)
    assert res.is_error and "interrupted" in res.content
