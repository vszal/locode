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
