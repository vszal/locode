"""The REPL /save and /resume handlers round-trip the loop's conversation state."""

import locode.session as session
from locode.config import Config
from locode.model.client import ModelClient
from locode.server.manager import SingleGpuManager
from locode.tools import build_registry
from locode.ui.repl import Repl


def _repl(tmp_path, monkeypatch):
    # Redirect session storage into tmp so tests never touch the real state dir.
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    cfg = Config()
    return Repl(cfg, ModelClient(cfg.base_url), SingleGpuManager(cfg),
                build_registry(cfg))


def test_save_then_resume_roundtrips(tmp_path, monkeypatch):
    r = _repl(tmp_path, monkeypatch)
    r._loop.history.append({"role": "user", "content": "hello"})
    r._loop.history.append({"role": "assistant", "content": "hi there"})
    r._loop.set_model("qwen14")
    snapshot = list(r._loop.history)

    r._slash_save("My Work")
    assert (tmp_path / "sessions" / "my-work.json").exists()

    r._loop.reset_context()                      # wipe to just the system prompt
    assert len(r._loop.history) == 1
    r._slash_resume("My Work")
    assert r._loop.history == snapshot
    assert r._loop.model_alias == "qwen14"


def test_resume_unknown_is_friendly(tmp_path, monkeypatch, capsys):
    r = _repl(tmp_path, monkeypatch)
    r._slash_resume("ghost")
    assert "no saved session" in capsys.readouterr().out


def test_resume_no_arg_lists_saved(tmp_path, monkeypatch, capsys):
    r = _repl(tmp_path, monkeypatch)
    r._slash_save("alpha")
    capsys.readouterr()  # drop the save line
    r._slash_resume("")
    assert "alpha" in capsys.readouterr().out


async def test_ctrl_c_during_a_turn_leaves_the_repl_alive(tmp_path, monkeypatch):
    # The interrupt scope only listens while the model streams, so a Ctrl-C
    # during a tool run or between iterations raises KeyboardInterrupt out of
    # run_turn. It is a BaseException, so `except Exception` never caught it and
    # it took the whole session down. _turn must absorb it and cancel the token.
    r = _repl(tmp_path, monkeypatch)

    async def boom(text):
        raise KeyboardInterrupt

    monkeypatch.setattr(r._loop, "run_turn", boom)
    await r._turn("do something slow")          # must not propagate
    assert r._loop.cancel.cancelled

    # And the next turn still works: run_turn resets the token itself, but the
    # REPL must not have latched anything of its own.
    async def ok(text):
        return "fine"

    monkeypatch.setattr(r._loop, "run_turn", ok)
    await r._turn("again")


# --- M4 visibility wiring: plan checklist, tally, file-change pairing ----------
def _fire(r, events):
    for e in events:
        r._on_event(e)


def test_on_event_renders_live_plan_and_tallies(tmp_path, monkeypatch, capsys):
    from locode.agent.plan import Plan
    r = _repl(tmp_path, monkeypatch)
    r._tally = {"iterations": 0, "tool_calls": 0, "nudges": 0}
    r._files_changed = set()
    r._pending_path = None

    # The loop mutates loop.plan, then emits the update_plan result; the REPL
    # reads the live plan off the loop to render the checklist.
    r._loop.plan = Plan()
    r._loop.plan.replace(["[x] read spec", "[>] write code", "[ ] run tests"])

    _fire(r, [
        {"phase": "iteration", "n": 0},
        {"phase": "run", "name": "update_plan", "args": {"tasks": []}},
        {"phase": "result", "name": "update_plan", "content": "Plan updated"},
        {"phase": "iteration", "n": 1},
        {"phase": "run", "name": "edit_file", "args": {"path": "code.py"}},
        {"phase": "result", "name": "edit_file", "content": "edited", "error": False},
        {"phase": "run", "name": "edit_file", "args": {"path": "broken.py"}},
        {"phase": "result", "name": "edit_file", "content": "no-op", "error": True},
        {"phase": "nudge", "reason": "repeated call"},
    ])
    out = capsys.readouterr().out

    # The plan rendered as a checklist, not a truncated generic tool line.
    assert "▶ write code" in out and "☑ read spec" in out and "☐ run tests" in out
    assert "update_plan {" not in out  # the generic ⚙ line was suppressed
    # Tally reflects the stream.
    assert r._tally["iterations"] == 2
    assert r._tally["tool_calls"] == 3   # update_plan + 2 edits
    assert r._tally["nudges"] == 1
    # Only the successful edit counts as a file changed.
    assert r._files_changed == {"code.py"}


async def test_turn_prints_summary_after_agentic_work(tmp_path, monkeypatch, capsys):
    r = _repl(tmp_path, monkeypatch)

    async def work(text):
        r._on_event({"phase": "iteration", "n": 0})
        r._on_event({"phase": "run", "name": "write_file", "args": {"path": "a.py"}})
        r._on_event({"phase": "result", "name": "write_file",
                     "content": "wrote", "error": False})
        return ""

    monkeypatch.setattr(r._loop, "run_turn", work)
    await r._turn("build it")
    out = capsys.readouterr().out
    assert "↳" in out and "1 file changed" in out and "1 tool call" in out
