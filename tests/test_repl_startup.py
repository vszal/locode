"""Startup honesty: the banner reports the SELECTED model, and the REPL loads it.

`is_up()` only ever answered "does something answer on :8081". Pairing that
answer with the `-m` alias in the banner claimed a model was ready when the
server held different weights entirely, and the real load then happened
silently on the first turn.
"""

import pytest

from locode.config import Config
from locode.model.client import ModelClient
from locode.server.manager import SingleGpuManager
from locode.tools import build_registry
from locode.ui import banner
from locode.ui import repl as repl_mod
from locode.ui.repl import Repl

_ALIASES = {
    "qwen14": "mlx-community/Qwen3-14B-4bit",
    "qwencoder14": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit",
}


def _repl(monkeypatch, *, model="qwen14", **over):
    cfg = Config()
    cfg.aliases.update(_ALIASES)
    cfg.model.default = model
    for k, v in over.items():
        setattr(cfg.server, k, v)
    r = Repl(cfg, ModelClient(cfg.base_url), SingleGpuManager(cfg),
             build_registry(cfg))
    r._color = False  # deterministic output regardless of the test terminal
    return r


def _stub_manager(r, *, resident, ensured=None):
    """Wire the manager so is_up/ensure_up answer from `resident` without I/O."""
    async def fake_is_up(alias=None):
        if alias is None:
            return resident is not None
        return resident == r._manager.resolve(alias) if _resolves(r, alias) else False

    async def fake_ensure_up(alias=None):
        if ensured is not None:
            ensured.append(alias)
        return r._manager.resolve(alias)

    r._manager.is_up = fake_is_up
    r._manager.ensure_up = fake_ensure_up


def _resolves(r, alias):
    try:
        r._manager.resolve(alias)
        return True
    except KeyError:
        return False


# --- banner ------------------------------------------------------------------
def test_banner_separates_model_and_server_state():
    # Server up, but serving something else: the model dot must be hollow even
    # though the server dot is filled.
    out = banner.render("qwen14", True, "/work", "0.1.0", color=False,
                        model_up=False)
    assert "○ qwen14" in out
    assert "● server: up" in out


def test_banner_model_dot_filled_when_loaded():
    out = banner.render("qwen14", True, "/work", "0.1.0", color=False,
                        model_up=True)
    assert "● qwen14" in out and "● server: up" in out


def test_banner_model_up_defaults_to_server_up():
    # --logo talks to nothing and passes one flag; it must keep working.
    assert "● qwen14" in banner.render("qwen14", True, "/w", "0.1.0", color=False)
    assert "○ qwen14" in banner.render("qwen14", False, "/w", "0.1.0", color=False)


def test_banner_says_down_not_starting_when_no_server():
    # "starting…" was a guess; nothing is necessarily starting.
    out = banner.render("qwen14", False, "/w", "0.1.0", color=False)
    assert "server: down" in out


def test_art_and_status_are_separable():
    # The status row is a snapshot of state the preload is about to change, so
    # it has to be printable on its own, after the art.
    art = banner.art(color=False)
    status = banner.status("qwen14", True, "/w", "0.1.0", color=False)
    assert "server:" not in art and "██" in art
    assert "██" not in status and "● server: up" in status


def test_render_still_composes_both():
    # --logo has nothing to wait on and keeps the one-shot form.
    out = banner.render("qwen14", True, "/w", "0.1.0", color=False)
    assert "██" in out and "● server: up" in out
    assert out.endswith(banner.status("qwen14", True, "/w", "0.1.0", color=False))


# --- startup ordering ---------------------------------------------------------
def _no_prompt(monkeypatch):
    """Make the first prompt read EOF, so run() prints startup and returns."""
    class _Session:
        def __init__(self, *a, **k):
            pass

        async def prompt_async(self, *a, **k):
            raise EOFError

    monkeypatch.setattr(repl_mod, "PromptSession", _Session)


async def test_status_row_reflects_the_model_loaded_during_startup(monkeypatch,
                                                                   capsys):
    # The bug: the whole banner printed BEFORE _preload_model, so a model that
    # loaded successfully still sat under "○ qwen14   ○ server: down" — a line
    # printed above the prompt, where nothing can go back and rewrite it.
    r = _repl(monkeypatch)
    _stub_manager(r, resident=None)
    _no_prompt(monkeypatch)

    assert await r.run() == 0

    out = capsys.readouterr().out
    assert "● qwen14   ● server: up" in out
    assert "server: down" not in out
    # ...and the art still came first, so the splash is unchanged.
    assert out.index("██") < out.index("● qwen14")


async def test_startup_says_ready_once(monkeypatch, capsys):
    # The status row says everything "● qwen14 ready" said, with more detail.
    r = _repl(monkeypatch)
    _stub_manager(r, resident=None)
    _no_prompt(monkeypatch)

    await r.run()

    assert "ready" not in capsys.readouterr().out


async def test_status_row_reports_down_when_the_load_fails(monkeypatch, capsys):
    r = _repl(monkeypatch)
    _stub_manager(r, resident=None)
    _no_prompt(monkeypatch)

    async def boom(alias=None):
        raise RuntimeError("refusing to load: it needs ~28.0 GB")

    r._manager.ensure_up = boom

    await r.run()

    out = capsys.readouterr().out
    assert "○ qwen14" in out and "server: down" in out
    assert "needs ~28.0 GB" in out


async def test_no_splash_keeps_the_ready_line(monkeypatch, capsys):
    # With --no-splash there's no status row to carry the news, so the preload
    # has to announce itself.
    r = _repl(monkeypatch)
    _stub_manager(r, resident=None)
    _no_prompt(monkeypatch)

    await r.run(splash=False)

    out = capsys.readouterr().out
    assert "qwen14 ready" in out and "██" not in out


# --- preload -----------------------------------------------------------------
async def test_preload_loads_when_another_model_is_resident(monkeypatch, capsys):
    ensured = []
    r = _repl(monkeypatch)
    _stub_manager(r, resident=_ALIASES["qwencoder14"], ensured=ensured)

    await r._preload_model()

    assert ensured == ["qwen14"]           # actually loaded, not assumed ready
    assert r._model_up and r._server_up
    assert "qwen14 ready" in capsys.readouterr().out


async def test_preload_reports_unknown_alias_without_touching_server(monkeypatch,
                                                                     capsys):
    ensured = []
    r = _repl(monkeypatch, model="ghost")
    _stub_manager(r, resident=None, ensured=ensured)

    await r._preload_model()

    assert ensured == []                   # a typo must not start anything
    assert not r._model_up
    assert "ghost" in capsys.readouterr().out


async def test_preload_skipped_when_down_and_autostart_off(monkeypatch):
    ensured = []
    r = _repl(monkeypatch, auto_start=False)
    _stub_manager(r, resident=None, ensured=ensured)
    r._server_up = False

    await r._preload_model()

    assert ensured == []                   # "don't launch a server for me"
    assert not r._model_up


async def test_preload_still_switches_a_running_server_with_autostart_off(
        monkeypatch):
    # auto_start governs launching a process, not switching one already running.
    ensured = []
    r = _repl(monkeypatch, auto_start=False)
    _stub_manager(r, resident=_ALIASES["qwencoder14"], ensured=ensured)
    r._server_up = True

    await r._preload_model()

    assert ensured == ["qwen14"]


async def test_preload_failure_leaves_repl_usable(monkeypatch, capsys):
    r = _repl(monkeypatch)
    _stub_manager(r, resident=None)

    async def boom(alias=None):
        raise RuntimeError("refusing to load: it needs ~28.0 GB")

    r._manager.ensure_up = boom

    await r._preload_model()               # must not propagate

    assert not r._model_up
    assert "28.0 GB" in capsys.readouterr().out


# --- toolbar -----------------------------------------------------------------
@pytest.mark.parametrize("model_up,server_up,expect", [
    (True, True, "● up"),
    (False, True, "◐ other model"),
    (False, False, "○ down"),
])
def test_toolbar_distinguishes_model_from_server(monkeypatch, model_up, server_up,
                                                 expect):
    r = _repl(monkeypatch)
    r._model_up, r._server_up = model_up, server_up
    assert expect in r._toolbar()
