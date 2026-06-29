import json
import signal

import pytest

import locode.server.manager as mod
from locode.config import Config
from locode.model.profiles import profile_for
from locode.server.manager import GB, SingleGpuManager, build_launch_argv, memory_fits

# Aliases now come from config, not a shipped table — model the user's config.
_ALIASES = {
    "qwen14": "mlx-community/Qwen3-14B-4bit",
    "qwencoder14": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit",
    "qwen4i": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    "gemma27": "mlx-community/gemma-3-text-27b-it-4bit",
}


def _cfg(**over):
    c = Config()
    c.aliases.update(_ALIASES)
    for k, v in over.items():
        setattr(c.server, k, v)
    return c


def _mgr(**over):
    return SingleGpuManager(_cfg(**over))


def argv_for(alias):
    mid = _ALIASES[alias]
    return build_launch_argv("/bin/mlx", mid, "127.0.0.1", 8081, profile_for(mid))


def test_launch_argv_qwen14_has_thinking_and_default_cache():
    argv = argv_for("qwen14")
    assert argv[:6] == ["/bin/mlx", "--model", "mlx-community/Qwen3-14B-4bit",
                        "--host", "127.0.0.1", "--port"]
    assert "--chat-template-args" in argv
    j = argv[argv.index("--chat-template-args") + 1]
    assert json.loads(j) == {"enable_thinking": False}
    assert "1610612736" in argv  # 1.5GB


def test_launch_argv_qwencoder_no_thinking():
    argv = argv_for("qwencoder14")
    assert "--chat-template-args" not in argv  # not a thinking model


def test_launch_argv_gemma27_tight_cache():
    argv = argv_for("gemma27")
    assert "1073741824" in argv  # 1GB tight budget


def _thinking_kwarg(argv):
    if "--chat-template-args" not in argv:
        return "omitted"
    return json.loads(argv[argv.index("--chat-template-args") + 1])["enable_thinking"]


def test_launch_argv_thinking_override_forces_on():
    # A model whose profile suppresses thinking, overridden back ON via config.
    mid = "mlx-community/Qwen3-14B-4bit"  # profile thinking_arg=True -> sends false
    argv = build_launch_argv("/bin/mlx", mid, "127.0.0.1", 8081,
                             profile_for(mid), thinking=True)
    assert _thinking_kwarg(argv) is True


def test_launch_argv_thinking_override_omits():
    # None means omit the kwarg entirely (template default), even when the
    # profile would have sent enable_thinking=false.
    mid = "mlx-community/Qwen3-14B-4bit"
    argv = build_launch_argv("/bin/mlx", mid, "127.0.0.1", 8081,
                             profile_for(mid), thinking=None)
    assert _thinking_kwarg(argv) == "omitted"


def test_resolve_uses_config_aliases():
    cfg = _cfg()
    cfg.aliases["mymodel"] = "org/Custom-4bit"
    m = SingleGpuManager(cfg)
    assert m.resolve("mymodel") == "org/Custom-4bit"           # config alias
    assert m.resolve("qwen14") == "mlx-community/Qwen3-14B-4bit"
    assert m.resolve("org/Foo-4bit") == "org/Foo-4bit"          # full-id passthrough
    assert set(_ALIASES) <= set(m.known_aliases())


def test_resolve_unknown_alias_raises_pointing_to_config():
    with pytest.raises(KeyError) as exc:
        _mgr().resolve("definitely-not-a-model")
    msg = str(exc.value)
    assert "definitely-not-a-model" in msg
    assert "full org/model id" in msg


async def test_ensure_up_uses_running_server(monkeypatch):
    m = _mgr()

    async def fake_served():
        # /v1/models lists the whole HF cache, not just the resident model.
        return ["mlx-community/Qwen3-14B-4bit", "mlx-community/Qwen3-0.6B-4bit"]

    started = []
    monkeypatch.setattr(m, "list_served", fake_served)
    monkeypatch.setattr(m, "_resident_model", lambda: "mlx-community/Qwen3-14B-4bit")
    monkeypatch.setattr(m, "start", lambda a: started.append(a))
    out = await m.ensure_up("qwen14")
    assert out == "mlx-community/Qwen3-14B-4bit"
    assert started == []  # resident == target -> did not restart


async def test_ensure_up_switches_when_requested_model_not_resident(monkeypatch):
    # Regression for the silent-fallback bug: a requested model that is merely
    # *cached* (so it shows up in /v1/models) but is NOT resident must trigger a
    # real switch — not be silently served by whatever is loaded.
    m = _mgr()

    async def fake_served():
        return ["mlx-community/Qwen3-14B-4bit",
                "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"]  # both cached

    switched = []

    async def fake_switch(alias):
        switched.append(alias)
        return m.resolve(alias)

    monkeypatch.setattr(m, "list_served", fake_served)
    monkeypatch.setattr(m, "_resident_model", lambda: "mlx-community/Qwen3-14B-4bit")
    monkeypatch.setattr(m, "switch", fake_switch)
    out = await m.ensure_up("qwencoder14")
    assert switched == ["qwencoder14"]
    assert out == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"


async def test_ensure_up_starts_when_down(monkeypatch):
    m = _mgr()

    async def fake_served():
        return []

    async def fake_start(alias):
        return m.resolve(alias)

    monkeypatch.setattr(m, "list_served", fake_served)
    monkeypatch.setattr(m, "start", fake_start)
    out = await m.ensure_up("qwencoder14")
    assert out == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"


async def test_switch_bad_alias_does_not_stop_server(monkeypatch):
    # Regression: a typo'd model must fail fast WITHOUT killing a running server.
    m = _mgr()
    stopped = []
    monkeypatch.setattr(m, "stop", lambda: stopped.append(True))
    with pytest.raises(KeyError):
        await m.switch("qwen14-typo")
    assert stopped == []  # server untouched


async def test_switch_already_served_skips_restart(monkeypatch):
    m = _mgr()
    stopped = []

    monkeypatch.setattr(m, "_resident_model", lambda: "mlx-community/Qwen3-14B-4bit")
    monkeypatch.setattr(m, "stop", lambda: stopped.append(True))
    out = await m.switch("qwen14")
    assert out == "mlx-community/Qwen3-14B-4bit"
    assert stopped == []  # already resident -> no destructive restart


async def test_switch_reloads_when_target_cached_but_not_resident(monkeypatch):
    # Regression: switching to a model that is in the HF cache but NOT resident
    # must stop+start, not skip because it appears in the /v1/models list.
    m = _mgr()
    stopped, started = [], []

    async def fake_stop():
        stopped.append(True)

    async def fake_start(alias):
        started.append(alias)
        return m.resolve(alias)

    monkeypatch.setattr(m, "_resident_model", lambda: "mlx-community/Qwen3-14B-4bit")
    monkeypatch.setattr(m, "stop", fake_stop)
    monkeypatch.setattr(m, "start", fake_start)
    out = await m.switch("qwencoder14")
    assert stopped == [True] and started == ["qwencoder14"]
    assert out == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"


async def test_ensure_up_respects_autostart_off(monkeypatch):
    cfg = Config()
    cfg.server.auto_start = False
    m = SingleGpuManager(cfg)

    async def fake_served():
        return []

    monkeypatch.setattr(m, "list_served", fake_served)
    with pytest.raises(RuntimeError, match="auto_start is off"):
        await m.ensure_up()


# --- remote / unmanaged endpoint ----------------------------------------------
def _remote_manager(monkeypatch, served):
    cfg = _cfg(base_url="https://gpu-box:8081")   # non-loopback -> unmanaged
    m = SingleGpuManager(cfg)

    async def fake_served():
        return served

    monkeypatch.setattr(m, "list_served", fake_served)
    return m


async def test_remote_ensure_up_uses_running_server(monkeypatch):
    m = _remote_manager(monkeypatch, ["org/Some-Model"])
    assert m._managed is False
    assert await m.ensure_up() == "org/Some-Model"   # routes to whatever it serves


async def test_remote_ensure_up_does_not_launch(monkeypatch):
    m = _remote_manager(monkeypatch, [])
    with pytest.raises(RuntimeError, match="remote/unmanaged"):
        await m.ensure_up()


async def test_remote_start_refuses(monkeypatch):
    m = _remote_manager(monkeypatch, [])
    with pytest.raises(RuntimeError, match="refusing to launch"):
        await m.start("qwen14")


async def test_remote_stop_is_noop_no_pkill(monkeypatch):
    m = _remote_manager(monkeypatch, [])
    called = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(a))
    await m.stop()
    assert called == []   # must NOT pkill local mlx servers when remote


async def test_remote_switch_routes_if_served_else_refuses(monkeypatch):
    m = _remote_manager(monkeypatch, ["mlx-community/Qwen3-14B-4bit"])
    assert await m.switch("qwen14") == "mlx-community/Qwen3-14B-4bit"  # already served
    m2 = _remote_manager(monkeypatch, ["org/Other"])
    with pytest.raises(RuntimeError, match="cannot switch models on remote"):
        await m2.switch("qwen14")


# --- SIGTERM -> SIGKILL escalation -------------------------------------------
async def test_terminate_escalates_to_sigkill_when_term_ignored(monkeypatch):
    # A wedged server ignores SIGTERM (uninterruptible sleep); stop() must escalate.
    m = _mgr()
    m._TERM_WAIT = 0.0     # don't linger on the ignored SIGTERM
    m._KILL_WAIT = 1.0
    sent, alive = [], {"v": True}

    def signal_servers(sig):
        sent.append(sig)
        if sig == signal.SIGKILL:
            alive["v"] = False    # only SIGKILL frees the wedged process

    monkeypatch.setattr(m, "_signal_servers", signal_servers)
    monkeypatch.setattr(mod, "_server_pids", lambda: [4242] if alive["v"] else [])
    await m._terminate_servers()
    assert sent == [signal.SIGTERM, signal.SIGKILL]


async def test_terminate_no_sigkill_when_sigterm_works(monkeypatch):
    m = _mgr()
    m._TERM_WAIT = 1.0
    sent, alive = [], {"v": True}

    def signal_servers(sig):
        sent.append(sig)
        if sig == signal.SIGTERM:
            alive["v"] = False    # exits gracefully

    monkeypatch.setattr(m, "_signal_servers", signal_servers)
    monkeypatch.setattr(mod, "_server_pids", lambda: [1] if alive["v"] else [])
    await m._terminate_servers()
    assert sent == [signal.SIGTERM]   # no needless SIGKILL


# --- _wait_up detects a failed launch ----------------------------------------
async def test_wait_up_raises_when_launched_process_exits():
    # If the new server process dies (e.g. can't bind the port), don't false-
    # positive on a stale server's /v1/models — raise a clear startup error.
    m = _mgr()

    class Dead:
        returncode = 1
        def poll(self):
            return 1

    m._proc = Dead()
    with pytest.raises(RuntimeError, match="exited during startup"):
        await m._wait_up("anything", secs=4)


# --- resident-model detection (reads the server's --model arg, not /v1/models) -
def test_resident_model_id_parses_ps(monkeypatch):
    class R:
        stdout = (
            "/usr/bin/python /opt/homebrew/bin/mlx_lm.server --model org/My-Model"
            " --host 127.0.0.1 --port 8081 --max-tokens 4096\n"
            "/bin/zsh -l\n"
        )

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    assert mod._resident_model_id() == "org/My-Model"


def test_resident_model_id_none_when_no_server(monkeypatch):
    class R:
        stdout = "/bin/zsh\n/usr/bin/vim file.py\n"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    assert mod._resident_model_id() is None


def test_resident_model_id_none_on_ps_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod._resident_model_id() is None


# --- preflight memory guard ---------------------------------------------------
def test_memory_fits_pure():
    ok, _need, budget = memory_fits(13 * GB, 1 * GB, 24 * GB, 5 * GB)
    assert ok and budget == 19 * GB
    ok2, need2, _ = memory_fits(17 * GB, 1 * GB, 24 * GB, 5 * GB)
    assert not ok2 and need2 > 19 * GB   # 17×1.15 + 1 ≈ 20.6 GB


def test_memory_budget_refuses_when_too_big(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 17 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    big = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
    with pytest.raises(RuntimeError, match="refusing to load"):
        m._check_memory_budget(big, profile_for(big))


def test_memory_budget_allows_when_fits(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 13 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    dev = "lmstudio-community/Devstral-Small-2507-MLX-4bit"
    m._check_memory_budget(dev, profile_for(dev))   # must not raise


def test_memory_budget_disabled_with_zero_reserve(monkeypatch):
    m = _mgr(memory_reserve_gb=0)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 999 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    m._check_memory_budget("org/huge", profile_for("org/huge"))   # guard off -> ok


def test_memory_budget_skips_when_uncached(monkeypatch):
    # Can't estimate a model we haven't downloaded -> skip rather than block.
    m = _mgr(memory_reserve_gb=5)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: None)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    m._check_memory_budget("org/uncached", profile_for("org/uncached"))   # no raise
