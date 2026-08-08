import json

import pytest

import locode.agent.loop as loop_mod
from locode.agent.loop import AgentLoop
from locode.config import Config
from locode.permissions import PermissionPolicy
from locode.tools.base import Registry
from locode.tools.plan import UpdatePlan
from locode.tools import fs


class FakeClient:
    """Returns scripted assistant messages; repeats the last when exhausted."""
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.n = 0

    async def complete(self, messages, model, *, tools=None, temperature=0.3,
                       max_tokens=4096, cancel=None, on_delta=None,
                       deadline=None, **_kw):
        msg = self.scripted[min(self.n, len(self.scripted) - 1)]
        self.n += 1
        if on_delta and msg.get("content"):
            on_delta(msg["content"])
        return msg


class CyclingClient(FakeClient):
    """Cycles the script forever instead of repeating the last message.

    FakeClient's repeat-the-last behaviour can only express a period-1 stall.
    The stall that actually shows up in the wild is a *cycle*: edit, run the
    test, edit the same way again, run the same test again. Reproducing it
    needs a client that loops.
    """
    async def complete(self, messages, model, **kw):
        msg = self.scripted[self.n % len(self.scripted)]
        self.n += 1
        on_delta = kw.get("on_delta")
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
    reg.register(UpdatePlan())
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
    # Two model calls plus the tool run itself — Esc has to reach a long bash,
    # not just a long generation.
    assert state["entered"] == 3
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


async def test_exhausted_truncation_stops_cleanly_not_raw_block(tmp_path):
    # A model that keeps emitting DIFFERENT unclosed ```tool fences (so the
    # repeat guard never fires) exhausts the truncation-retry budget. Rather than
    # falling through to return the raw half-written JSON as the final answer —
    # what devstral24 e2e runs did, 5/6 ending mid-edit — the loop stops cleanly.
    def cut(tag, path):
        return {"role": "assistant",
                "content": f"Fix {tag}:\n```tool\n{{\"name\": \"edit_file\", "
                           f"\"args\": {{\"path\": \"{path}\", \"old\": \"a long "
                           f"block for {tag} that got cut o"}
    loop = make_loop(tmp_path, [cut("one", "a.txt"), cut("two", "b.txt"),
                                cut("three", "c.txt")])
    out = await loop.run_turn("fix it")
    assert out.startswith("⏹ stopped")
    assert "cut off" in out
    assert "```tool" not in out   # the raw block is NOT surfaced as the answer


async def test_length_finish_reason_nudges_even_without_a_broken_fence(tmp_path):
    # Prose cut off mid-sentence at max_tokens has no unclosed fence for the
    # heuristic to see, so it used to be returned as a confident final answer —
    # half a design document, reported as done. The server's own "length"
    # verdict is the reliable signal.
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "length",
         "content": "The design has three layers. The first is the storage lay"},
        {"role": "assistant", "content": "Here is the shorter version."},
    ])
    out = await loop.run_turn("describe the design")
    assert out == "Here is the shorter version."
    assert any("cut off" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_truncated_write_file_is_salvaged_and_lands_on_disk(tmp_path):
    # A big document written as one write_file cut off at the token limit: the
    # content string never closes, extract() recovers nothing, and today the whole
    # partial reply is lost. Salvage must LAND the partial file, then steer the
    # model to append the rest.
    async def confirm(name, args, preview):
        return "yes"

    partial = "# Design\\n\\n" + "x" * 3000  # JSON-escaped newline in the wire body
    truncated = ('```tool\n{"tool": "write_file", "path": "design.md", '
                 '"content": "' + partial)
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "length", "content": truncated},
        {"role": "assistant", "content": "Appended the rest."},
    ], confirm=confirm)
    out = await loop.run_turn("write a design doc to design.md")
    assert out == "Appended the rest."
    # The partial content is on disk (not evaporated), with its escapes decoded.
    written = (tmp_path / "design.md").read_text()
    assert written.startswith("# Design\n\n")
    assert len(written) > 3000
    # And the model was told to CONTINUE with append_file, not re-write.
    assert any("append_file" in m["content"] and "CUT OFF" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_truncated_write_salvage_is_bounded(tmp_path):
    # A model that keeps re-writing the same doc and truncating every time must
    # not salvage forever. After the bounded budget the turn stops rather than
    # grinding — here the repeat detector catches the identical re-write first.
    async def confirm(name, args, preview):
        return "yes"

    truncated = ('```tool\n{"tool": "write_file", "path": "d.md", '
                 '"content": "' + "y" * 2000)
    # Same truncated reply every time (client repeats its last scripted message).
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "length", "content": truncated},
    ], confirm=confirm)
    out = await loop.run_turn("write d.md")
    assert out.startswith("⏹")  # stopped, did not spin the full budget
    assert (tmp_path / "d.md").exists()


async def test_repetition_reply_is_discarded_and_nudged(tmp_path):
    # The client aborts a degenerate loop with finish_reason="repetition". The
    # garbage content must NOT land in history; the model is nudged to break out
    # and its next, clean reply is returned.
    garbage = "megahyper" * 500
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "repetition", "content": garbage},
        {"role": "assistant", "content": "Right — here is the plan in brief."},
    ])
    out = await loop.run_turn("give me the plan")
    assert out == "Right — here is the plan in brief."
    # The looped text never entered history as an assistant turn.
    assert not any(garbage[:50] in m.get("content", "") for m in loop.history)
    # A repetition nudge was appended.
    assert any("repeating the same text" in m["content"]
               for m in loop.history if m["role"] == "user")


async def test_repetition_aborts_are_bounded(tmp_path):
    # A model that keeps degenerating must not spin forever: after
    # max_repetition_aborts the turn stops instead of nudging endlessly.
    cfg = Config()
    cfg.agent.max_repetition_aborts = 3
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "repetition",
         "content": "loop " * 400},
    ], cfg=cfg)
    out = await loop.run_turn("do the thing")
    assert out.startswith("⏹")
    assert "repetition loop" in out


async def test_stop_finish_reason_is_a_real_answer(tmp_path):
    # The normal case must not be dragged into the truncation path.
    loop = make_loop(tmp_path, [
        {"role": "assistant", "finish_reason": "stop", "content": "All done."},
    ])
    assert await loop.run_turn("status?") == "All done."
    assert not any("cut off" in m["content"]
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
    # identical-call detector never fires) yet keeps hitting the same error. Each
    # edit targets a nonexistent file, so every call is a distinct signature with
    # an identical "no such file" error — exactly the case. (A no-op old==new edit
    # would take the separate no-change path, not this one.)
    async def confirm(name, args, preview):
        return "yes"

    cfg = Config()
    cfg.agent.max_error_stall = 3
    cfg.agent.max_repeat_calls = 99  # ensure the *repeat* path can't fire here
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="ghost.txt", old="a", new="b"),
        native_call("edit_file", path="ghost.txt", old="c", new="d"),
        native_call("edit_file", path="ghost.txt", old="e", new="f"),
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

    cfg = Config()
    cfg.agent.max_error_stall = 3
    cfg.agent.max_repeat_calls = 99
    cfg.agent.max_iterations = 25
    # Varying, always-erroring edits forever (FakeClient repeats the last).
    loop = make_loop(tmp_path, [
        native_call("edit_file", path="ghost.txt", old="a", new="b"),
        native_call("edit_file", path="ghost.txt", old="c", new="d"),
        native_call("edit_file", path="ghost.txt", old="e", new="f"),
        native_call("edit_file", path="ghost.txt", old="g", new="h"),
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert "stopped" in out and "same error" in out
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_iterations  # bailed early, not at the budget


async def test_nochange_edit_nudged_then_recovers(tmp_path):
    # A no-op edit (old==new) is the model editing blind — usually at a line the
    # error names but that is actually fine. The FIRST one is tolerated (models
    # often self-correct); a SECOND consecutive one earns a specific redirect
    # ("editing... NOTHING"), after which the model recovers.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_nochange_edits = 2
    cfg.agent.max_repeat_calls = 99   # isolate the no-change path
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),   # read-before-edit gate
        native_call("edit_file", path="a.txt", old="a", new="a"),
        native_call("edit_file", path="a.txt", old="b", new="b"),
        {"role": "assistant", "content": "Let me run the compiler to find the real line."},
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix the syntax error")
    assert out == "Let me run the compiler to find the real line."  # recovered
    nudges = [m for m in loop.history if m["role"] == "user"
              and "changing the file NOTHING" in m["content"]]
    assert len(nudges) == 1
    # A single no-op earlier must NOT have tripped the same-error stall.
    assert not any("identical each time" in m.get("content", "")
                   for m in loop.history)


async def test_single_nochange_edit_is_tolerated(tmp_path):
    # One no-op then a real edit: no redirect at all — the first blind guess is
    # free, and the streak resets the moment real work happens.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_nochange_edits = 2
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),   # read-before-edit gate
        native_call("edit_file", path="a.txt", old="x", new="x"),   # no-op
        native_call("edit_file", path="a.txt", old="hello", new="world"),  # real
        {"role": "assistant", "content": "done"},
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert out == "done"
    assert (tmp_path / "a.txt").read_text() == "world"
    assert not any("changing the file NOTHING" in m.get("content", "")
                   for m in loop.history)


async def test_nochange_edit_bails_when_ignored(tmp_path):
    # If the model ignores the redirect and keeps submitting no-op edits, the turn
    # ends cleanly instead of grinding out zero-change edits to the budget.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_nochange_edits = 2
    cfg.agent.max_repeat_calls = 99
    cfg.agent.max_iterations = 25
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),   # read-before-edit gate
        native_call("edit_file", path="a.txt", old="a", new="a"),
        native_call("edit_file", path="a.txt", old="b", new="b"),
        native_call("edit_file", path="a.txt", old="c", new="c"),
        native_call("edit_file", path="a.txt", old="d", new="d"),
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert "stopped" in out and "change nothing" in out
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_iterations  # bailed early, not at the budget


async def test_alternating_call_cycle_is_caught(tmp_path):
    # Measured in the eval suite (2026-07-21): a real run alternated a no-op
    # edit_file with an identical `pytest` invocation for all 50 iterations and
    # fired ZERO nudges. Both detectors compared each batch only against the one
    # immediately before it, so a period-2 cycle reset them every single turn.
    # Neither call is ever *consecutively* repeated here, yet the turn is plainly
    # going nowhere.
    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    cfg.agent.max_iterations = 25
    client = CyclingClient([
        native_call("edit_file", path="a.txt", old="hello", new="hello"),
        native_call("read_file", path="a.txt"),
    ])
    loop = make_loop_with_client(tmp_path, client, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert "stopped" in out
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_iterations  # caught the cycle, not the budget


async def test_alternating_error_cycle_is_caught(tmp_path):
    # The same period-2 blindness in the error-stall detector: two *different*
    # failing calls alternating means `error_sig == last_error_sig` is never true,
    # so the streak reset every turn even though each individual error recurred
    # verbatim. max_repeat_calls is disabled so only the error path can fire.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_error_stall = 3
    cfg.agent.max_repeat_calls = 999
    cfg.agent.max_iterations = 25
    client = CyclingClient([
        native_call("edit_file", path="a.txt", old="nope", new="x"),
        native_call("edit_file", path="missing.txt", old="a", new="b"),
    ])
    loop = make_loop_with_client(tmp_path, client, confirm=confirm, cfg=cfg)
    out = await loop.run_turn("fix it")
    assert "stopped" in out and "same error" in out
    runs = sum(1 for m in loop.history
               if m["role"] == "user" and "Tool results" in m["content"])
    assert runs < cfg.agent.max_iterations


async def test_repeated_call_with_changing_result_is_not_a_stall(tmp_path):
    # The counterpart guard. Making the detectors interleaving-immune must not
    # make them trigger-happy: re-reading a file between edits that actually
    # change it is normal, productive work. The same call appears three times
    # here, but its RESULT differs every time, so nothing is stuck.
    async def confirm(name, args, preview):
        return "yes"

    (tmp_path / "a.txt").write_text("hello")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),
        native_call("edit_file", path="a.txt", old="hello", new="world"),
        native_call("read_file", path="a.txt"),
        native_call("edit_file", path="a.txt", old="world", new="there"),
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "Done — it now reads 'there'."},
    ], confirm=confirm, cfg=cfg)
    out = await loop.run_turn("update the greeting")
    assert out == "Done — it now reads 'there'."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "repeating it will not change anything" in m["content"]]
    assert nudges == []


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
    # The dirs must EXIST and must not be EMPTY. This test is about wallclock
    # and nothing else, so its calls have to sail past every other guard: 20
    # failing ls calls trip the consecutive-error guard, and 20 ls calls on
    # empty dirs trip the no-information guard, both long before the clock.
    for i in range(20):
        (tmp_path / f"d{i}").mkdir()
        (tmp_path / f"d{i}" / "keep.txt").write_text("x\n")
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


# --- announced-intent dead-end -------------------------------------------
# A model that says "I'll examine the file:" and emits no tool call used to
# have that narration returned as a confident final answer.

@pytest.mark.parametrize("text", [
    "I'll first examine the file to understand the current implementation:",
    "Let me check the tests before making a change.",
    "Now I will implement the fix.",
    "Sure! Here's what I found:",
    "First, I need to read the config file",
])
def test_announces_next_action_detects_dead_ends(text):
    assert loop_mod._announces_next_action(text)


@pytest.mark.parametrize("text", [
    "The bug was an off-by-one in truncate(). I fixed it and the tests pass.",
    "Done. Let me know if you want the docstrings updated too.",
    "There are three functions in this module: word_wrap, truncate, and "
    "title_case. All of them now behave as the tests require.",
    "",
    "   ",
])
def test_announces_next_action_ignores_real_answers(text):
    assert not loop_mod._announces_next_action(text)


async def test_announced_intent_nudges_then_acts(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "I'll read the file now:"},
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "The file contains the word hello."},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("what is in a.txt?")
    assert "hello" in out
    assert any(e.get("reason") == "announced intent, no action" for e in events)


async def test_announced_intent_nudged_only_once(tmp_path):
    """If it announces intent again after the nudge, take the second reply as
    the answer rather than grinding the whole iteration budget."""
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "Let me look at the code:"},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("check the code")
    assert out == "Let me look at the code:"
    nudges = [e for e in events
              if e.get("reason") == "announced intent, no action"]
    assert len(nudges) == 1


async def test_real_answer_is_not_nudged(tmp_path):
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "The answer is 42."},
    ])
    events = []
    loop._on_event = events.append
    assert await loop.run_turn("what is the answer?") == "The answer is 42."
    assert not any(e.get("phase") == "nudge" for e in events)


# --- single-reply wallclock deadline --------------------------------------

async def test_runaway_reply_stops_at_budget_and_keeps_partial(tmp_path):
    """A completion that outruns the turn budget must end the turn with a
    budget stop, not propagate a raw exception — and the partial text it did
    produce stays in history rather than vanishing."""
    class DeadlineClient:
        async def complete(self, messages, model, **kw):
            assert kw.get("deadline") is not None, "loop must pass a deadline"
            raise loop_mod.DeadlineExceeded("half a design doc")

    loop = make_loop_with_client(tmp_path, DeadlineClient())
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("write DESIGN.md")

    assert out.startswith("⏹")
    assert "the turn's wallclock ran out" in out
    assert loop.history[-1]["content"] == "half a design doc"
    assert any(e.get("phase") == "stopped" for e in events)


async def test_deadline_mid_write_lands_the_partial_document(tmp_path):
    """When the turn's wallclock expires WHILE the model streams a large
    write_file, the partial document must be landed on disk before stopping —
    not thrown away with the rest of the reply. (The r11 design-doc deaths:
    ~24k chars generated into one write_file, then nothing on disk.)"""
    async def confirm(name, args, preview):
        return "yes"

    partial = ('```tool\n{"tool": "write_file", "path": "DESIGN.md", '
               '"content": "# Design\\n\\n' + "x" * 3000)

    class DeadlineMidWrite:
        async def complete(self, messages, model, **kw):
            raise loop_mod.DeadlineExceeded(partial)

    loop = make_loop_with_client(tmp_path, DeadlineMidWrite(), confirm=confirm)
    out = await loop.run_turn("write DESIGN.md")

    assert out.startswith("⏹")
    assert "landed its partial file first" in out
    written = (tmp_path / "DESIGN.md").read_text()
    assert written.startswith("# Design\n\n") and len(written) > 3000


async def test_deadline_shrinks_as_the_turn_progresses(tmp_path):
    """The deadline is the turn's budget, not a per-call one: a later call in
    the same turn gets a deadline no further away than the first."""
    seen = []

    class RecordingClient(FakeClient):
        async def complete(self, messages, model, **kw):
            seen.append(kw.get("deadline"))
            return await super().complete(messages, model, **kw)

    (tmp_path / "a.txt").write_text("x")
    client = RecordingClient([
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "It contains x."},
    ])
    loop = make_loop_with_client(tmp_path, client)
    await loop.run_turn("read a.txt")

    assert len(seen) == 2 and all(d is not None for d in seen)
    assert seen[1] <= seen[0] + 0.01  # same turn budget, not refreshed


# --- plan-aware stopping -----------------------------------------------------
# The model's own update_plan list is the strongest evidence available that a
# turn isn't over: unlike the deliverable check it needs no named output file,
# and unlike the intent heuristic it isn't guessing from phrasing.

def plan_call(*tasks):
    return native_call("update_plan", tasks=list(tasks))


async def test_stopping_with_open_tasks_is_nudged_back_to_work(tmp_path):
    loop = make_loop(tmp_path, [
        plan_call("[>] write DESIGN.md", "[ ] write PLAN.md"),
        {"role": "assistant", "content": "I've written the design."},  # stops early
        plan_call("[x] write DESIGN.md", "[x] write PLAN.md"),         # goes back to work
        {"role": "assistant", "content": "Both documents are written now."},
    ])
    out = await loop.run_turn("design it, then plan it")
    assert out == "Both documents are written now."
    nudge = [m["content"] for m in loop.history
             if m.get("kind") == "nudge" and "not finished" in m["content"]]
    assert len(nudge) == 1
    assert "write PLAN.md" in nudge[0]     # names the actual next task


async def test_no_nudge_once_every_task_is_done(tmp_path):
    loop = make_loop(tmp_path, [
        plan_call("[x] write DESIGN.md", "[x] write PLAN.md"),
        {"role": "assistant", "content": "Both documents are written."},
    ])
    out = await loop.run_turn("design it, then plan it")
    assert out == "Both documents are written."
    assert not any("not finished" in m["content"]
                   for m in loop.history if m.get("kind") == "nudge")


async def test_open_task_nudges_are_bounded(tmp_path):
    # A task the model genuinely can't finish must not become an infinite loop.
    loop = make_loop(tmp_path, [
        plan_call("[ ] do the impossible thing"),
        {"role": "assistant", "content": "I cannot do that."},
    ])
    out = await loop.run_turn("do it")
    assert out == "I cannot do that."
    nudges = [m for m in loop.history
              if m.get("kind") == "nudge" and "not finished" in m["content"]]
    assert len(nudges) == Config().agent.max_open_task_retries


async def test_turn_without_a_plan_is_unaffected(tmp_path):
    # The plan is opt-in: a model that never calls update_plan must behave
    # exactly as it did before the feature existed.
    loop = make_loop(tmp_path, [{"role": "assistant", "content": "Hello."}])
    assert await loop.run_turn("hi") == "Hello."
    assert not loop.plan


async def test_plan_survives_across_turns(tmp_path):
    # "ok, now do step 3" is a continuation, not a new plan.
    loop = make_loop(tmp_path, [
        plan_call("[x] a", "[ ] b"),
        {"role": "assistant", "content": "done a."},
        {"role": "assistant", "content": "done a."},
        {"role": "assistant", "content": "done a."},
        {"role": "assistant", "content": "done a."},
    ])
    await loop.run_turn("do a and b")
    assert loop.plan.summary() == "1/2 done"


# --- generation throughput -------------------------------------------------
def test_reply_chars_counts_prose():
    assert loop_mod._reply_chars({"content": "hello"}) == 5


def test_reply_chars_counts_native_tool_calls():
    """A reply that arrives as structured tool_calls has empty content but cost
    just as much to generate. Counting content alone would report a working
    model as stalled on exactly the turns where it was doing the work."""
    msg = {"content": "", "tool_calls": [{"name": "read_file",
                                          "args": {"path": "a.py"}}]}
    assert loop_mod._reply_chars(msg) > 20


def test_reply_chars_survives_unserializable_tool_calls():
    msg = {"content": "", "tool_calls": [object()]}
    assert loop_mod._reply_chars(msg) > 0


def test_reply_chars_handles_empty_and_missing_fields():
    assert loop_mod._reply_chars({}) == 0
    assert loop_mod._reply_chars({"content": None, "tool_calls": None}) == 0


async def test_assistant_end_reports_generated_chars(tmp_path):
    """The throughput metric the eval harness mines is only as good as this
    event, so pin the field end to end."""
    class OneShotClient:
        async def complete(self, messages, model, **kw):
            return {"content": "done: " + "x" * 100, "finish_reason": "stop"}

    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    loop = AgentLoop(OneShotClient(), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path), on_event=events.append)
    await loop.run_turn("hi")
    ends = [e for e in events if e.get("phase") == "assistant_end"]
    assert ends and ends[0]["chars"] == 106


# --- repeated PROSE (a stall with no tool call) ---------------------------
# Every other stuck-detector keys on a tool-call signature, so a model that
# repeats *itself* rather than a *call* is invisible to all of them. Observed
# in the r6-baseline sweep: an 18,709-char document regenerated verbatim, 245s
# then 266s of a 600s turn, with write_file never called and PLAN.md never
# written.

def long_prose(tag="Milestone 1"):
    """A reply big enough that regenerating it is what burns the turn."""
    body = "# PLAN.md\n\n" + f"{tag}: build the queue.\n" * 200
    assert len(body) >= loop_mod.PROSE_REPEAT_MIN_CHARS
    return {"role": "assistant", "content": body}


async def test_repeated_prose_stops_when_the_deliverable_is_still_missing(
        tmp_path):
    """The r6 failure, in miniature: asked to write a file, the model narrates
    it instead, is nudged, and narrates the identical text again."""
    loop = make_loop(tmp_path, [long_prose()])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("write a PLAN.md for the queue")
    assert "repeated the same reply" in out
    assert any(e.get("phase") == "stopped"
               and "repeated the same reply" in e.get("reason", "")
               for e in events)


async def test_repeated_prose_stops_on_the_first_repeat(tmp_path):
    """It must not nudge twice. A turn holds only about two max-length
    replies, so a second nudge just dies mid-reply instead."""
    loop = make_loop(tmp_path, [long_prose()])
    events = []
    loop._on_event = events.append
    await loop.run_turn("write a PLAN.md for the queue")
    assert len([e for e in events if e.get("phase") == "nudge"]) == 1


async def test_repeated_prose_stops_while_the_model_plan_has_open_tasks(
        tmp_path):
    """The other grinding path: no named deliverable, but the model's own plan
    says it isn't finished."""
    loop = make_loop(tmp_path, [
        plan_call("[ ] do the impossible thing"),
        long_prose(),
    ])
    out = await loop.run_turn("do the impossible thing")
    assert "repeated the same reply" in out


async def test_differing_prose_is_not_a_repeat(tmp_path):
    """A model that reacts to the nudge with different text is making progress
    and must not be stopped."""
    loop = make_loop(tmp_path, [
        long_prose("Milestone 1"),
        long_prose("Milestone 2"),
    ])
    out = await loop.run_turn("write a PLAN.md for the queue")
    assert "repeated the same reply" not in out
    assert "Milestone 2" in out


async def test_repeat_check_ignores_whitespace_reflow(tmp_path):
    """Byte-identical is too strict: the same document re-emitted with a
    different line wrap is the same stall."""
    body = long_prose()["content"]
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": body},
        {"role": "assistant", "content": body.replace("\n", "\n  ")},
    ])
    out = await loop.run_turn("write a PLAN.md for the queue")
    assert "repeated the same reply" in out


async def test_a_short_repeated_answer_is_still_returned(tmp_path):
    """Regenerating a terse reply costs nothing, so it keeps its ordinary
    handling — the size gate is what keeps this check from hijacking the
    announced-intent and missing-deliverable paths."""
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "Let me look at the code:"},
    ])
    out = await loop.run_turn("check the code")
    assert out == "Let me look at the code:"


async def test_missing_deliverable_nudge_names_the_drafted_document(tmp_path):
    """When the model wrote the document into chat, the nudge must say so.
    The generic text claims it "only looked around" — false here, and a model
    that just spent a quarter of the turn composing the document answers that
    by composing it again."""
    loop = make_loop(tmp_path, [long_prose()])
    out = await loop.run_turn("write a PLAN.md for the queue")
    nudge = next(m for m in loop.history if m.get("kind") == "nudge")
    assert "into your reply" in nudge["content"]
    assert "do NOT write that text out again".lower() in nudge["content"].lower()
    assert "write_file" in nudge["content"]
    assert "only looked around" not in nudge["content"]


async def test_missing_deliverable_nudge_is_unchanged_when_nothing_was_drafted(
        tmp_path):
    """A model that genuinely only read files still gets the original wording."""
    (tmp_path / "a.txt").write_text("hi")
    loop = make_loop(tmp_path, [
        native_call("read_file", path="a.txt"),
        {"role": "assistant", "content": "I looked at it."},
    ])
    await loop.run_turn("write a PLAN.md for the queue")
    nudge = next(m for m in loop.history if m.get("kind") == "nudge")
    assert "only looked around" in nudge["content"]


# --- the prose signature, against real sampled output ---------------------

def test_prose_signature_tolerates_a_single_character_of_sampling_noise():
    """The r7-prose run regenerated a 25,391-char document that differed in ONE
    character 13,659 in — a real newline where the first copy had a literal
    backslash-n. Exact equality called that a different reply, so the detector
    never fired and the turn died on wallclock exactly as before."""
    a = "# taskq Design Document\n" + ("filler line here\n" * 900) + "|\\\n| tail"
    b = a.replace("|\\\n| tail", "|\\n| tail")
    assert a != b and len(a) == len(b)
    assert loop_mod._same_prose(loop_mod._prose_sig(a), loop_mod._prose_sig(b))


def test_a_materially_shorter_document_is_not_a_repeat():
    """What a truncation nudge ASKS for is a shorter document — and a shorter
    document opens exactly the same way. On the prefix alone, complying would be
    indistinguishable from stalling, so the length test is load-bearing."""
    long_doc = "# taskq Design Document\n" + ("filler line here\n" * 900)
    short_doc = "# taskq Design Document\n" + ("filler line here\n" * 200)
    assert not loop_mod._same_prose(loop_mod._prose_sig(long_doc),
                                    loop_mod._prose_sig(short_doc))


def test_replies_with_different_openings_are_not_a_repeat():
    a = "# Design\n" + ("x" * 5000)
    b = "# Plan\n" + ("x" * 5000)
    assert not loop_mod._same_prose(loop_mod._prose_sig(a),
                                    loop_mod._prose_sig(b))


async def test_near_identical_truncated_reply_stops_the_turn(tmp_path):
    """End to end, on the shape that actually occurred: a truncated write_file
    re-emitted with one character changed."""
    head = ('```tool\n{"name": "write_file", "args": {"path": "DESIGN.md", '
            '"content": "# taskq Design Document\\n')
    a = head + ("section text here\\n" * 300) + "|\\\n| tail"
    b = head + ("section text here\\n" * 300) + "|\\n| tail"
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": a},
        {"role": "assistant", "content": b},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("write a DESIGN.md for the queue")
    assert "repeated the same reply" in out
    assert len([e for e in events if e.get("phase") == "nudge"]) == 1


# --- refusals: a denial the model can act on ---------------------------------

async def test_headless_denial_says_the_tool_is_gone_for_good(tmp_path):
    # No confirm callback: nothing can ever approve write_file, so the refusal
    # has to say so rather than leaving the model to try variants.
    loop = make_loop(tmp_path, [
        native_call("write_file", path="out.txt", content="x"),
        {"role": "assistant", "content": "I cannot write files here."},
    ])
    out = await loop.run_turn("write out.txt")
    assert out == "I cannot write files here."
    fed = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "not available in this session" in fed
    assert "Do NOT retry write_file" in fed


async def test_user_denial_is_worded_as_a_no_for_now(tmp_path):
    async def confirm(name, args, preview):
        return "no"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="out.txt", content="x"),
        {"role": "assistant", "content": "Okay."},
    ], confirm=confirm)
    await loop.run_turn("write out.txt")
    fed = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "the user refused" in fed
    assert "not available in this session" not in fed


async def test_headless_stops_after_repeated_refusals(tmp_path):
    # The reported failure mode: the model keeps trying *variants* of an
    # install, so no two calls match and the repeat detector never fires.
    # Every one of them is refused, which is the signal that matters.
    client = CyclingClient([
        native_call("write_file", path="a.txt", content="1"),
        native_call("write_file", path="b.txt", content="2"),
        native_call("write_file", path="c.txt", content="3"),
        native_call("write_file", path="d.txt", content="4"),
    ])
    loop = make_loop_with_client(tmp_path, client)
    out = await loop.run_turn("write some files")
    assert "not available in this session" in out
    assert not any(tmp_path.glob("*.txt"))


async def test_interactive_denials_do_not_end_the_turn(tmp_path):
    # A user may decline several unrelated calls in a turn that is otherwise
    # going fine — only the headless case is provably hopeless.
    async def confirm(name, args, preview):
        return "no" if args.get("path", "").startswith("skip") else "yes"

    loop = make_loop(tmp_path, [
        native_call("write_file", path="skip1.txt", content="1"),
        native_call("write_file", path="skip2.txt", content="2"),
        native_call("write_file", path="skip3.txt", content="3"),
        native_call("write_file", path="keep.txt", content="4"),
        {"role": "assistant", "content": "Wrote the one you allowed."},
    ], confirm=confirm)
    out = await loop.run_turn("write four files")
    assert out == "Wrote the one you allowed."
    assert (tmp_path / "keep.txt").read_text() == "4"


async def test_denial_events_carry_a_reason(tmp_path):
    # "⛔ bash denied" with no preceding prompt is indistinguishable from a
    # broken tool. Every refusal has to say which of the four things happened.
    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    cfg.permissions.tools["write_file"] = "deny"
    loop = AgentLoop(
        FakeClient([native_call("write_file", path="out.txt", content="x"),
                    {"role": "assistant", "content": "Okay."}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path), on_event=events.append)
    await loop.run_turn("write out.txt")
    denied = [e for e in events if e["phase"] == "denied"]
    assert [e["reason"] for e in denied] == ["config"]


async def test_a_remembered_no_always_is_permanent_for_the_model_too(tmp_path):
    # "no (always)" pins DENY for the session, and every later call is refused
    # with no prompt. Telling the model to "try a different approach" then sends
    # it round the same loop; it has to hear that the tool is gone.
    asked = []

    async def confirm(name, args, preview):
        asked.append(args["path"])
        return "no_always"

    events = []
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = Config()
    loop = AgentLoop(
        FakeClient([native_call("write_file", path="a.txt", content="1"),
                    native_call("write_file", path="b.txt", content="2"),
                    {"role": "assistant", "content": "Stopped."}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path), confirm=confirm, on_event=events.append)
    await loop.run_turn("write two files")
    assert asked == ["a.txt"]                    # the second never prompted
    reasons = [e["reason"] for e in events if e["phase"] == "denied"]
    assert reasons == ["user declined", "session policy"]
    fed = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "that answer stands for the whole session" in fed


async def test_a_tool_that_prompts_runs_outside_the_interrupt_scope(tmp_path):
    # ask_user opens its own prompt_toolkit selector from inside run(). Raw mode
    # and a prompt_toolkit Application cannot share stdin, so the loop must skip
    # the scope for it — by the prompts_user flag, not by tool name.
    from contextlib import asynccontextmanager

    from locode.tools.ask import AskUser

    state = {"active": False, "saw_active": None}

    @asynccontextmanager
    async def scope():
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    async def select(question, options):
        state["saw_active"] = state["active"]
        return options[0]

    reg = Registry()
    reg.register(AskUser())
    cfg = Config()
    loop = AgentLoop(
        FakeClient([native_call("ask_user", question="which?",
                                options=["a", "b"]),
                    {"role": "assistant", "content": "Got it."}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path), select=select, interrupt=scope)
    await loop.run_turn("ask me")
    assert state["saw_active"] is False


async def test_a_tool_raising_does_not_end_the_turn(tmp_path):
    # A 9B model emitting edit_file with no `new` field made args["new"] raise
    # KeyError('new'), which escaped run_turn and killed a run 19 iterations
    # deep — the whole turn lost, logged as the single word "'new'". The model
    # can recover from a tool error; it cannot recover from the loop exiting.
    class Exploding:
        name = "boom"
        description = "raises"
        permission = "auto"
        schema = {"type": "object", "properties": {}}

        async def run(self, args, ctx):
            raise KeyError("new")

    reg = Registry()
    reg.register(Exploding())
    cfg = Config()
    loop = AgentLoop(
        FakeClient([native_call("boom"),
                    {"role": "assistant", "content": "Recovered."}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path))
    out = await loop.run_turn("go")
    assert out == "Recovered."
    fed = "\n".join(m["content"] for m in loop.history if m["role"] == "user")
    assert "boom failed: KeyError" in fed
    assert "against the tool's schema" in fed


async def test_cancellation_still_propagates_through_a_tool(tmp_path):
    # The catch-all must not swallow the two exceptions that mean "stop the
    # turn on purpose" — an interrupt would otherwise be reported back to the
    # model as a tool error and the loop would carry on.
    from locode.agent.cancel import CancelledByUser

    class Cancelling:
        name = "boom"
        description = "cancels"
        permission = "auto"
        schema = {"type": "object", "properties": {}}

        async def run(self, args, ctx):
            raise CancelledByUser()

    reg = Registry()
    reg.register(Cancelling())
    cfg = Config()
    loop = AgentLoop(
        FakeClient([native_call("boom"),
                    {"role": "assistant", "content": "should not get here"}]),
        FakeManager(), reg, PermissionPolicy(cfg.permissions), cfg,
        cwd=str(tmp_path))
    # _run_turn already turns it into the interrupted result, which is the
    # point: it must reach that handler rather than be reported to the model as
    # a tool error and the loop carry on.
    assert await loop.run_turn("go") == "⛔ interrupted"


# --- seen-green test gate (Option C) -----------------------------------------
class FakeBash:
    """A stand-in for the bash tool that returns scripted output, so the
    seen-green gate can be exercised without running a real subprocess. Name is
    "bash" because the gate keys on that (the only tool that runs tests)."""
    name = "bash"
    description = "run a shell command"
    permission = "auto"
    schema = {"type": "object",
              "properties": {"cmd": {"type": "string"}}}

    def __init__(self, output: str, is_error: bool = False):
        self.output = output
        self.is_error = is_error

    async def run(self, args, ctx):
        from locode.tools.base import ToolResult
        return ToolResult(self.output, is_error=self.is_error)


def make_loop_with_bash(tmp_path, scripted, bash: FakeBash, cfg=None):
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    reg.register(UpdatePlan())
    reg.register(bash)
    cfg = cfg or Config()
    cfg.permissions.tools["bash"] = "auto"  # run it headless without an approver
    return AgentLoop(FakeClient(scripted), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg,
                     cwd=str(tmp_path))


async def test_claims_tests_pass_without_seeing_green_is_nudged(tmp_path):
    # The "should now pass" false-completion: the model runs the suite, it is
    # NOT green, yet it ends the turn asserting the tests pass. Gate nudges once,
    # then the model recovers with a plain answer that is returned.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="pytest -q"),
         {"role": "assistant", "content": "The tests should now pass."},
         {"role": "assistant", "content": "Corrected the off-by-one."}],
        FakeBash("1 failed, 2 passed in 0.10s", is_error=True))
    out = await loop.run_turn("fix the failing test")
    assert out == "Corrected the off-by-one."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "passing test result" in m["content"]]
    assert len(nudges) == 1


async def test_claims_tests_pass_after_green_is_trusted(tmp_path):
    # A green pytest tally DID appear this turn, so the same "tests pass" claim
    # is a verified finish — no nudge, returned directly.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="pytest -q"),
         {"role": "assistant", "content": "All tests pass."}],
        FakeBash("3 passed in 0.05s"))
    out = await loop.run_turn("fix the failing test")
    assert out == "All tests pass."
    assert not [m for m in loop.history if m["role"] == "user"
                and "passing test result" in m["content"]]


async def test_non_test_final_answer_is_not_gated(tmp_path):
    # A finish with no claim about tests — a design/plan task — never trips the
    # gate even though no green result was seen this turn.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="ls"),
         {"role": "assistant", "content": "The design document is complete."}],
        FakeBash("cart.py  test_cart.py"))
    out = await loop.run_turn("write the design doc")
    assert out == "The design document is complete."
    assert not [m for m in loop.history if m["role"] == "user"
                and "passing test result" in m["content"]]


async def test_unverified_tests_gate_fires_only_once(tmp_path):
    # If the model repeats the unverified claim after the nudge instead of
    # actually running the suite, the second claim is returned — the gate fires
    # once and does not grind.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="pytest -q"),
         {"role": "assistant", "content": "The tests should pass now."},
         {"role": "assistant", "content": "Tests pass."}],
        FakeBash("1 failed in 0.10s", is_error=True))
    out = await loop.run_turn("fix the failing test")
    assert out == "Tests pass."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "passing test result" in m["content"]]
    assert len(nudges) == 1


def test_looks_green_test_recognizes_pytest_tally():
    assert loop_mod._looks_green_test("5 passed in 0.12s")
    assert loop_mod._looks_green_test("collected 5 items\n\n5 passed")
    # A partial run is not green.
    assert not loop_mod._looks_green_test("3 passed, 1 failed in 0.2s")
    assert not loop_mod._looks_green_test("2 passed\n1 error")
    assert not loop_mod._looks_green_test("no tests ran")
    # A traceback alongside a passed count is not green either.
    assert not loop_mod._looks_green_test("1 passed\nTraceback (most recent call last):")


def test_test_claim_matches_pass_assertions_not_doc_language():
    m = loop_mod._TEST_CLAIM_RE.search
    assert m("The tests should now pass.")
    assert m("All tests pass.")
    assert m("the test suite passes")
    assert m("the tests are passing")
    # Non-test finishes must not match.
    assert not m("The design document is complete.")
    assert not m("I passed the file path to the function.")
    assert not m("The plan is written.")


# --- unverified compile/run claim (hallucinated-verify false-completion) ------
async def test_claims_compiles_without_running_check_is_nudged(tmp_path):
    # The reproduced gemmacoder12 syntax-fix false-completion: the model reads a
    # broken file, asserts it "compiles" and is "syntactically correct", and
    # tries to finish WITHOUT ever running py_compile. No pathology counter sees
    # it (no repeat, no fail); only this gate does. Nudge once, then the model
    # recovers with a plain answer that is returned.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("read_file", path="parser.py"),
         {"role": "assistant",
          "content": "The file is syntactically correct and already compiles."},
         {"role": "assistant", "content": "Fixed the missing colon."}],
        FakeBash("unused"))
    (tmp_path / "parser.py").write_text("def parse(line)\n    return line\n")
    out = await loop.run_turn("fix the syntax error so py_compile succeeds")
    assert out == "Fixed the missing colon."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "compiles / runs cleanly" in m["content"]]
    assert len(nudges) == 1


async def test_claims_compiles_after_clean_check_is_trusted(tmp_path):
    # A verify command (py_compile) ran and exited cleanly this turn, so the
    # same "compiles" claim is a verified finish — returned directly, no nudge.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="python3 -m py_compile parser.py"),
         {"role": "assistant", "content": "The file compiles cleanly now."}],
        FakeBash("(exit 0 — command succeeded, no output)"))
    out = await loop.run_turn("fix the syntax error")
    assert out == "The file compiles cleanly now."
    assert not [m for m in loop.history if m["role"] == "user"
                and "compiles / runs cleanly" in m["content"]]


async def test_failing_compile_does_not_credit_verify_ok(tmp_path):
    # py_compile ran but FAILED (is_error) — the model must not be credited with
    # a clean check, so a subsequent "it compiles" claim is still gated.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("bash", cmd="python3 -m py_compile parser.py"),
         {"role": "assistant", "content": "It compiles fine now."},
         {"role": "assistant", "content": "Done."}],
        FakeBash("SyntaxError: expected ':'", is_error=True))
    out = await loop.run_turn("fix the syntax error")
    assert out == "Done."
    nudges = [m for m in loop.history if m["role"] == "user"
              and "compiles / runs cleanly" in m["content"]]
    assert len(nudges) == 1


async def test_non_verify_final_answer_is_not_gated_by_compile_gate(tmp_path):
    # A finish that makes no compile/run claim never trips the gate, even with no
    # clean check this turn.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("read_file", path="notes.md"),
         {"role": "assistant", "content": "Here is a summary of the module."}],
        FakeBash("unused"))
    (tmp_path / "notes.md").write_text("hello\n")
    out = await loop.run_turn("summarize the module")
    assert out == "Here is a summary of the module."
    assert not [m for m in loop.history if m["role"] == "user"
                and "compiles / runs cleanly" in m["content"]]


def test_verify_claim_matches_compile_run_language_not_prose():
    m = loop_mod._VERIFY_CLAIM_RE.search
    assert m("The file is syntactically correct and already compiles.")
    assert m("py_compile succeeds")
    assert m("It compiles cleanly now.")
    assert m("no syntax errors")
    assert m("the script runs without error")
    assert m("it imports cleanly")
    # Ordinary prose must not match.
    assert not m("This function computes the running total.")
    assert not m("I will run the tests next.")
    assert not m("The plan is complete.")


def test_is_verify_bash_tolerates_non_string_cmd():
    # A weak model sometimes emits cmd as a LIST of argv tokens; the check runs
    # before the tool executes and must not crash on `.lower()` (regression:
    # 'list' object has no attribute 'lower' killed a live run).
    assert loop_mod._is_verify_bash(["python3", "-m", "py_compile", "x.py"])
    assert loop_mod._is_verify_bash("python3 -m py_compile x.py")
    assert not loop_mod._is_verify_bash(["ls", "-la"])
    assert not loop_mod._is_verify_bash(None)
    assert not loop_mod._is_verify_bash("")


# --- verify-task crediting (qythos9 add-test open-plan re-do loop) -------------
def test_is_verify_task_matches_run_verify_tests_only():
    from locode.agent.plan import Task
    m = lambda s: loop_mod._is_verify_task(Task(text=s))
    # run/verify a test suite — these ARE completed by a green run
    assert m("Run pytest and verify all tests pass")
    assert m("run the tests")
    assert m("make test_stats.py pass")
    assert m("Confirm the test suite is green")
    assert m("execute the tests and ensure they pass")
    # a test FILE to write, or unrelated work — must NOT match (no run/verify verb)
    assert not m("Create test_primes.py with pytest tests")
    assert not m("Write the is_prime function")
    assert not m("Write DESIGN.md")
    assert not loop_mod._is_verify_task(None)


async def test_green_test_credits_forgotten_verify_task_and_finishes(tmp_path):
    # qythos9 add-test, measured 2026-07-27: the model wrote the code, ran the
    # suite to green, but ended narrating "All tests pass" WITHOUT marking its
    # own "run the tests" task done. The plan stayed open, the open-tasks nudge
    # fired, and the model re-ran the passing tests to a repeat-stop. A green
    # result IS that task's completion, so the loop credits it and finishes.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("update_plan", tasks=["[x] Write test_primes.py",
                                            "[>] Run pytest and verify all tests pass"]),
         native_call("bash", cmd="pytest -q"),
         {"role": "assistant", "content": "All tests pass."}],
        FakeBash("4 passed in 0.01s"))
    out = await loop.run_turn("add tests for is_prime")
    assert out == "All tests pass."
    assert loop.plan.complete
    assert loop.plan.summary() == "2/2 done"
    # the re-do driver — the open-tasks nudge — must NOT have fired
    assert not [m for m in loop.history if m["role"] == "user"
                and "task(s) open" in m["content"]]


async def test_open_verify_task_without_green_is_not_credited(tmp_path):
    # The credit is double-locked: with no green result this turn, the verify
    # task stays open and the ordinary open-tasks nudge still fires — a failing
    # or un-run suite can't be credited as a pass.
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("update_plan", tasks=["[x] Write test_primes.py",
                                            "[>] Run pytest and verify all tests pass"]),
         native_call("bash", cmd="pytest -q"),
         {"role": "assistant", "content": "Still working on it."}],
        FakeBash("1 failed in 0.10s", is_error=True))
    await loop.run_turn("add tests for is_prime")
    assert not loop.plan.complete
    assert [m for m in loop.history if m["role"] == "user"
            and "task(s) open" in m["content"]]


async def test_completed_plan_restated_finishes_cleanly(tmp_path):
    # gemmacoder12 rename-across-files, measured 2026-07-27: the model finished
    # all the work, marked its plan 3/3 done, then re-emitted the identical
    # finished plan instead of stopping — and hit a repeat-stop whose message
    # ("repeated the same tool call without making progress") reads as a FAILURE
    # on work that in fact landed. A redundant update_plan on an already-complete
    # plan is the model signalling completion the only way it knows; finish
    # cleanly with a success-toned answer, not the failure-toned repeat-stop.
    done = ["[x] Rename in models.py", "[x] Rename in views.py", "[x] Verify"]
    loop = make_loop(tmp_path, [native_call("update_plan", tasks=done)])
    out = await loop.run_turn("rename get_user to fetch_user")
    assert loop.plan.complete
    assert "All planned tasks are complete" in out
    assert "without making progress" not in out


async def test_incomplete_plan_restated_does_not_early_finish(tmp_path):
    # The completion gate is real: an OPEN plan re-stated is not a finish signal —
    # the model still has declared work to do, so the clean-finish path must NOT
    # fire (it falls through to ordinary repeat handling instead).
    loop = make_loop(
        tmp_path,
        [native_call("update_plan", tasks=["[x] Rename in models.py",
                                           "[ ] Rename in views.py"])])
    out = await loop.run_turn("rename get_user to fetch_user")
    assert not loop.plan.complete
    assert "All planned tasks are complete" not in out


async def test_completed_plan_finishes_on_first_restate(tmp_path):
    # gemmacoder12 already-correct AND rename-across-files, measured 2026-07-27:
    # the common shape is a TWO-call restate — the model completes its plan, then
    # re-emits the identical finished plan exactly once before self-terminating
    # (build 53's guard only caught the 3-call variant, so this benign-but-noisy
    # spin still got flagged). The clean-finish must fire on the FIRST repeat.
    # Script a third, distinct assistant message that is only ever reached if we
    # DID NOT finish at the first restate: its sentinel must not surface.
    done = ["[x] Rename in models.py", "[x] Rename in views.py", "[x] Verify"]
    loop = make_loop(tmp_path, [
        native_call("update_plan", tasks=done),
        native_call("update_plan", tasks=done),
        {"role": "assistant", "content": "SENTINEL-THIRD-TURN"}])
    out = await loop.run_turn("rename get_user to fetch_user")
    assert loop.plan.complete
    assert "All planned tasks are complete" in out
    assert "SENTINEL-THIRD-TURN" not in out  # finished before consuming turn 3
    assert "without making progress" not in out


# --- repeated mutating edit (gemmacoder12 duplicating-replace loop) -----------
async def test_repeated_mutating_edit_stops_despite_varying_echo(tmp_path):
    # gemmacoder12, user-reported: the model re-issues a byte-IDENTICAL
    # replace_lines every turn. Each "succeeds" with a DIFFERENT echo — the diff
    # marches down the file (@@ -144 → -146 → -148…) as the edit keeps DUPLICATING
    # content and the file grows. The result-changed reset would hold the repeat
    # streak at 1 forever, so the corrupting edit ran without bound while the
    # model declared false success. A repeated mutating edit must trip the repeat
    # guard on its call signature alone, regardless of the shifting result echo.
    class VaryingEdit:
        name = "replace_lines"
        description = "replace a line range"
        permission = "auto"
        schema = {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"},
            "end": {"type": "integer"}, "new": {"type": "string"}}}

        def __init__(self):
            self.calls = 0

        async def run(self, args, ctx):
            from locode.tools.base import ToolResult
            self.calls += 1
            # A new diff offset every call -> result_sig differs each time.
            return ToolResult(f"replaced lines 136-137 (diff @@ -{144 + 2 * self.calls})")

    edit = VaryingEdit()
    reg = Registry()
    reg.register(edit)
    cfg = Config()
    cfg.permissions.tools["replace_lines"] = "auto"  # run headless, no approver
    call = native_call("replace_lines", path="./f.py", start=136, end=137,
                       new="    with tempfile.TemporaryDirectory() as tmp:")
    loop = AgentLoop(CyclingClient([call]), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg, cwd=str(tmp_path))
    out = await loop.run_turn("fix the empty with-block")

    assert out.startswith("⏹ stopped")
    assert "repeated the same tool call" in out
    # Bounded: the corrupting edit runs at most until the guard trips, NOT forever.
    assert edit.calls == cfg.agent.max_repeat_calls - 1
    # The edit-specific nudge fired (tells it to re-read), not the generic one
    # that would falsely claim "it returned the same result each time".
    nudges = [m["content"] for m in loop.history
              if m["role"] == "user" and m.get("kind") == "nudge"]
    assert any("RE-READ" in n for n in nudges)
    assert not any("returned the same result each time" in n for n in nudges)


async def test_unverified_edits_nudges_after_repeated_blind_edits(tmp_path):
    # Lever 2 (verify-gate): three edits to the same file in a row with no run
    # and no re-read between them earns a one-time nudge to look at ground truth
    # before editing again — the open loop behind the duplicated-mess failure.
    cfg = Config()
    cfg.permissions.tools["write_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("write_file", path="./f.py", content="one\n"),
         native_call("write_file", path="./f.py", content="two\n"),
         native_call("write_file", path="./f.py", content="three\n"),
         {"role": "assistant", "content": "Done."}],
        cfg=cfg)
    out = await loop.run_turn("keep fixing f.py")
    assert out == "Done."
    nudges = [m["content"] for m in loop.history
              if m["role"] == "user" and m.get("kind") == "nudge"]
    assert any("re-read" in n and "f.py" in n for n in nudges)
    # The nudge landed AFTER the third edit; the model then answered, so no
    # fourth write happened.
    assert (tmp_path / "f.py").read_text() == "three\n"


async def test_verify_bash_run_resets_the_verify_gate(tmp_path):
    # A py_compile/pytest/python run between edits closes the loop, so the gate
    # must credit it and NOT nudge — here two edits, a verify run, two more.
    cfg = Config()
    cfg.permissions.tools["write_file"] = "auto"
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("write_file", path="./f.py", content="one\n"),
         native_call("write_file", path="./f.py", content="two\n"),
         native_call("bash", cmd="python -m py_compile f.py"),
         native_call("write_file", path="./f.py", content="three\n"),
         native_call("write_file", path="./f.py", content="four\n"),
         {"role": "assistant", "content": "Done."}],
        FakeBash("OK"), cfg=cfg)
    out = await loop.run_turn("keep fixing f.py")
    assert out == "Done."
    assert not any(m.get("kind") == "nudge"
                   and "re-read" in m.get("content", "")
                   for m in loop.history)


async def test_reread_resets_the_verify_gate(tmp_path):
    # Re-reading the file is also looking at ground truth, so it must reset the
    # gate the same way a verify run does.
    cfg = Config()
    cfg.permissions.tools["write_file"] = "auto"
    cfg.permissions.tools["read_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("write_file", path="./f.py", content="one\n"),
         native_call("write_file", path="./f.py", content="two\n"),
         native_call("read_file", path="./f.py"),
         native_call("write_file", path="./f.py", content="three\n"),
         native_call("write_file", path="./f.py", content="four\n"),
         {"role": "assistant", "content": "Done."}],
        cfg=cfg)
    out = await loop.run_turn("keep fixing f.py")
    assert out == "Done."
    assert not any(m.get("kind") == "nudge"
                   and "re-read" in m.get("content", "")
                   for m in loop.history)


async def test_non_verify_bash_does_not_reset_the_gate(tmp_path):
    # A poke-around command (ls) sees text, not behavior — it must NOT count as
    # verification, so the gate still fires on the third blind edit.
    cfg = Config()
    cfg.permissions.tools["write_file"] = "auto"
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("write_file", path="./f.py", content="one\n"),
         native_call("write_file", path="./f.py", content="two\n"),
         native_call("bash", cmd="ls -la"),
         native_call("write_file", path="./f.py", content="three\n"),
         {"role": "assistant", "content": "Done."}],
        FakeBash("f.py"), cfg=cfg)
    out = await loop.run_turn("keep fixing f.py")
    assert out == "Done."
    nudges = [m["content"] for m in loop.history
              if m["role"] == "user" and m.get("kind") == "nudge"]
    assert any("re-read" in n and "f.py" in n for n in nudges)


async def test_verify_nudge_carries_an_episodic_ledger(tmp_path):
    # Lever 3 (action-ledger): the verify-gate nudge prepends a terse recap of
    # what the turn has already done — edits per file and checks run (with a
    # not-green note) — so a model that has lost the thread sees its own history.
    cfg = Config()
    cfg.permissions.tools["write_file"] = "auto"
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("write_file", path="./f.py", content="one\n"),
         native_call("write_file", path="./f.py", content="two\n"),
         native_call("bash", cmd="pytest -q"),
         native_call("write_file", path="./f.py", content="three\n"),
         native_call("write_file", path="./f.py", content="four\n"),
         native_call("write_file", path="./f.py", content="five\n"),
         {"role": "assistant", "content": "Done."}],
        FakeBash("1 failed, 0 passed in 0.10s", is_error=True), cfg=cfg)
    out = await loop.run_turn("keep fixing f.py")
    assert out == "Done."
    nudge = next(m["content"] for m in loop.history
                 if m.get("kind") == "nudge" and "re-read" in m["content"])
    assert nudge.startswith("So far this turn you have:")
    assert "edited f.py 5×" in nudge
    assert "run a check 1× (still not green)" in nudge


def test_ledger_line_is_empty_when_nothing_worth_reciting():
    from locode.agent.loop import _ledger_line
    # A single edit and no runs isn't worth a recap.
    assert _ledger_line({"f.py": 1}, {}, 0, False) == ""
    # A green check is noted without the not-green tag.
    line = _ledger_line({"f.py": 2}, {}, 1, True)
    assert "edited f.py 2×" in line and "run a check 1×" in line
    assert "not green" not in line


# --- compaction vs the repeat guard -----------------------------------------
# Two guards that are each correct alone were killing turns together: compaction
# tells the model "output omitted — re-read if you need it", then the repeat
# guard stops it for making that identical read. See _forgive_rereads.

def _sig(name, **args):
    return (name, json.dumps(args, sort_keys=True, ensure_ascii=False))


def test_forgive_rereads_clears_read_only_streaks():
    from locode.agent.loop import _forgive_rereads
    streaks = {(_sig("read_file", path="a.py"),): ("out", 2),
               (_sig("grep", pattern="x"),): ("out", 1)}
    nudged = {(_sig("read_file", path="a.py"),)}
    assert _forgive_rereads(streaks, nudged, {}) == 2
    assert streaks == {}
    assert nudged == set()  # nudge re-armed, so it doesn't stop on sight


def test_forgive_rereads_never_forgives_mutations_or_bash():
    from locode.agent.loop import _forgive_rereads
    # A repeated mutating edit is not progress no matter what the context looks
    # like (build 42), and bash can mutate — both keep their streaks.
    edit = (_sig("edit_file", path="a.py", old="x", new="y"),)
    shell = (_sig("bash", command="rm -rf build"),)
    streaks = {edit: ("ok", 2), shell: ("ok", 2)}
    assert _forgive_rereads(streaks, set(), {}) == 0
    assert set(streaks) == {edit, shell}


def test_forgive_rereads_leaves_mixed_batches_alone():
    from locode.agent.loop import _forgive_rereads
    # One batch carrying both a read and an edit still counts as a repeat.
    mixed = (_sig("read_file", path="a.py"), _sig("edit_file", path="a.py"))
    streaks = {mixed: ("ok", 2)}
    assert _forgive_rereads(streaks, set(), {}) == 0
    assert set(streaks) == {mixed}


def test_forgive_rereads_is_bounded_per_signature():
    # Unbounded forgiveness disarms the repeat guard exactly when compaction is
    # frequent: every firing wipes the streak, so a real read loop never
    # accumulates one. Measured live at a 70k budget — 7 compactions, 18
    # forgiven re-reads, 16 repeats, no answer.
    from locode.agent.loop import _forgive_rereads, _MAX_FORGIVEN_REREADS
    sig = (_sig("read_file", path="a.py"),)
    counts: dict = {}
    for _ in range(_MAX_FORGIVEN_REREADS):
        assert _forgive_rereads({sig: ("out", 2)}, set(), counts) == 1
    streaks = {sig: ("out", 2)}
    assert _forgive_rereads(streaks, set(), counts) == 0
    assert streaks == {sig: ("out", 2)}   # the guard can see it again


@pytest.mark.asyncio
async def test_compaction_mid_turn_does_not_repeat_stop_a_reread(tmp_path):
    # The live build-58 failure: the model finished its edit, then re-read three
    # files whose output compaction had discarded, and was repeat-stopped for it.
    for n in "abc":
        (tmp_path / f"{n}.py").write_text(f"# {n}\n" + ("x = 1\n" * 1200))
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.agent.max_history_chars = 40_000
    cfg.agent.auto_compact_ratio = 0.25   # compact early and often
    reads = [native_call("read_file", path=f"./{n}.py") for n in "abc"]
    loop = make_loop(tmp_path, reads + reads
                     + [{"role": "assistant", "content": "a.py is longest."}], cfg=cfg)
    out = await loop.run_turn("read the three files, then tell me about them")
    assert out == "a.py is longest."
    assert "repeated the same tool call" not in out


@pytest.mark.asyncio
async def test_reread_loop_still_stops_despite_frequent_compaction(tmp_path):
    # The other side of the bound: the same read over and over, with compaction
    # firing between each, must still be caught. Forgiveness buys a re-read a
    # second chance, not immunity.
    (tmp_path / "a.py").write_text("# a\n" + ("x = 1\n" * 1200))
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.agent.max_history_chars = 40_000
    cfg.agent.auto_compact_ratio = 0.25
    loop = make_loop(tmp_path, [native_call("read_file", path="./a.py")] * 12, cfg=cfg)
    out = await loop.run_turn("read a.py")
    assert "repeated the same tool call" in out


@pytest.mark.asyncio
async def test_repeat_stop_still_fires_when_compaction_never_runs(tmp_path):
    # The guard must keep working normally — this is the control for the test
    # above, with the history far too small to ever trigger compaction.
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    loop = make_loop(tmp_path, [native_call("read_file", path="./a.py")] * 8, cfg=cfg)
    out = await loop.run_turn("read a.py")
    assert "repeated the same tool call" in out


# --- the open-tasks nudge vs the repeat guard (4.19) ------------------------
# Guards fighting each other, the same shape as compaction-vs-repeat (4.14) from
# the other direction. Live: the fix landed, `python3 sync.py` printed the right
# answer, and the turn was reported as "repeated the same tool call without
# making progress" -- because the plan still said "run the script again", the
# open-tasks nudge demanded it, and the repeat guard punished the compliance.

def test_forgive_nudged_verifies_covers_reads_and_verify_bash():
    from locode.agent.loop import _forgive_nudged_verifies
    read = (_sig("read_file", path="a.py"),)
    verify = (_sig("bash", cmd="python3 sync.py"),)
    streaks = {read: ("out", 2), verify: ("out", 2)}
    assert _forgive_nudged_verifies(streaks, set(), {}) == 2
    assert streaks == {}


def test_forgive_nudged_verifies_refuses_mutating_shell():
    from locode.agent.loop import _forgive_nudged_verifies
    # bash is only excused when it CHECKS. A destructive command repeated after
    # a nudge is still a loop, and re-running it is not free.
    mutate = (_sig("bash", cmd="rm -rf build && cp -r a b"),)
    edit = (_sig("edit_file", path="a.py", old="x", new="y"),)
    streaks = {mutate: ("ok", 2), edit: ("ok", 2)}
    assert _forgive_nudged_verifies(streaks, set(), {}) == 0
    assert set(streaks) == {mutate, edit}


def test_forgive_nudged_verifies_is_bounded():
    from locode.agent.loop import (_forgive_nudged_verifies,
                                   _MAX_FORGIVEN_NUDGED)
    sig = (_sig("bash", cmd="pytest -q"),)
    counts: dict = {}
    for _ in range(_MAX_FORGIVEN_NUDGED):
        assert _forgive_nudged_verifies({sig: ("out", 2)}, set(), counts) == 1
    streaks = {sig: ("out", 2)}
    assert _forgive_nudged_verifies(streaks, set(), counts) == 0
    assert streaks == {sig: ("out", 2)}   # the guard can see it again


def test_forgive_nudged_verifies_survives_bad_bash_args(tmp_path):
    # A weak model sometimes emits cmd as a list, or omits it. Must not raise.
    from locode.agent.loop import _forgive_nudged_verifies
    weird = (("bash", "not json at all"),)
    nocmd = (_sig("bash", command="pytest -q"),)   # wrong key
    streaks = {weird: ("ok", 2), nocmd: ("ok", 2)}
    assert _forgive_nudged_verifies(streaks, set(), {}) == 0


@pytest.mark.asyncio
async def test_verify_rerun_demanded_by_plan_nudge_is_not_a_repeat(tmp_path):
    # The whole transcript, end to end: work, verify, leave a task open, get
    # nudged, comply by re-verifying. That must not end the turn as a repeat.
    (tmp_path / "sync.py").write_text("print('differs: button.txt')\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    run = native_call("bash", cmd="python3 sync.py")
    loop = make_loop(
        tmp_path,
        [native_call("update_plan", tasks=["[x] fix it", "[ ] re-run to verify"]),
         run,                                   # verified once
         run,                                   # the re-run the nudge demands
         {"role": "assistant", "content": "verified, output is correct"}],
        cfg=cfg, confirm=lambda *a, **k: True)
    out = await loop.run_turn("fix sync.py and verify it")
    assert "repeated the same tool call" not in out
    assert out == "verified, output is correct"
# Every other guard in the loop keys on FAILURE, so a model that is wrong in a
# way that produces no errors falls through all of them. Live shape: the model
# read SOURCE_PATH = "skills/cloud/gke-compute-classes" out of the script it was
# debugging and queried git for it — ls-remote, ls-tree, ls-tree with 2>&1,
# ls-tree again. Six exit-0 empty results, four byte-identical. The emptiness
# WAS the diagnosis (no such prefix in the repo) and nothing could say so.

def _noinfo_nudges(loop):
    return [m for m in loop.history
            if m.get("kind") == "nudge" and "came back empty" in m["content"]]


def test_is_noinfo_matches_whole_results_only(tmp_path):
    from locode.agent.loop import _is_noinfo
    from locode.tools.shell import _EMPTY_OK
    assert _is_noinfo(_EMPTY_OK)
    assert _is_noinfo("(no matches)")
    assert _is_noinfo("  (empty directory)  ")   # stripped
    assert not _is_noinfo("")                    # not a tool result we produce
    # The trap: a grep that really did find the literal text "(no matches)" in
    # some file has found something. Substring matching would erase that.
    assert not _is_noinfo("src/a.py:12:    return '(no matches)'")


@pytest.mark.asyncio
async def test_all_empty_calls_are_nudged_then_stopped(tmp_path):
    cfg = Config()
    cfg.agent.max_noinfo_calls = 3
    # Every pattern distinct, so the repeat guard never fires; every call
    # succeeds, so neither the error stall nor the all-errored guard can.
    (tmp_path / "a.py").write_text("x = 1\n")
    scripted = [native_call("grep", pattern=f"nomatch{i}") for i in range(12)]
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("find the thing")
    assert len(_noinfo_nudges(loop)) == 1        # nudged once...
    assert "kept coming back empty" in out       # ...then the turn ends
    body = _noinfo_nudges(loop)[0]["content"]
    assert "assumption" in body                  # name the actual fault
    assert "nothing matched" in body


@pytest.mark.asyncio
async def test_one_informative_call_clears_the_empty_streak(tmp_path):
    # A model that IS learning something must be untouched, even if most of its
    # calls come back empty.
    cfg = Config()
    cfg.agent.max_noinfo_calls = 3
    # Each hit must be a DISTINCT call: repeating one identical successful grep
    # trips the repeat guard, which spends the iteration on a nudge instead of
    # running it — and then the empties really do land back-to-back.
    (tmp_path / "a.py").write_text("".join(f"needle{i} = 1\n" for i in range(6)))
    scripted = []
    for i in range(6):
        scripted += [native_call("grep", pattern=f"nomatch{i}a"),
                     native_call("grep", pattern=f"nomatch{i}b"),
                     native_call("grep", pattern=f"needle{i}")]   # this one hits
    scripted.append({"role": "assistant", "content": "found it"})
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("find the needle")
    assert out == "found it"
    assert _noinfo_nudges(loop) == []


@pytest.mark.asyncio
async def test_failing_calls_do_not_count_as_empty(tmp_path):
    # An error is not "no information" — it says a great deal. These two guards
    # must not double-count the same batch.
    cfg = Config()
    cfg.agent.max_noinfo_calls = 3
    cfg.agent.max_consecutive_errors = 99      # keep the sibling guard out
    scripted = [native_call("read_file", path=f"./gone{i}.py") for i in range(8)]
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("read them")
    assert _noinfo_nudges(loop) == []
    assert "kept coming back empty" not in out


# --- telling the model it was compacted (4.17) ------------------------------
# Compaction used to be invisible to the model: evidence vanished from under it
# with no signal, so it read the gap as forgetfulness and re-read — which costs
# the same space again and compacts again. Live thrash (eval long-context-find,
# 88k of files against a 70k budget): read alpha..echo, compact, then
# alpha/bravo/alpha/charlie/alpha/charlie/delta/delta until the repeat guard
# stopped the turn with the question unanswered.

def _compact_notices(events):
    # Counted from the event stream, not the final history: the notice is an
    # ordinary message and a LATER compaction pass may well shrink it away
    # again. What matters is that it was in front of the model at the moment
    # its context was cut, which is exactly what the event records.
    return [e for e in events
            if e.get("phase") == "nudge" and e.get("reason") == "context compacted"]


@pytest.mark.asyncio
async def test_compaction_tells_the_model_it_happened(tmp_path):
    for n in "abc":
        (tmp_path / f"{n}.py").write_text(f"# {n}\n" + ("x = 1\n" * 1200))
    cfg = Config()
    cfg.agent.max_history_chars = 40_000
    cfg.agent.auto_compact_ratio = 0.25
    reads = [native_call("read_file", path=f"./{n}.py") for n in "abc"]
    loop = make_loop(tmp_path, reads + [{"role": "assistant", "content": "done"}],
                     cfg=cfg)
    events: list = []
    loop._on_event = events.append
    await loop.run_turn("read the three files")
    assert _compact_notices(events), \
        "the model was never told its context had been compacted"
    body = next(m["content"] for m in loop.history
                if m.get("kind") == "nudge" and "Context notice" in m["content"])
    assert "re-read" in body      # the specific behaviour to avoid
    assert "survive" in body      # and why writing conclusions down works


@pytest.mark.asyncio
async def test_compaction_notice_is_bounded(tmp_path):
    # Repeating the advice every single compaction turns it into boilerplate
    # AND spends the very budget it is warning about. Say it twice, then stop.
    from locode.agent.loop import _MAX_COMPACT_NOTICES
    (tmp_path / "a.py").write_text("# a\n" + ("x = 1\n" * 1200))
    cfg = Config()
    cfg.agent.max_repeat_calls = 50        # let it run; we're counting notices
    cfg.agent.max_history_chars = 40_000
    cfg.agent.auto_compact_ratio = 0.25
    loop = make_loop(tmp_path, [native_call("read_file", path="./a.py")] * 14,
                     cfg=cfg)
    events: list = []
    loop._on_event = events.append
    await loop.run_turn("read a.py")
    # Compaction fires on nearly every one of the ~50 iterations here; the
    # notice must not.
    assert len(_compact_notices(events)) == _MAX_COMPACT_NOTICES


@pytest.mark.asyncio
async def test_no_compaction_no_notice(tmp_path):
    # Control: a small turn must not get a scary context warning.
    (tmp_path / "a.py").write_text("x = 1\n")
    loop = make_loop(tmp_path, [native_call("read_file", path="./a.py"),
                                {"role": "assistant", "content": "read it"}])
    events: list = []
    loop._on_event = events.append
    await loop.run_turn("read a.py")
    assert _compact_notices(events) == []


# --- every call failing (4.16) ----------------------------------------------
# Content-independent sibling of the same-error stall. A model guessing at
# filenames gets a NEW error each time, so neither max_error_stall (keyed on
# error text) nor the repeat guard (keyed on the call) can see it. Observed:
# nine consecutive iterations reading notes/golf.py ... notes/tango.py, none of
# which existed, after compaction dropped the real file contents.

def _allerr_nudges(loop):
    return [m for m in loop.history
            if m.get("kind") == "nudge" and "ALL failed" in m["content"]]


@pytest.mark.asyncio
async def test_distinct_failing_calls_are_nudged_then_stopped(tmp_path):
    cfg = Config()
    cfg.agent.max_consecutive_errors = 3
    # Every path distinct, so the repeat guard never fires; every error text
    # distinct, so the same-error stall never fires either.
    scripted = [native_call("read_file", path=f"./gone{i}.py") for i in range(12)]
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("find the handler")
    assert len(_allerr_nudges(loop)) == 1        # steered once...
    assert "every tool call kept failing" in out  # ...then the turn ends


@pytest.mark.asyncio
async def test_one_success_clears_the_all_error_streak(tmp_path):
    # The guard must not punish a model that is failing occasionally but
    # getting somewhere: any success in a batch resets the count.
    cfg = Config()
    cfg.agent.max_consecutive_errors = 3
    scripted = []
    for i in range(6):
        (tmp_path / f"ok{i}.py").write_text(f"x = {i}\n")
        scripted.append(native_call("read_file", path=f"./gone{i}.py"))
        scripted.append(native_call("read_file", path=f"./ok{i}.py"))
    scripted.append({"role": "assistant", "content": "a.py holds x."})
    loop = make_loop(tmp_path, scripted, cfg=cfg)
    out = await loop.run_turn("look around")
    assert out == "a.py holds x."
    assert _allerr_nudges(loop) == []


@pytest.mark.asyncio
async def test_partly_failing_batch_does_not_count(tmp_path):
    # A parallel batch where one call succeeded did something; only a batch in
    # which NOTHING succeeded is counted.
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.agent.max_consecutive_errors = 2
    batch = native_multi(("read_file", {"path": "./a.py"}),
                         ("read_file", {"path": "./gone.py"}))
    loop = make_loop(tmp_path, [batch] * 5
                     + [{"role": "assistant", "content": "done looking."}], cfg=cfg)
    out = await loop.run_turn("look")
    assert _allerr_nudges(loop) == []
    assert "every tool call kept failing" not in out


# --- the zero-change completion gate (lever #3) ---------------------------
#
# The pathology: the model answers "done" having taken no action at all. Every
# other stop-net counts actions — repeats, no-ops, errors — so with zero actions
# there is nothing for any of them to count. Measured live (gemmacoder12
# diff-report, 2026-07-28): one iteration, zero tool calls, a self-terminated
# "answered", and silently wrong output.

@pytest.mark.parametrize("text", [
    "fix the bug in report.py",
    "remove the debug block from main.py",
    "add a docstring to utils.py",
    "refactor parser.py so it stops using globals",
    "config.yaml needs updating to point at the new host",
    "rename the helper in tools.py",
    # The shape that motivated widening _CHANGE_WINDOW to 120 (build 78): the
    # brief names the file first, describes the bug for a full sentence, and
    # only then says what to do. This is the verbatim `two-bugs` eval prompt,
    # whose two recorded zero-mutation false completions the gate could not see
    # at the old 80-char window — 120 characters separate "stats.py" from "Fix".
    ("stats.py has two separate bugs. In the total function the loop subtracts "
     "each value instead of adding it. In the average function there is a stray "
     "plus one that makes the result wrong. Fix both so that total of 1, 2, 3 "
     "is 6 and average of 1, 2, 3 is 2.0."),
])
def test_change_requests_are_recognised(text):
    assert loop_mod._asks_for_a_change(text)


def test_the_window_stops_short_of_the_nothing_to_fix_case():
    """The `already-correct` eval prompt must stay quiet at the shipped window.

    It is the canonical false-positive risk for this gate: a request that names
    a file and uses the word "fix", but only conditionally ("fix it only if it
    is actually wrong"), so a model that verifies and correctly changes nothing
    is RIGHT. It survives at 120 and trips at 200, which is what bounds the
    widening — pin it so a future bump has to confront this case.
    """
    text = ('palindrome.py has an is_palindrome function that should ignore '
            'case, spaces, and punctuation. Verify it correctly returns True '
            'for "A man, a plan, a canal: Panama" and False for "hello", and '
            'fix it only if it is actually wrong.')
    assert not loop_mod._asks_for_a_change(text)


@pytest.mark.parametrize("text", [
    "what does report.py do?",
    "explain how the queue works in main.py",
    "which function in utils.py handles retries?",
    "summarize the design decisions in ARCHITECTURE.md",
    # No file named at all — the gate deliberately stays quiet rather than
    # guessing at what a bare "fix it" refers to.
    "fix the bug",
    "write a short summary of what this project does",
])
def test_read_only_and_unanchored_requests_are_not_change_requests(text):
    assert not loop_mod._asks_for_a_change(text)


@pytest.mark.parametrize("text", [
    # The `long-context-find` brief, verbatim-shaped: a real change request that
    # names its target as a DIRECTORY, so the filename-with-extension anchor
    # could not see it. It was 1 of the 4 recorded gate escapees.
    "Every handler function in the notes directory is supposed to add a "
    "comment line reading FIXME unreviewed directly above its def line.",
    "remove the dead fixtures from the tests directory",
    "rename every fixture in the dst directory",
])
def test_a_named_directory_anchors_a_change_request(text):
    assert loop_mod._asks_for_a_change(text)


@pytest.mark.parametrize("text", [
    # A determiner is not a name. "this directory" appears 6× across the two
    # batteries in briefs that are questions, not change requests; treating it
    # as an anchor would turn the gate on for any of them that happens to use a
    # change verb within the window.
    "what does this directory contain?",
    "list the files in the current folder and explain what each one does",
    # Still no anchor of any kind — a bare change verb must not be enough, or
    # the gate fires on 34 of the 37 battery prompts including `already-correct`.
    "fix whatever is broken",
])
def test_an_unnamed_directory_is_not_an_anchor(text):
    assert not loop_mod._asks_for_a_change(text)


async def test_declaring_done_without_acting_is_nudged(tmp_path):
    (tmp_path / "report.py").write_text("def f():\n    return 1\n")
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "The bug is fixed."},
        native_call("read_file", path="report.py"),
        {"role": "assistant", "content": "Now corrected in report.py."},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("fix the bug in report.py")
    assert "corrected" in out
    assert any(e.get("reason") == "declared done without acting" for e in events)


async def test_the_zero_change_gate_fires_only_once(tmp_path):
    """A model that insists it is done gets its second answer returned, not a
    third nudge — the gate's claim is that "I did nothing" deserves to be said
    twice, not that it is always wrong."""
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "Nothing to do; the code is correct."},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("fix the bug in report.py")
    assert out == "Nothing to do; the code is correct."
    assert len([e for e in events
                if e.get("reason") == "declared done without acting"]) == 1


async def test_a_read_only_question_is_never_gated(tmp_path):
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "It parses the log and prints a total."},
    ])
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("what does report.py do?")
    assert out.startswith("It parses")
    assert not any(e.get("reason") == "declared done without acting"
                   for e in events)


async def test_an_edit_disarms_the_gate(tmp_path):
    (tmp_path / "report.py").write_text("x = 1\n")
    loop = make_loop(tmp_path, [
        native_call("write_file", path="report.py", content="x = 2\n"),
        {"role": "assistant", "content": "Done."},
    ])
    events = []
    loop._on_event = events.append
    assert await loop.run_turn("fix the bug in report.py") == "Done."
    assert not any(e.get("reason") == "declared done without acting"
                   for e in events)


async def test_a_bash_run_disarms_the_gate(tmp_path):
    """bash counts as having acted even when the command only reads: it CAN
    mutate and the loop cannot cheaply tell which, so the gate stays quiet."""
    (tmp_path / "report.py").write_text("x = 1\n")
    loop = make_loop_with_bash(tmp_path, [
        native_call("bash", cmd="cat report.py"),
        {"role": "assistant", "content": "Already correct — no change needed."},
    ], FakeBash("x = 1"))
    events = []
    loop._on_event = events.append
    out = await loop.run_turn("fix the bug in report.py")
    assert "no change needed" in out
    assert not any(e.get("reason") == "declared done without acting"
                   for e in events)


async def test_a_named_deliverable_stays_with_the_deliverable_gate(tmp_path):
    """The two gates partition on expected_artifacts. "write a PLAN.md" is the
    deliverable gate's case; stacking both nudged one mistake twice and turned a
    clean return into a repeat-stop."""
    loop = make_loop(tmp_path, [
        {"role": "assistant", "content": "Here is the plan: step one, step two."},
    ])
    events = []
    loop._on_event = events.append
    await loop.run_turn("write a PLAN.md for the queue")
    reasons = [e.get("reason") for e in events if e.get("phase") == "nudge"]
    assert not any(r == "declared done without acting" for r in reasons)
    assert any("deliverable" in (r or "") for r in reasons)


# --- done-on-repeated-verify: a success that locode was calling a failure ----
# Measured shape (syntax-fix, gemmacoder12_4bit, build 79): fix the file, run
# py_compile, watch it pass, re-run the identical check instead of saying so.
# 0/10 clean finishes on runs that all scored 1.00. Rewording the check result
# was tried first and moved nothing (build 80, reverted). The three negatives
# below are the load-bearing half — a false "done" is worse than a false flail.

async def test_reverified_green_edit_finishes_instead_of_flailing(tmp_path):
    (tmp_path / "parser.py").write_text("def parse(line)\n    return line\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.permissions.tools["edit_file"] = "auto"
    check = native_call("bash", cmd="python3 -m py_compile parser.py")
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("read_file", path="./parser.py"),  # read-before-edit gate
         native_call("edit_file", path="./parser.py",
                     old="def parse(line)", new="def parse(line):"),
         check,          # green
         check,          # ... and the model asks again instead of finishing
         check],
        FakeBash(""), cfg=cfg)
    out = await loop.run_turn("fix the syntax error in parser.py")
    assert "repeated the same tool call" not in out
    assert "parser.py" in out and "passed" in out


async def test_a_repeated_broken_edit_is_still_a_flail(tmp_path):
    # Condition 1. An earlier green check must not license ending a turn whose
    # edits are now going round in circles — that repeat is a real dead end.
    (tmp_path / "parser.py").write_text("x = 1\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.permissions.tools["edit_file"] = "auto"
    bad = native_call("edit_file", path="./parser.py", old="NOT PRESENT", new="y")
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("edit_file", path="./parser.py", old="x = 1", new="x = 2"),
         native_call("bash", cmd="python3 -m py_compile parser.py"),
         bad, bad, bad],
        FakeBash(""), cfg=cfg)
    out = await loop.run_turn("fix parser.py")
    assert "repeated the same tool call" in out


async def test_a_green_check_with_no_landed_edit_is_still_a_flail(tmp_path):
    # Condition 2. Every edit FAILED, so the check passes only because the file
    # was never touched. There is no work to report and the turn must not claim
    # any — this is the false-completion the gate exists to avoid.
    (tmp_path / "parser.py").write_text("x = 1\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.permissions.tools["edit_file"] = "auto"
    check = native_call("bash", cmd="python3 -m py_compile parser.py")
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("edit_file", path="./parser.py", old="ABSENT", new="y"),
         check, check, check],
        FakeBash(""), cfg=cfg)
    out = await loop.run_turn("fix parser.py")
    assert "repeated the same tool call" in out


async def test_a_verify_that_has_started_failing_is_still_a_flail(tmp_path):
    # Condition 3. The edit landed, but the check is RED and the model is
    # re-running it hoping for a different answer. Latest-wins is the point:
    # "a verify passed at some point this turn" would wrongly finish here.
    (tmp_path / "parser.py").write_text("x = 1\n")
    cfg = Config()
    cfg.agent.max_repeat_calls = 2
    cfg.permissions.tools["edit_file"] = "auto"
    check = native_call("bash", cmd="python3 -m py_compile parser.py")
    loop = make_loop_with_bash(
        tmp_path,
        [native_call("edit_file", path="./parser.py", old="x = 1", new="x = ("),
         check, check, check],
        FakeBash("SyntaxError: unexpected EOF", is_error=True), cfg=cfg)
    out = await loop.run_turn("fix parser.py")
    assert "repeated the same tool call" in out


# --- build 87: a rejected no-op call is lifted back out of history ----------
# The pathology, measured over the 651-run corpus: 16.8% of edit_file calls are
# a byte-identical old==new, 108 of the 137 runs that hit one resent it after
# being told not to, and those runs clean-finish 18% against a 52% baseline.
# Build 80 settled that rewording the rejection doesn't move it, so the lever is
# to stop showing the model a worked example of the call it must not repeat.

def _mk_call(name, **args):
    from locode.tools.base import ToolCall
    return ToolCall(name=name, args=args, source="fenced")


def test_redact_drops_the_fence_but_keeps_the_prose_and_marks_the_attempt():
    call = _mk_call("edit_file", path="a.py", old="x = 1", new="x = 1")
    content = ('I will fix the assignment.\n'
               '```tool\n{"name": "edit_file", "args": {"old": "x = 1"}}\n```')
    out = loop_mod.redact_noop_calls(content, [call], [call])
    assert "```tool" not in out          # nothing left to copy
    assert '"old"' not in out
    assert "I will fix the assignment." in out   # reasoning survives
    assert "edit_file: rejected" in out          # the turn still has a shape


def test_redact_keeps_a_sibling_call_that_actually_landed():
    dud = _mk_call("edit_file", path="a.py", old="x", new="x")
    good = _mk_call("read_file", path="b.py")
    content = ('```tool\n{"name": "edit_file", "args": {}}\n```\n'
               '```tool\n{"name": "read_file", "args": {}}\n```')
    out = loop_mod.redact_noop_calls(content, [dud, good], [dud])
    assert "read_file" in out and "```tool" in out
    assert "edit_file: rejected" in out
    # The surviving fence must be the read, not the rejected edit.
    assert '"name": "read_file"' in out
    assert out.count("```tool") == 1


def test_redact_is_a_no_op_when_nothing_was_rejected():
    call = _mk_call("read_file", path="a.py")
    content = '```tool\n{"name": "read_file", "args": {}}\n```'
    assert loop_mod.redact_noop_calls(content, [call], []) == content


def test_two_same_named_calls_only_redact_the_one_that_no_opped():
    # Identity, not name: dropping the sibling would erase a real action.
    dud = _mk_call("edit_file", path="a.py", old="x", new="x")
    landed = _mk_call("edit_file", path="b.py", old="y", new="z")
    content = ('```tool\n{"name": "edit_file", "args": {"path": "a.py"}}\n```\n'
               '```tool\n{"name": "edit_file", "args": {"path": "b.py"}}\n```')
    out = loop_mod.redact_noop_calls(content, [dud, landed], [dud])
    assert '"path": "b.py"' in out
    assert '"path": "a.py"' not in out


async def test_a_no_op_edit_leaves_no_copyable_call_in_history(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("read_file", path="./a.py"),  # read-before-edit gate
         native_call("edit_file", path="./a.py", old="x = 1", new="x = 1"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("fix a.py")
    assistants = [m for m in loop.history if m.get("kind") == "assistant"]
    redacted = [m for m in assistants if "rejected" in m["content"]]
    assert redacted, "the no-op call was never redacted"
    assert '"old"' not in redacted[0]["content"]
    # And the tool result still tells the model what happened. `old` is in the
    # file here, so build 110 answers "already done" rather than calling the
    # edit malformed — redaction has to keep working on that non-error branch
    # too, or the model copies the dud call straight back out of its own turn.
    joined = "\n".join(m["content"] for m in loop.history)
    assert "ALREADY DONE" in joined


async def test_redaction_is_off_when_the_config_says_so(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    cfg.agent.redact_noop_calls = False
    loop = make_loop(
        tmp_path,
        [native_call("read_file", path="./a.py"),  # read-before-edit gate
         native_call("edit_file", path="./a.py", old="x = 1", new="x = 1"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("fix a.py")
    joined = "\n".join(m["content"] for m in loop.history
                       if m.get("kind") == "assistant")
    assert '"old"' in joined          # the call is still quotable
    assert "rejected —" not in joined


# --- [verify-after-change] the repeat guard vs. the debugging loop -----------
# Measured over 656 eval logs (ROADMAP 5.8): of the runs killed by the repeat
# guard, 80% were repeating `bash` — the model re-running its tests — and 82% of
# consecutive identical bash pairs had a landed edit in between. 140 of 265
# repeat-stop deaths (82% on exec-bugfix) were the model re-verifying a change we
# asked it to make. The streak only reset on a changed result, so two rounds
# whose test output happened not to move were fatal no matter how much real work
# sat between them.

def make_cycling_loop_with_bash(tmp_path, scripted, bash, cfg=None):
    """make_loop_with_bash, but the script CYCLES — the only way to express an
    edit/retest alternation rather than a period-1 stall."""
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    reg.register(UpdatePlan())
    reg.register(bash)
    cfg = cfg or Config()
    cfg.permissions.tools["bash"] = "auto"
    cfg.permissions.tools["append_file"] = "auto"
    return AgentLoop(CyclingClient(scripted), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg, cwd=str(tmp_path))


def _stop_reason(events):
    stops = [e["reason"] for e in events if e.get("phase") == "stopped"]
    return stops[0] if stops else None


async def test_retesting_after_a_real_edit_is_not_a_repeat(tmp_path):
    """Distinct edits that all LAND, each followed by the same test command.

    Before this fix the turn died after the test had run exactly twice, with
    four separate edits sitting in the file — reported to the user as "repeated
    the same tool call without making progress", which was simply false.
    """
    (tmp_path / "app.py").write_text("x = 1\n")
    events = []
    loop = make_cycling_loop_with_bash(
        tmp_path,
        [c for i in range(1, 9) for c in
         (native_call("append_file", path="app.py", content=f"# fix {i}\n"),
          native_call("bash", cmd="pytest -q"))],
        FakeBash("1 failed, 2 passed in 0.10s", is_error=True))
    loop._on_event = events.append
    await loop.run_turn("fix the failing test")

    reason = _stop_reason(events)
    assert reason != "the model repeated the same tool call without making progress"
    # It still stops — but through the net that keys on the ERROR TEXT, which is
    # the honest signal for "your edits aren't changing the failure".
    assert reason == "edits kept hitting the same error without making progress"
    # ...and only after the failure had genuinely recurred, not after two runs.
    runs = [e.get("name") for e in events if e.get("phase") == "run"]
    assert runs.count("bash") >= 3


async def test_retesting_with_nothing_in_between_still_stops(tmp_path):
    """The guard must keep firing on a true no-progress loop: same check, same
    output, no edit between the calls. 47% of the corpus deaths are this, and
    they are correct."""
    events = []
    loop = make_loop_with_bash(
        tmp_path, [native_call("bash", cmd="pytest -q")],
        FakeBash("1 failed in 0.10s", is_error=True))
    loop._on_event = events.append
    await loop.run_turn("run the tests")
    assert _stop_reason(events) is not None
    runs = [e.get("name") for e in events if e.get("phase") == "run"]
    assert len(runs) < 6          # bails quickly, doesn't grind to the cap


async def test_a_repeated_identical_edit_still_counts_as_a_repeat(tmp_path):
    """The reset is restricted to non-mutating batches on purpose. A repeated
    edit is the case the existing `repeated_edit` exception exists for — letting
    it reset itself (it does, after all, land a change every time) would defeat
    the guard exactly where it earns its keep."""
    (tmp_path / "app.py").write_text("x = 1\n")
    events = []
    cfg = Config()
    cfg.permissions.tools["append_file"] = "auto"
    loop = make_loop_with_bash(
        tmp_path, [native_call("append_file", path="app.py", content="# f\n")],
        FakeBash("unused"), cfg=cfg)
    loop._on_event = events.append
    await loop.run_turn("fix app.py")
    assert _stop_reason(events) == ("the model repeated the same tool call "
                                    "without making progress")


# --- read-before-edit gate, loop wiring (build 93) --------------------------


async def test_loop_refuses_an_edit_to_a_file_never_read(tmp_path):
    # The measured failure this gate exists for: the model reconstructs the
    # target from a traceback and edits from memory. Here the `old` even
    # MATCHES — the edit would have landed. It is still refused, because the
    # model had no way to know the text was right.
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("edit_file", path="./a.py", old="x = 1", new="x = 2"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("fix a.py")
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    assert any("have NOT read" in m.get("content", "") for m in loop.history)


async def test_loop_lets_the_edit_through_after_a_read(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("read_file", path="./a.py"),
         native_call("edit_file", path="./a.py", old="x = 1", new="x = 2"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("fix a.py")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    assert not any("have NOT read" in m.get("content", "") for m in loop.history)


async def test_the_gate_can_be_switched_off(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    cfg.agent.require_read_before_edit = False
    loop = make_loop(
        tmp_path,
        [native_call("edit_file", path="./a.py", old="x = 1", new="x = 2"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("fix a.py")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_a_read_in_an_earlier_turn_still_counts(tmp_path):
    # Session-scoped, not turn-scoped: history carries across turns, so the
    # read_file result is still in the model's context in turn 2.
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("read_file", path="./a.py"),
         {"role": "assistant", "content": "read it"},
         native_call("edit_file", path="./a.py", old="x = 1", new="x = 2"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("look at a.py")
    await loop.run_turn("now fix it")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_resetting_context_forgets_what_was_read(tmp_path):
    # The gate's premise is that the file's text is still IN the model's
    # context. Once history is cut down, that is no longer true.
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = Config()
    cfg.permissions.tools["edit_file"] = "auto"
    loop = make_loop(
        tmp_path,
        [native_call("read_file", path="./a.py"),
         {"role": "assistant", "content": "read it"},
         native_call("edit_file", path="./a.py", old="x = 1", new="x = 2"),
         {"role": "assistant", "content": "done"}],
        cfg=cfg)
    await loop.run_turn("look at a.py")
    loop.reset_context()
    await loop.run_turn("now fix it")
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    assert any("have NOT read" in m.get("content", "") for m in loop.history)


# --- a repeated test failure is named in the result (build 101 / 5.24b) ------
# 47 of 84 failing test runs in the b99 sweep were identical to the one before,
# in 16 of 16 runs, and the model's dominant response was to update its plan.
# Archive-wide: 693 repeats across 370 runs, and the identity never once fired
# on a green run or on non-pytest output.

RED = ("[exit 1]\nF....F...\n"
       "FAILED tests/test_a.py::test_wrap - AssertionError\n"
       "E   AssertionError: assert 3 == 4\n")
RED_OTHER = ("[exit 1]\n..F......\n"
             "FAILED tests/test_a.py::test_trunc - AssertionError\n"
             "E   AssertionError: assert 1 == 2\n")


def test_the_same_failure_is_recognised_across_cosmetic_drift():
    # Durations and tmp paths move every run; the failure has not changed.
    a = loop_mod._test_failure_id(RED + "\n1 failed in 0.42s\n")
    b = loop_mod._test_failure_id(RED + "\n1 failed in 0.51s\n")
    assert a == b is not None


def test_a_different_failing_test_is_a_different_identity():
    assert loop_mod._test_failure_id(RED) != loop_mod._test_failure_id(RED_OTHER)


def test_a_green_run_has_no_failure_identity():
    assert loop_mod._test_failure_id("........\n\n8 passed in 0.3s\n") is None


def test_ordinary_shell_output_is_not_a_test_result():
    assert loop_mod._test_failure_id(
        "total 8\ndrwxr-xr-x  3 me  staff  96 file.py\n") is None
    assert loop_mod._test_failure_id("") is None
    assert loop_mod._test_failure_id("...") is None   # a bare ellipsis in prose


def test_an_error_only_run_still_has_an_identity():
    assert loop_mod._test_failure_id(
        "ERROR tests/test_a.py::test_x\nE   ImportError: no module\n") is not None


def test_the_note_escalates_to_a_count_after_the_first():
    first = loop_mod._same_failure_note(1)
    assert "SAME FAILURE as the previous test run" in first
    later = loop_mod._same_failure_note(3)
    assert "4 test runs in a row" in later
    assert len(later) < len(first)      # the paragraph earns its length once


class ScriptedTool:
    """A stand-in for `bash`: returns whichever canned payload it's asked for.

    The real trigger is a pytest run, but the loop only ever sees the result
    string, so scripting it keeps the test hermetic (and off a subprocess)."""
    name = "run_tests"
    description = "run the tests"
    schema = {"type": "object", "properties": {"which": {"type": "string"}}}
    permission = "auto"

    def __init__(self, payloads):
        self.payloads = payloads

    async def run(self, args, ctx):
        from locode.tools.base import ToolResult
        return ToolResult(self.payloads[args["which"]], is_error=True)


def make_loop_with_tests(tmp_path, scripted, payloads, cfg=None):
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    reg.register(UpdatePlan())
    reg.register(ScriptedTool(payloads))
    cfg = cfg or Config()
    cfg.agent.max_repeat_calls = 99   # isolate the same-failure path
    cfg.agent.max_error_stall = 99
    return AgentLoop(FakeClient(scripted), FakeManager(), reg,
                     PermissionPolicy(cfg.permissions), cfg, cwd=str(tmp_path))


def _results(loop):
    return [m["content"] for m in loop.history
            if m["role"] == "user" and m.get("kind") == "tool_result"]


async def test_a_repeat_failure_is_annotated(tmp_path):
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red"),
         native_call("run_tests", which="red"),
         {"role": "assistant", "content": "ok"}],
        {"red": RED})
    await loop.run_turn("fix it")
    res = _results(loop)
    assert "SAME FAILURE" not in res[0]      # the first one is just a failure
    assert "SAME FAILURE as the previous test run" in res[1]


async def test_the_repeat_note_escalates_in_place(tmp_path):
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red")] * 3
        + [{"role": "assistant", "content": "ok"}],
        {"red": RED})
    await loop.run_turn("fix it")
    res = _results(loop)
    assert "SAME FAILURE as the previous test run" in res[1]
    assert "3 test runs in a row" in res[2]


async def test_a_changed_failure_is_not_annotated(tmp_path):
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red"),
         native_call("run_tests", which="other"),
         {"role": "assistant", "content": "ok"}],
        {"red": RED, "other": RED_OTHER})
    await loop.run_turn("fix it")
    assert not any("SAME FAILURE" in r for r in _results(loop))


async def test_a_green_run_between_two_reds_still_reads_as_a_repeat(tmp_path):
    # A green run is not a failure identity, so it neither annotates nor clears
    # the last one — the second red is still the same failure as the first.
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red"),
         native_call("run_tests", which="green"),
         native_call("run_tests", which="red"),
         {"role": "assistant", "content": "ok"}],
        {"red": RED, "green": "....\n4 passed in 0.1s\n"})
    await loop.run_turn("fix it")
    res = _results(loop)
    assert "SAME FAILURE" not in res[1]
    assert "SAME FAILURE" in res[2]


async def test_the_identity_does_not_survive_into_the_next_turn(tmp_path):
    # A new turn is a new question; opening it with a stale accusation would be
    # wrong even when the bytes happen to match.
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red"),
         {"role": "assistant", "content": "ok"}],
        {"red": RED})
    await loop.run_turn("fix it")
    loop._client.n = 0
    await loop.run_turn("now try again")
    assert not any("SAME FAILURE" in r for r in _results(loop))


async def test_the_annotation_does_not_disable_the_repeat_guard(tmp_path):
    # result_sig is computed from the RAW result. If it were taken after the
    # annotation, the running count would make every repeat look novel and the
    # no-progress guard could never fire on the case it exists for.
    cfg = Config()
    cfg.agent.max_repeat_calls = 3
    loop = make_loop_with_tests(
        tmp_path, [native_call("run_tests", which="red")] * 12,
        {"red": RED}, cfg=cfg)
    cfg.agent.max_repeat_calls = 3     # make_loop_with_tests widened it
    cfg.agent.max_iterations = 20
    out = await loop.run_turn("fix it")
    assert "stopped" in out
    assert len(_results(loop)) < 20


# --- naming the test, and making the annotation visible (build 102) ----------
# b101 measured that the note redirects the model off update_plan (20 of 28 → 0
# of 28) but the trajectory showed what it does instead: told to "read the
# failing test", it called read_file on the SOURCE file it had been editing,
# never once on the test. It substituted the nearest thing it had an identifier
# for. A test name is recoverable in 106 of 106 archived repeat events.

HEADER_ONLY = ("[exit 1]\nF....F...\n"
               "=================================== FAILURES ==================\n"
               "________________________ test_wrap_exact_fit __________________\n"
               "E   AssertionError: assert 3 == 4\n")


def test_the_failed_summary_line_is_preferred_for_naming():
    # It carries the FILE too, which is what the model needs in order to open it.
    assert loop_mod._failing_test_names(RED) == ["tests/test_a.py::test_wrap"]


def test_the_failures_banner_is_the_fallback():
    # The short summary is truncated out of most real results; the banner is not.
    assert loop_mod._failing_test_names(HEADER_ONLY) == ["test_wrap_exact_fit"]


def test_no_name_is_recoverable_from_a_bare_progress_line():
    assert loop_mod._failing_test_names("FFF\n") == []


def test_the_note_names_the_test_and_says_it_is_the_test():
    note = loop_mod._same_failure_note(1, ["tests/test_a.py::test_wrap"])
    # Build 103 splits the id: the file is the thing to open, the bare name is
    # the thing to read once it is open.
    assert "Call read_file on `tests/test_a.py` now" in note
    assert "`test_wrap`" in note
    assert "the TEST, not the source file" in note


def test_the_escalated_note_also_names_the_test():
    note = loop_mod._same_failure_note(4, ["test_wrap_exact_fit"])
    assert "`test_wrap_exact_fit`" in note
    assert "5 test runs in a row" in note


def test_many_failing_tests_are_summarised_not_listed():
    out = loop_mod._name_the_tests(["a", "b", "c", "d"])
    assert "`a`, `b`" in out and "(and 2 more)" in out
    assert "`c`" not in out


def test_the_note_degrades_gracefully_with_no_name():
    assert "the failing test" in loop_mod._same_failure_note(1, [])


async def test_the_annotation_emits_an_event(tmp_path):
    # Without this the lever is ungradeable: the `result` event is written per
    # call BEFORE the annotation, so b101's sweep archived zero annotations
    # while 28 of them fired.
    events = []
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red")] * 3
        + [{"role": "assistant", "content": "ok"}],
        {"red": RED})
    loop._on_event = events.append
    await loop.run_turn("fix it")
    fired = [e for e in events
             if e.get("phase") == "nudge" and "same failure" in e.get("reason", "")]
    assert [e["reason"] for e in fired] == [
        "same failure (2 runs in a row)", "same failure (3 runs in a row)"]


async def test_no_event_when_the_failure_changes(tmp_path):
    events = []
    loop = make_loop_with_tests(
        tmp_path,
        [native_call("run_tests", which="red"),
         native_call("run_tests", which="other"),
         {"role": "assistant", "content": "ok"}],
        {"red": RED, "other": RED_OTHER})
    loop._on_event = events.append
    await loop.run_turn("fix it")
    assert not any("same failure" in e.get("reason", "") for e in events)


# --- build 103: the shared file is named once ---------------------------------
# Found by printing what the model actually saw on a live b102 run: every failing
# test was in test_textkit.py and the filename was repeated inside every id, so
# the one actionable token was buried in a 140-character run-on.

def test_a_shared_file_is_named_once():
    note = loop_mod._same_failure_note(1, [
        "test_textkit.py::test_word_wrap", "test_textkit.py::test_truncate"])
    assert "Call read_file on `test_textkit.py` now" in note
    assert "test_textkit.py::" not in note          # not repeated inside the ids
    assert "`test_word_wrap`, `test_truncate`" in note


def test_ids_across_two_files_keep_their_full_form():
    note = loop_mod._same_failure_note(1, [
        "test_a.py::test_x", "test_b.py::test_y"])
    assert "`test_a.py::test_x`" in note


def test_banner_names_without_a_file_still_work():
    note = loop_mod._same_failure_note(1, ["test_wrap_exact_fit"])
    assert "`test_wrap_exact_fit`" in note
    assert "the TEST, not the source file" in note


def test_the_escalated_note_names_the_file():
    note = loop_mod._same_failure_note(3, [
        "test_textkit.py::test_a", "test_textkit.py::test_b"])
    assert "open `test_textkit.py`" in note
    assert "4 test runs in a row" in note


def test_split_returns_no_file_when_ids_are_mixed():
    assert loop_mod._split_test_ids(["a.py::t", "bare"]) == ("", ["a.py::t", "bare"])


# --- build 108 / 5.32: the note must ask for a CALL, not a sentence ----------

def test_the_first_note_demands_a_read_file_call():
    note = loop_mod._same_failure_note(1, ["tests/test_a.py::test_wrap"])
    assert "Call read_file on `tests/test_a.py` now" in note
    assert "the next thing you send must be that read_file call" in note


def test_the_first_note_no_longer_asks_for_a_sentence():
    # 66% of these were answered with prose and no tool call because the note
    # closed by asking for one (b107-indent, 50 events, median 246 chars).
    note = loop_mod._same_failure_note(1, ["tests/test_a.py::test_wrap"])
    assert "say in one sentence" not in note
    assert "in one sentence" not in note


def test_the_call_is_named_before_the_diagnosis():
    note = loop_mod._same_failure_note(1, ["tests/test_a.py::test_wrap"])
    assert note.index("read_file") < note.index("idea behind it")


def test_the_escalated_note_is_unchanged_in_shape():
    # It already converts 82%; build 108 does not touch it.
    note = loop_mod._same_failure_note(3, ["tests/test_a.py::test_wrap"])
    assert "Stop editing and open" in note
