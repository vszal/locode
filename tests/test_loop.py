import json

import pytest

from locode.agent.loop import AgentLoop
from locode.config import Config
from locode.permissions import PermissionPolicy
from locode.tools.base import Registry
from locode.tools import fs


class FakeClient:
    """Returns scripted assistant messages; repeats the last when exhausted."""
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.n = 0

    async def complete(self, messages, model, *, tools=None, temperature=0.3,
                       max_tokens=4096, cancel=None, on_delta=None):
        msg = self.scripted[min(self.n, len(self.scripted) - 1)]
        self.n += 1
        if on_delta and msg.get("content"):
            on_delta(msg["content"])
        return msg


class FakeManager:
    def __init__(self, model_id="mlx-community/Qwen3-14B-4bit"):
        self.model_id = model_id

    async def ensure_up(self, alias):
        return self.model_id


def native_call(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {
                "name": name, "arguments": json.dumps(args)}}]}


def native_multi(*calls):
    """One assistant message carrying several NATIVE tool_calls (parallel)."""
    tcs = [{"id": str(i), "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(calls)]
    return {"role": "assistant", "content": "", "tool_calls": tcs}


def fenced_multi(*calls):
    """One assistant message with several ```tool blocks back-to-back — how a
    weak local model speculatively dumps a whole plan in a single turn."""
    body = "".join('```tool\n' + json.dumps({"name": n, "args": a}) + '\n```'
                   for n, a in calls)
    return {"role": "assistant", "content": body}


def make_loop(tmp_path, scripted, confirm=None, cfg=None):
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = cfg or Config()
    return AgentLoop(FakeClient(scripted), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path), confirm=confirm)


async def test_plain_answer_no_tools(tmp_path):
    loop = make_loop(tmp_path, [{"role": "assistant", "content": "Hello there."}])
    out = await loop.run_turn("hi")
    assert out == "Hello there."


async def test_tool_call_executes_and_feeds_result(tmp_path):
    (tmp_path / "a.txt").write_text("hello world")
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "The file says hello world."},
    ])
    out = await loop.run_turn("what's in a.txt?")
    assert out == "The file says hello world."
    # the file content was fed back as a tool-results user turn
    joined = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "hello world" in joined


async def test_ask_denied_blocks_write(tmp_path):
    async def confirm(name, args, preview):
        return "no"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="out.txt", content="x"),
        {"role": "assistant", "content": "Okay, I won't write it."},
    ], confirm=confirm)
    out = await loop.run_turn("write out.txt")
    assert out == "Okay, I won't write it."
    assert not (tmp_path / "out.txt").exists()  # write was blocked
    results = [m["content"] for m in loop.history if m["role"] == "user"]
    assert any("denied" in r for r in results)


async def test_ask_yes_allows_write(tmp_path):
    async def confirm(name, args, preview):
        return "yes"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="out.txt", content="data"),
        {"role": "assistant", "content": "Done."},
    ], confirm=confirm)
    out = await loop.run_turn("write it")
    assert out == "Done."
    assert (tmp_path / "out.txt").read_text() == "data"


async def test_always_remembers_permission(tmp_path):
    calls = {"n": 0}

    async def confirm(name, args, preview):
        calls["n"] += 1
        return "always"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="a.txt", content="1"),
        native_call("write_file", path="b.txt", content="2"),
        {"role": "assistant", "content": "Both written."},
    ], confirm=confirm)
    out = await loop.run_turn("write two files")
    assert out == "Both written."
    assert calls["n"] == 1  # asked once, remembered for the second
    assert (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").exists()


async def test_malformed_triggers_nudge(tmp_path):
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": '```tool\n{"name": "ls", oops}\n```'},
        {"role": "assistant", "content": "Recovered, here is the answer."},
    ])
    out = await loop.run_turn("list files")
    assert out == "Recovered, here is the answer."
    assert any("could not be parsed" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_confirm_runs_outside_interrupt_scope(tmp_path):
    # Regression: the Esc key-listener (raw mode) must NOT be active while a
    # tool-approval prompt is showing, or the two fight for the terminal and
    # hang. Here we assert the scope is exited before confirm is called.
    from contextlib import asynccontextmanager

    state = {"active": False, "entered": 0, "confirm_saw_active": None}

    @asynccontextmanager
    async def scope():
        state["active"] = True
        state["entered"] += 1
        try:
            yield
        finally:
            state["active"] = False

    async def confirm(name, args, preview):
        state["confirm_saw_active"] = state["active"]
        return "yes"

    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    loop = AgentLoop(
        FakeClient([native_call("write_file", path="x.txt", content="hi"),
                    {"role": "assistant", "content": "Done."}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path), confirm=confirm, interrupt=scope)
    out = await loop.run_turn("write x")
    assert out == "Done."
    assert state["entered"] == 2           # scope wrapped each model call
    assert state["confirm_saw_active"] is False  # confirm ran OUTSIDE the scope
    assert (tmp_path / "x.txt").read_text() == "hi"


async def test_native_call_leaves_coherent_assistant_turn(tmp_path):
    # A native tool_call carries empty content. The stored assistant turn must
    # not be blank — it should show the call in the fenced format, so a weak
    # model doesn't read the following "Tool results:" as a fresh user request
    # and stop after one step.
    (tmp_path / "a.txt").write_text("hi")
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "It says hi."},
    ])
    out = await loop.run_turn("read a.txt")
    assert out == "It says hi."
    first_assistant = next(m["content"] for m in loop.history
                           if m["role"] == "assistant")
    assert "```tool" in first_assistant and "read_file" in first_assistant


async def test_native_call_with_narration_keeps_both(tmp_path):
    # Qwen3-Coder-style: a native tool_call arrives WITH narration prose. The
    # stored assistant turn must keep the prose AND record the call as a fenced
    # block — dropping the call (the old behavior when content was non-empty)
    # left an incoherent history (narration -> results, no call between) that
    # made the model narrate "let me fix this:" and then stop before editing.
    (tmp_path / "a.txt").write_text("hi")
    msg = native_call("read_file", path="a.txt")
    msg["content"] = "Let me read the file to see what's there."
    loop = make_loop(tmp_path, [msg, {"role": "assistant", "content": "It says hi."}])
    out = await loop.run_turn("read a.txt")
    assert out == "It says hi."
    first_assistant = next(m["content"] for m in loop.history
                           if m["role"] == "assistant")
    assert "Let me read the file" in first_assistant     # narration kept
    assert "```tool" in first_assistant and "read_file" in first_assistant  # call recorded
    # the call must appear exactly once (no duplicate block)
    assert first_assistant.count("read_file") == 1


async def test_truncated_tool_call_nudges_not_dead_ends(tmp_path):
    # A tool call cut off by the token limit leaves an OPENED but unclosed ```tool
    # fence. The parser recovers nothing, so without a guard the loop would return
    # the half-written block as a "final answer" — the exact "stops without
    # editing" symptom. Instead it must nudge once to re-issue a smaller call.
    truncated = ('Let me fix it:\n```tool\n{"name": "edit_file", "args": '
                 '{"path": "a.txt", "old": "a very long block that got cut o')
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": truncated},
        {"role": "assistant", "content": "Recovered with a smaller edit."},
    ])
    out = await loop.run_turn("fix a.txt")
    assert out == "Recovered with a smaller edit."   # did NOT dead-end on turn 1
    assert any("cut off" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_complete_fenced_call_is_not_truncation(tmp_path):
    # A normal, closed ```tool fence must parse and run — never be mistaken for a
    # truncated call.
    (tmp_path / "a.txt").write_text("hello")
    loop = make_loop(tmp_path, [
        fenced_multi(("read_file", {"path": "a.txt"})),
        {"role": "assistant", "content": "it says hello"},
    ])
    out = await loop.run_turn("read a.txt")
    assert out == "it says hello"
    assert not any("cut off" in m["content"]
                   for m in loop.history if m["role"] == "user")


async def test_empty_response_nudges_then_reports(tmp_path):
    # Empty content + no tool call must not silently return "" (which looks like
    # stopping after one step). It nudges once, then surfaces a visible message.
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": ""},   # dead-end #1 -> nudge
        {"role": "assistant", "content": ""},   # dead-end #2 -> give up visibly
    ])
    out = await loop.run_turn("do something")
    assert out == "(the model returned an empty response)"
    assert any("empty message" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_empty_then_recovers(tmp_path):
    # If the first reply is empty, the nudge should let the model recover with a
    # real answer on the next turn.
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "Here is the answer."},
    ])
    out = await loop.run_turn("answer me")
    assert out == "Here is the answer."


async def test_assistant_end_fires_on_cancel(tmp_path):
    # Regression: if the stream is interrupted, assistant_end must still be
    # emitted, or the UI's wait spinner is never stopped and flickers into the
    # prompt. (The spinner is started on assistant_start, stopped on _end.)
    from locode.agent.cancel import CancelledByUser

    class CancellingClient:
        async def complete(self, messages, model, **kw):
            raise CancelledByUser()

    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    loop = AgentLoop(CancellingClient(), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path), on_event=events.append)
    out = await loop.run_turn("hi")
    assert out == "⛔ interrupted"
    phases = [e.get("phase") for e in events]
    assert phases.count("assistant_start") == phases.count("assistant_end") >= 1


async def test_speculative_fenced_batch_runs_only_first(tmp_path):
    # The core fix: a weak model dumps ls→read→edit in ONE turn, with the edit's
    # `old` guessed before it ever saw the file. We must run only the first
    # grounded call (ls); the speculative read/edit must NOT execute, so the
    # bad-guess edit can't fire and cascade into "old not found".
    (tmp_path / "a.txt").write_text("real contents")
    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    loop = AgentLoop(
        FakeClient([
            fenced_multi(("ls", {}),
                         ("read_file", {"path": "a.txt"}),
                         ("edit_file", {"path": "a.txt",
                                        "old": "GUESSED LINE", "new": "x"})),
            {"role": "assistant", "content": "done"},
        ]), FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path), on_event=events.append)
    out = await loop.run_turn("update a.txt")
    assert out == "done"
    # The recorded assistant turn holds ONLY the first call, not the whole plan.
    first_assistant = next(m["content"] for m in loop.history
                           if m["role"] == "assistant")
    assert "ls" in first_assistant
    assert "edit_file" not in first_assistant and "read_file" not in first_assistant
    # The speculative edit never ran -> no "old not found" error in the results.
    results = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "not found" not in results
    assert (tmp_path / "a.txt").read_text() == "real contents"  # untouched
    assert any(e.get("phase") == "info" for e in events)


async def test_single_fenced_call_runs_normally(tmp_path):
    # A lone fenced call must NOT be trimmed away — only multi-call batches are.
    (tmp_path / "a.txt").write_text("hello")
    loop = make_loop(tmp_path, [
        fenced_multi(("read_file", {"path": "a.txt"})),
        {"role": "assistant", "content": "it says hello"},
    ])
    out = await loop.run_turn("read a.txt")
    assert out == "it says hello"
    joined = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "hello" in joined


async def test_native_parallel_calls_not_trimmed(tmp_path):
    # Native tool_calls are intentional parallelism (a reliable model) — run all.
    (tmp_path / "a.txt").write_text("AAA")
    (tmp_path / "b.txt").write_text("BBB")
    loop = make_loop(tmp_path, [
        native_multi(("read_file", {"path": "a.txt"}),
                     ("read_file", {"path": "b.txt"})),
        {"role": "assistant", "content": "both read"},
    ])
    out = await loop.run_turn("read both files")
    assert out == "both read"
    joined = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "AAA" in joined and "BBB" in joined  # both native calls executed


async def test_repeated_malformed_bails_fast(tmp_path):
    # A model that can't fix its own tool JSON must not grind every iteration —
    # after max_malformed_retries it stops with a clear message instead.
    cfg = Config()
    cfg.agent.max_malformed_retries = 3
    bad = {"role": "assistant",
           "content": '```tool\n{"name": "ls", broken json here}\n```'}
    loop = make_loop(tmp_path, [bad], cfg=cfg)  # repeats the bad msg forever
    out = await loop.run_turn("do it")
    assert "stopped" in out and "unparseable" in out
    # bailed at the cap, not after all 25 iterations
    nudges = [m for m in loop.history
              if m["role"] == "user" and "could not be parsed" in m["content"]]
    assert len(nudges) == cfg.agent.max_malformed_retries - 1


async def test_repeated_identical_call_bails(tmp_path):
    # A no-op edit (old == new) repeated every turn is the "stuck" signature seen
    # with weak models; it must bail at max_repeat_calls, not grind to the budget.
    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    cfg.agent.max_iterations = 25
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="a.txt", old="hello", new="hello"),
    ], cfg=cfg)  # FakeClient repeats the last msg forever
    out = await loop.run_turn("fix it")
    assert "stopped" in out and "without making progress" in out
    # bailed at the repeat cap, well before 25 iterations
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_repeat_calls


async def test_repeated_call_nudged_before_bailing(tmp_path):
    # Before hard-stopping a stuck repeat, the loop nudges once — and if the model
    # takes the hint and changes course, the turn recovers instead of dying.
    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="a.txt", old="hello", new="hello"),
        native_call("edit_file", path="a.txt", old="hello", new="hello"),
        native_call("edit_file", path="a.txt", old="hello", new="hello"),
        {"role": "assistant", "content": "OK, the file is already correct."},
    ], cfg=cfg)
    out = await loop.run_turn("fix it")
    assert out == "OK, the file is already correct."  # recovered, did not stop
    nudges = [m for m in loop.history if m["role"] == "user"
              and "repeating it will not change anything" in m["content"]]
    assert len(nudges) == 1


async def test_budget_max_iterations(tmp_path):
    cfg = Config()
    cfg.agent.max_iterations = 2
    # Always returns a tool call -> never terminates on its own.
    loop = make_loop(tmp_path, [native_call("ls")], cfg=cfg)
    out = await loop.run_turn("loop forever")
    assert "stopped" in out and "iterations" in out
