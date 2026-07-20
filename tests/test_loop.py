import json

import pytest

import locode.agent.loop as loop_mod
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


def make_loop_with_client(tmp_path, client, confirm=None, cfg=None):
    """Like make_loop, but takes a pre-built client — for tests (below) that
    need a client wired to a fake clock instead of the plain FakeClient."""
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = cfg or Config()
    return AgentLoop(client, FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path), confirm=confirm)


class FakeClock:
    """A controllable stand-in for time.monotonic(), advanced explicitly."""
    def __init__(self):
        self.t = 0.0

    def advance(self, dt: float) -> None:
        self.t += dt

    def now(self) -> float:
        return self.t


class SlowFakeClient(FakeClient):
    """A FakeClient whose completions each burn a fixed slice of (fake)
    wallclock time, so tests can exercise the slow-progress ratio nudge
    without a real 60s+ grace period actually elapsing."""
    def __init__(self, scripted, clock: FakeClock, seconds_per_call: float):
        super().__init__(scripted)
        self._clock = clock
        self._seconds_per_call = seconds_per_call

    async def complete(self, *a, **kw):
        self._clock.advance(self._seconds_per_call)
        return await super().complete(*a, **kw)


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


async def test_repeated_read_nudge_names_unread_file(tmp_path):
    # Reported bug: asked to compare DESIGN.md against test_scraper.py and write
    # POC_TASKS.md, a weak model gets stuck re-reading DESIGN.md and never
    # touches test_scraper.py. The generic "try something different" nudge
    # wasn't actionable enough — it must name the concrete unread file.
    (tmp_path / "DESIGN.md").write_text("design doc")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    loop = make_loop(tmp_path, [
        native_call("read_file", path="DESIGN.md"),
        native_call("read_file", path="DESIGN.md"),
        native_call("read_file", path="DESIGN.md"),
        {"role": "assistant", "content": "Comparing now."},
    ], cfg=cfg)
    out = await loop.run_turn(
        "compare the DESIGN.md with the code in test_scraper.py. Create a new "
        "file POC_TASKS.md and suggest next steps for the POC there.")
    assert out == "Comparing now."
    nudges = [m["content"] for m in loop.history if m["role"] == "user"
              and "repeating it will" in m["content"]]
    assert len(nudges) == 1
    assert "test_scraper.py" in nudges[0]
    assert "poc_tasks.md" not in nudges[0]  # the file to CREATE, not read
    assert "design.md" not in nudges[0]     # already read, not the hint


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


async def test_error_stall_nudged_then_recovers(tmp_path):
    # The subtler stuck signature: the model VARIES its edits every turn (so the
    # identical-call detector never fires) yet keeps hitting the same error. The
    # no-op "old == new" error text is constant regardless of the values, so each
    # call is a distinct signature with an identical error — exactly the case.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_error_stall = 3
    cfg.agent.max_repeat_calls = 99  # ensure the *repeat* path can't fire here
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="a.txt", old="a", new="a"),
        native_call("edit_file", path="a.txt", old="b", new="b"),
        native_call("edit_file", path="a.txt", old="c", new="c"),
        {"role": "assistant", "content": "Right — this needs a rewrite, not a swap."},
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert out == "Right — this needs a rewrite, not a swap."  # recovered
    nudges = [m for m in loop.history if m["role"] == "user"
              and "identical each time" in m["content"]]
    assert len(nudges) == 1


async def test_error_stall_bails_when_ignored(tmp_path):
    # If the model ignores the structural nudge and keeps hitting the same error,
    # the loop bails cleanly instead of grinding to the iteration budget.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_error_stall = 3
    cfg.agent.max_repeat_calls = 99
    cfg.agent.max_iterations = 25
    # Varying, always-erroring edits forever (FakeClient repeats the last).
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="a.txt", old="a", new="a"),
        native_call("edit_file", path="a.txt", old="b", new="b"),
        native_call("edit_file", path="a.txt", old="c", new="c"),
        native_call("edit_file", path="a.txt", old="d", new="d"),
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert "stopped" in out and "same error" in out
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_iterations  # bailed early, not at the budget


async def test_missing_deliverable_nudges_then_recovers(tmp_path):
    # The reported bug: asked to read files then write a PLAN.md, a weak model
    # reads around and then just narrates a plan in prose without ever calling
    # write_file. Must nudge once instead of silently returning the narration.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("stuff")
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "Here's my plan: first we should..."},
        native_call("write_file", path="PLAN.md", content="# Plan\n..."),
        {"role": "assistant", "content": "Done, wrote PLAN.md."},
    ], confirm=confirm)
    out = await loop.run_turn("read a.txt and then make a plan for next steps "
                              "by writing a PLAN.md")
    assert out == "Done, wrote PLAN.md."
    assert (tmp_path / "PLAN.md").read_text() == "# Plan\n..."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "no write_file or edit_file call" in m["content"]]
    assert len(nudges) == 1


async def test_missing_deliverable_nudges_then_accepts_explanation(tmp_path):
    # If the model still doesn't write the file after the nudge, but explains
    # why, the harness must accept that explanation as the final answer rather
    # than nudging forever or hard-stopping.
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "I'll write a PLAN.md with next steps."},
        {"role": "assistant", "content": "I can't write PLAN.md: no clear next "
                                         "steps exist yet without more info."},
    ])
    out = await loop.run_turn("make a plan for next steps by writing a PLAN.md")
    assert out == ("I can't write PLAN.md: no clear next steps exist yet "
                   "without more info.")
    nudges = [m for m in loop.history if m["role"] == "user"
              and "no write_file or edit_file call" in m["content"]]
    assert len(nudges) == 1


async def test_missing_deliverable_not_triggered_when_written(tmp_path):
    # A model that writes the requested file on the very first turn must not be
    # nudged at all.
    async def confirm(name, args, preview):
        return "yes"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="PLAN.md", content="# Plan"),
        {"role": "assistant", "content": "Wrote PLAN.md."},
    ], confirm=confirm)
    out = await loop.run_turn("write a PLAN.md with next steps")
    assert out == "Wrote PLAN.md."
    assert not any("no write_file or edit_file call" in m["content"]
                   for m in loop.history if m["role"] == "user")


async def test_missing_deliverable_survives_a_detour_then_recovers(tmp_path):
    # The real bug this guards against: after one nudge, the model hallucinates
    # success and detours through a (failing) verification read instead of
    # actually writing. The old single-nudge design would then trust the NEXT
    # dead-end unconditionally, silently returning it as "done". It must nudge
    # again instead of letting the false claim slip through.
    async def confirm(name, args, preview):
        return "yes"

    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "Let me create the POC_TASKS.md file."},
        native_call("read_file", path="POC_TASKS.md"),  # hallucinated "verify"
        {"role": "assistant", "content": "Let me create it properly:"},
        native_call("write_file", path="POC_TASKS.md", content="# tasks"),
        {"role": "assistant", "content": "Done, wrote POC_TASKS.md."},
    ], confirm=confirm)
    out = await loop.run_turn("Create a new file POC_TASKS.md with next steps.")
    assert out == "Done, wrote POC_TASKS.md."
    assert (tmp_path / "POC_TASKS.md").read_text() == "# tasks"
    nudges = [m for m in loop.history if m["role"] == "user"
              and "no write_file or edit_file call" in m["content"]]
    assert len(nudges) == 2  # nudged again after the detour, not trusted blindly


async def test_missing_deliverable_bails_after_repeated_detours(tmp_path):
    # If the model keeps detouring (e.g. ls) and dead-ending without ever
    # attempting the write, it must bail with a clear message at the cap
    # instead of grinding or silently accepting a false "done".
    cfg = Config()
    cfg.agent.max_missing_deliverable_retries = 2
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "I'll create POC_TASKS.md now."},
        native_call("ls"),
        {"role": "assistant", "content": "Let me create it properly."},
        native_call("ls"),
        {"role": "assistant", "content": "Working on it."},
    ], cfg=cfg)
    out = await loop.run_turn("Create a new file POC_TASKS.md with next steps.")
    assert "stopped" in out and "poc_tasks.md" in out
    assert not (tmp_path / "POC_TASKS.md").exists()


async def test_missing_deliverable_not_triggered_for_read_only_mentions(tmp_path):
    # A filename mentioned only in a reading context ("read config.py") must not
    # be treated as an expected deliverable — no false-positive nudge.
    (tmp_path / "config.py").write_text("X = 1")
    loop = make_loop(tmp_path, [
        native_call("read_file", path="config.py"),
        {"role": "assistant", "content": "It sets X to 1."},
    ])
    out = await loop.run_turn("read config.py and explain it")
    assert out == "It sets X to 1."
    assert not any("no write_file or edit_file call" in m["content"]
                   for m in loop.history if m["role"] == "user")


async def test_budget_max_iterations(tmp_path):
    cfg = Config()
    cfg.agent.max_iterations = 2
    # Always returns a tool call -> never terminates on its own.
    loop = make_loop(tmp_path, [native_call("ls")], cfg=cfg)
    out = await loop.run_turn("loop forever")
    assert "stopped" in out and "iterations" in out


async def test_history_budget_stops_before_server_crash(tmp_path):
    # Reproduces the shape of a real incident: a model that never repeats an
    # identical call (so max_repeat_calls never fires) or hits the same error
    # twice (so max_error_stall never fires), but keeps re-appending large
    # content each turn. Left unchecked this is exactly what grew a local mlx
    # server's prompt cache past 5GB until it hard-crashed on a Metal OOM
    # abort. The history-size budget must catch it independent of those
    # behavioral detectors — and independent of auto-compact, which is
    # disabled here so this test isolates the hard stop itself rather than
    # exercising compaction (see test_compact.py for that).
    cfg = Config()
    cfg.agent.max_history_chars = 50_000
    cfg.agent.max_repeat_calls = 1000
    cfg.agent.max_error_stall = 1000
    cfg.agent.auto_compact_ratio = 1000

    def big_call(i, size=30_000):
        return {"role": "assistant", "content": "x" * size,
                "tool_calls": [{"id": str(i), "function": {
                    "name": "ls", "arguments": json.dumps({"path": f"d{i}"})}}]}

    scripted = [big_call(i) for i in range(10)]
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("do it")
    assert "stopped" in out and "too large" in out


async def test_wallclock_pauses_during_confirm(tmp_path, monkeypatch):
    # A human taking a long time to approve/deny an ASK tool call isn't the
    # model dawdling — that wait must not count against the turn's wallclock
    # budget. Confirm burns 200s of (fake) wallclock against a 100s budget;
    # without pause-tracking this would hard-stop on "wallclock exceeded"
    # right after the write.
    clock = FakeClock()
    monkeypatch.setattr(loop_mod.time, "monotonic", clock.now)
    cfg = Config()
    cfg.agent.max_wallclock_seconds = 100

    async def confirm(name, args, preview):
        clock.advance(200)
        return "yes"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="out.txt", content="x"),
        {"role": "assistant", "content": "done"},
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("write out.txt")
    assert out == "done"
    assert (tmp_path / "out.txt").read_text() == "x"


def _nudge_messages(loop):
    return [m for m in loop.history if m["role"] == "user"
            and "wallclock time relative" in m["content"]]


async def test_slow_progress_nudges_once_past_grace(tmp_path, monkeypatch):
    # A model that keeps calling tools (so it never hits max_repeat_calls or
    # finishes on its own) but takes 50s per completion, against a 200s
    # wallclock budget and a 50-iteration cap: iterations badly lag wallclock,
    # so the ratio nudge should fire exactly once, then the turn should still
    # hard-stop on the wallclock deadline (the nudge doesn't buy extra time).
    clock = FakeClock()
    monkeypatch.setattr(loop_mod.time, "monotonic", clock.now)
    cfg = Config()
    cfg.agent.max_iterations = 50
    cfg.agent.max_wallclock_seconds = 200
    cfg.agent.max_repeat_calls = 1000
    cfg.agent.max_error_stall = 1000
    cfg.agent.slow_progress_ratio = 0.5
    cfg.agent.slow_progress_grace_seconds = 10
    cfg.agent.slow_progress_grace_iterations = 1
    scripted = [native_call("ls", path=f"d{i}") for i in range(20)]
    client = SlowFakeClient(scripted, clock, seconds_per_call=50)
    loop = make_loop_with_client(tmp_path, client, cfg=cfg)
    out = await loop.run_turn("do it")
    assert "stopped" in out and "wallclock exceeded" in out
    assert len(_nudge_messages(loop)) == 1


async def test_slow_progress_not_triggered_within_grace_period(tmp_path, monkeypatch):
    # Without the grace period, one 50s-costing completion against the default
    # 600s wallclock budget and 50-iteration cap WOULD trip the ratio check
    # (iter_frac 1/50=0.02 < 0.5 x 50/600~=0.0417). A large grace window holds
    # it off, so no nudge should land even though the ratio is unfavorable.
    clock = FakeClock()
    monkeypatch.setattr(loop_mod.time, "monotonic", clock.now)
    cfg = Config()
    cfg.agent.slow_progress_grace_seconds = 1000.0
    scripted = [
        native_call("ls", path="."),
        {"role": "assistant", "content": "done"},
    ]
    client = SlowFakeClient(scripted, clock, seconds_per_call=50)
    loop = make_loop_with_client(tmp_path, client, cfg=cfg)
    out = await loop.run_turn("do it")
    assert out == "done"
    assert len(_nudge_messages(loop)) == 0


async def test_kind_field_never_reaches_the_client(tmp_path):
    # Internal bookkeeping ("kind": "user_prompt"/"assistant"/"tool_result"/
    # "nudge"/"system", used by agent/compact.py) must be stripped before the
    # history is sent to the model server — only role/content belong on the
    # wire.
    seen = []

    class RecordingClient(FakeClient):
        async def complete(self, messages, model, **kw):
            seen.append(messages)
            return await super().complete(messages, model, **kw)

    (tmp_path / "a.txt").write_text("hi")
    loop = make_loop_with_client(tmp_path, RecordingClient([
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "it says hi"},
    ]))
    out = await loop.run_turn("read a.txt")
    assert out == "it says hi"
    assert len(seen) >= 2
    for batch in seen:
        for m in batch:
            assert set(m.keys()) == {"role", "content"}
    # meanwhile the loop's OWN history keeps the "kind" tags
    assert all("kind" in m for m in loop.history)


async def test_explicit_compact_shrinks_history(tmp_path):
    cfg = Config()
    cfg.agent.compact_keep_recent = 2
    loop = make_loop(tmp_path, [{"role": "assistant", "content": "done"}], cfg=cfg)
    await loop.run_turn("hi")
    for i in range(10):
        loop.history.append({
            "role": "user",
            "content": "Tool results:\n\n[ls]\n" + ("x" * 500),
            "kind": "tool_result",
        })
        loop.history.append({"role": "assistant", "content": f"step {i}",
                             "kind": "assistant"})
    before_chars = sum(len(m.get("content") or "") for m in loop.history)
    report = loop.compact()
    after_chars = sum(len(m.get("content") or "") for m in loop.history)
    assert after_chars < before_chars
    assert "->" in report


async def test_auto_compact_fires_before_hard_stop(tmp_path):
    # A long-but-not-stuck session (each turn's tool-result dump is bulky, but
    # distinct, so max_repeat_calls/max_error_stall never trip) must get
    # structurally compacted by the soft threshold BEFORE the hard
    # max_history_chars stop gives up on it — recovering headroom instead of
    # immediately bailing.
    cfg = Config()
    cfg.agent.max_history_chars = 20_000
    cfg.agent.auto_compact_ratio = 0.5
    cfg.agent.compact_keep_recent = 2
    cfg.agent.max_repeat_calls = 1000
    cfg.agent.max_error_stall = 1000

    def big_call(i, size=3_000):
        # Distinct args each turn (different path) so the repeat-call
        # detector never fires; bulky content so history grows fast, like the
        # real incident this whole budget system guards against.
        return {"role": "assistant", "content": "x" * size,
                "tool_calls": [{"id": str(i), "function": {
                    "name": "ls", "arguments": json.dumps({"path": f"d{i}"})}}]}

    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)

    scripted = [big_call(i) for i in range(30)]
    loop = AgentLoop(FakeClient(scripted), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path), on_event=events.append)
    await loop.run_turn("list a bunch of directories")
    assert any(e.get("phase") == "info" and "auto-compacted" in e.get("text", "")
               for e in events)


async def test_slow_progress_not_triggered_when_pace_keeps_up(tmp_path, monkeypatch):
    # Iterations advance in lockstep with (or faster than) the wallclock ratio
    # threshold, so the nudge should never fire even past the grace period.
    clock = FakeClock()
    monkeypatch.setattr(loop_mod.time, "monotonic", clock.now)
    cfg = Config()
    cfg.agent.max_iterations = 50
    cfg.agent.max_wallclock_seconds = 600
    cfg.agent.max_repeat_calls = 99
    cfg.agent.slow_progress_ratio = 0.5
    cfg.agent.slow_progress_grace_seconds = 10
    cfg.agent.slow_progress_grace_iterations = 1
    scripted = [
        native_call("ls", path="a"),
        native_call("ls", path="b"),
        native_call("ls", path="c"),
        {"role": "assistant", "content": "done"},
    ]
    client = SlowFakeClient(scripted, clock, seconds_per_call=20)
    loop = make_loop_with_client(tmp_path, client, cfg=cfg)
    out = await loop.run_turn("do it")
    assert out == "done"
    assert len(_nudge_messages(loop)) == 0
