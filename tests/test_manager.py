import json
import signal
import types

import pytest

import locode.server.manager as mod
from locode.config import Config
from locode.model.profiles import profile_for
from locode.server.manager import (
    GB,
    SingleGpuManager,
    build_launch_argv,
    context_tokens_for,
    kv_cache_bytes,
    memory_fits,
)

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
    # Nothing resident, so _term_wait() stays at the base and this test can't
    # pick up a real running server's size-scaled grace period.
    monkeypatch.setattr(mod, "_resident_model_id", lambda: None)
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
    monkeypatch.setattr(mod, "_resident_model_id", lambda: None)
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
def _no_kv(monkeypatch):
    """Neutralise the KV estimate so a test can exercise the older arithmetic
    in isolation. Also keeps these tests off the machine's real HF cache."""
    monkeypatch.setattr(mod, "_model_config", lambda mid: None)


def test_memory_fits_pure():
    ok, _need, budget = memory_fits(13 * GB, 1 * GB, 24 * GB, 5 * GB)
    assert ok and budget == 19 * GB
    ok2, need2, _ = memory_fits(17 * GB, 1 * GB, 24 * GB, 5 * GB)
    assert not ok2 and need2 > 19 * GB   # 17×1.15 + 1 ≈ 20.6 GB


def test_memory_fits_uses_the_tighter_of_ram_and_wired_cap():
    # 19 GB of headroom by RAM, but the GPU may only wire 16 GB.
    # 14×1.15 + 1 = 17.1 GB: fits the RAM budget, exceeds the wired cap.
    ok, need, budget = memory_fits(14 * GB, 1 * GB, 24 * GB, 5 * GB,
                                   wired_limit=16 * GB)
    assert not ok and budget == 16 * GB and need > 16 * GB
    # Same model, no wired cap known -> the RAM budget alone lets it through.
    assert memory_fits(14 * GB, 1 * GB, 24 * GB, 5 * GB)[0]


def test_memory_fits_ignores_a_wired_cap_that_is_not_binding():
    ok, _need, budget = memory_fits(13 * GB, 1 * GB, 24 * GB, 5 * GB,
                                    wired_limit=22 * GB)
    assert ok and budget == 19 * GB


def test_memory_budget_refuses_when_too_big(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 17 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    big = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
    with pytest.raises(RuntimeError, match="refusing to load"):
        m._check_memory_budget(big, profile_for(big))


def test_memory_budget_allows_when_fits(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 9 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: 18 * GB)
    ok = "sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx"
    m._check_memory_budget(ok, profile_for(ok))   # must not raise


def test_wired_cap_blocks_a_model_that_free_ram_would_allow(monkeypatch):
    # With a small reserve the RAM budget (24 − 2 = 22 GB) would admit devstral,
    # but the raised wired cap of 20000 MB = 19.5 GB is the real ceiling. Before
    # the fix that sysctl was never read and this load was allowed — twice, and
    # both times it panicked the GPU driver.
    m = _mgr(memory_reserve_gb=2)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 14 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: 20000 * 1024 * 1024)
    # Devstral's real shape, stated here rather than read from the HF cache:
    # the model can be (and now has been) deleted from disk, and a guard test
    # must not depend on which weights happen to be downloaded.
    monkeypatch.setattr(mod, "_model_config", lambda mid: _DEVSTRAL_CFG)
    dev = "mlx-community/Devstral-Small-2-24B-Instruct-2512-4bit"
    with pytest.raises(RuntimeError, match="wired-memory cap"):
        m._check_memory_budget(dev, profile_for(dev))
    # Same inputs, default wired cap unknown -> only the RAM ceiling applies,
    # and the KV estimate is what has to stop it.
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    with pytest.raises(RuntimeError, match="KV cache"):
        m._check_memory_budget(dev, profile_for(dev))


def test_the_error_names_the_binding_ceiling(monkeypatch):
    # Lowering memory_reserve_gb cannot help when the wired cap is what binds,
    # so the message must not suggest it.
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 15 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: 16 * GB)
    with pytest.raises(RuntimeError) as ei:
        m._check_memory_budget("org/m", profile_for("org/m"))
    assert "wired-memory cap" in str(ei.value)
    assert "memory_reserve_gb" not in str(ei.value)


def test_memory_budget_disabled_with_zero_reserve(monkeypatch):
    m = _mgr(memory_reserve_gb=0)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 999 * GB)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    m._check_memory_budget("org/huge", profile_for("org/huge"))   # guard off -> ok


def test_refusal_suggests_a_model_that_fits(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    # The refused model is huge; qwen4i is small enough to be a real option.
    sizes = {"mlx-community/Qwen3-4B-Instruct-2507-4bit": 3 * GB}
    monkeypatch.setattr(mod, "_model_disk_bytes",
                        lambda mid: sizes.get(mid, 17 * GB))
    with pytest.raises(RuntimeError) as ei:
        m._check_memory_budget("org/huge", profile_for("org/huge"))
    assert "qwen4i fits" in str(ei.value) and "/model qwen4i" in str(ei.value)


def test_refusal_prefers_the_configured_default_when_it_fits(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    m._cfg.model.default = "qwen4i"
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    # qwen14 is larger and would win "largest that fits", but the default is
    # the one chosen on merit, so it must be suggested instead.
    sizes = {"mlx-community/Qwen3-4B-Instruct-2507-4bit": 3 * GB,
             "mlx-community/Qwen3-14B-4bit": 8 * GB}
    monkeypatch.setattr(mod, "_model_disk_bytes",
                        lambda mid: sizes.get(mid, 17 * GB))
    with pytest.raises(RuntimeError) as ei:
        m._check_memory_budget("org/huge", profile_for("org/huge"))
    assert "qwen4i fits" in str(ei.value)


def test_refusal_is_silent_when_nothing_fits(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 17 * GB)
    with pytest.raises(RuntimeError) as ei:
        m._check_memory_budget("org/huge", profile_for("org/huge"))
    assert "fits" not in str(ei.value)


def test_refusal_survives_a_failing_suggestion_scan(monkeypatch):
    # The suggestion is a courtesy; it must never replace the refusal.
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 17 * GB)

    def boom(*a, **k):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(m, "_fitting_aliases", boom)
    with pytest.raises(RuntimeError, match="refusing to load"):
        m._check_memory_budget("org/huge", profile_for("org/huge"))


def test_refused_model_is_never_suggested_to_itself(monkeypatch):
    m = _mgr(memory_reserve_gb=5)
    _no_kv(monkeypatch)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    monkeypatch.setattr(mod, "_wired_limit_bytes", lambda total: None)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 1 * GB)
    dev = "mlx-community/Qwen3-14B-4bit"
    fits = m._fitting_aliases(19 * GB, exclude_id=dev)
    assert dev not in [m.resolve(a) for a, _ in fits]


def test_memory_budget_skips_when_uncached(monkeypatch):
    # Can't estimate a model we haven't downloaded -> skip rather than block.
    m = _mgr(memory_reserve_gb=5)
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: None)
    monkeypatch.setattr(mod, "_total_ram_bytes", lambda: 24 * GB)
    m._check_memory_budget("org/uncached", profile_for("org/uncached"))   # no raise


# --- KV cache estimate --------------------------------------------------------
# Real shapes, read from the cached config.json of each model.
_DEVSTRAL_CFG = {"num_hidden_layers": 40, "num_key_value_heads": 8,
                 "head_dim": 128, "num_attention_heads": 32,
                 "hidden_size": 5120}
# Gemma-3: 40 of 48 layers run on a 1024-token window.
_GEMMA_CFG = {"num_hidden_layers": 48, "num_key_value_heads": 8,
              "head_dim": 256, "sliding_window": 1024,
              "layer_types": ["sliding_attention"] * 40 + ["full_attention"] * 8}
# Qwythos: hybrid, 24 linear-attention layers hold no context-scaled cache.
_QYTHOS_CFG = {"num_hidden_layers": 32, "num_key_value_heads": 4,
               "head_dim": 256,
               "layer_types": ["linear_attention"] * 24 + ["full_attention"] * 8}


def test_kv_cache_matches_the_hand_calculation():
    # 2 (K+V) × 40 layers × 8 kv heads × 128 dim × 2 bytes = 160 KB/token.
    assert kv_cache_bytes(_DEVSTRAL_CFG, 1) == 160 * 1024
    assert kv_cache_bytes(_DEVSTRAL_CFG, 1000) == 160 * 1024 * 1000


def test_kv_cache_derives_head_dim_when_absent():
    cfg = dict(_DEVSTRAL_CFG)
    del cfg["head_dim"]          # 5120 / 32 = 160
    assert kv_cache_bytes(cfg, 1) == 2 * 40 * 8 * 160 * 2


def test_kv_cache_prefers_the_nested_text_config():
    # A VL-derived text model: the wrapper's top level has the vision shape.
    wrapped = {"num_hidden_layers": 2, "num_key_value_heads": 1, "head_dim": 8,
               "text_config": _DEVSTRAL_CFG}
    assert kv_cache_bytes(wrapped, 1) == 160 * 1024


def test_sliding_layers_are_capped_at_the_window():
    # The overshoot that would have blocked gemma: at 40k tokens only the 8 full
    # layers scale; the 40 sliding ones stay pinned at 1024.
    tokens, per = 40_000, 2 * 8 * 256 * 2
    expected = (8 * tokens + 40 * 1024) * per
    assert kv_cache_bytes(_GEMMA_CFG, tokens) == expected
    # ...and that is far below the flat all-layers-full figure.
    assert expected < 48 * tokens * per / 4


def test_sliding_window_ignored_when_the_model_disables_it():
    # Qwen2.5 ships sliding_window=131072 with use_sliding_window=False.
    cfg = dict(_DEVSTRAL_CFG, sliding_window=1024, use_sliding_window=False)
    assert kv_cache_bytes(cfg, 5_000) == kv_cache_bytes(_DEVSTRAL_CFG, 5_000)


def test_linear_attention_layers_hold_no_context_scaled_cache():
    tokens, per = 40_000, 2 * 4 * 256 * 2
    assert kv_cache_bytes(_QYTHOS_CFG, tokens) == 8 * tokens * per


def test_a_global_window_applies_without_layer_types():
    cfg = dict(_DEVSTRAL_CFG, sliding_window=2048)
    per = 2 * 8 * 128 * 2
    assert kv_cache_bytes(cfg, 40_000) == 40 * 2048 * per


@pytest.mark.parametrize("cfg", [
    None, {}, "not a dict", {"num_hidden_layers": 40},
    {"num_hidden_layers": 0, "num_key_value_heads": 8, "head_dim": 128},
])
def test_kv_cache_returns_none_on_unusable_config(cfg):
    assert kv_cache_bytes(cfg, 1000) is None


def test_kv_cache_returns_none_for_a_nonpositive_context():
    assert kv_cache_bytes(_DEVSTRAL_CFG, 0) is None


def test_cache_estimate_never_undercuts_the_profile_figure(monkeypatch):
    # A tiny model's computed KV is below the profile's flat budget; keep the
    # larger of the two rather than shrinking the reservation.
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config",
                        lambda mid: {"num_hidden_layers": 1,
                                     "num_key_value_heads": 1, "head_dim": 8})
    p = profile_for("org/tiny")
    assert m._cache_bytes("org/tiny", p) == p.prompt_cache_bytes


def test_cache_estimate_scales_with_the_history_budget(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config", lambda mid: _DEVSTRAL_CFG)
    p = profile_for("mlx-community/Devstral-Small-2-24B-Instruct-2512-4bit")
    m._cfg.agent.max_history_chars = 100_000
    big = m._cache_bytes("x", p)
    m._cfg.agent.max_history_chars = 30_000
    assert m._cache_bytes("x", p) < big


def test_cache_estimate_falls_back_when_config_unreadable(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config", lambda mid: None)
    p = profile_for("org/unknown")
    assert m._cache_bytes("org/unknown", p) == p.prompt_cache_bytes


def test_context_tokens_covers_history_plus_one_generation():
    c = _cfg()
    c.agent.max_history_chars = 90_000
    c.model.max_tokens = 8192
    assert context_tokens_for(c) == 30_000 + 8192


# --- wired-limit reader -------------------------------------------------------
def test_wired_limit_reads_the_sysctl(monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _Ran("20000\n"))
    assert mod._wired_limit_bytes(24 * GB) == 20000 * 1024 * 1024


def test_wired_limit_zero_means_the_kernel_default(monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Ran("0\n"))
    assert mod._wired_limit_bytes(24 * GB) == int(24 * GB * 0.75)


def test_wired_limit_is_none_off_darwin(monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    assert mod._wired_limit_bytes(24 * GB) is None


def test_wired_limit_survives_a_missing_sysctl(monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")

    def boom(*a, **k):
        raise OSError("no sysctl")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod._wired_limit_bytes(24 * GB) is None


class _Ran:
    def __init__(self, stdout):
        self.stdout = stdout


# --- SIGTERM grace scales with model size ------------------------------------
def test_term_wait_scales_with_weights():
    # 14 GB of wired buffers cannot be unwired in the flat 6 s.
    assert mod.term_wait_for(14 * GB, base=6.0) == 6.0 + 2.0 * 14


def test_term_wait_is_capped():
    assert mod.term_wait_for(500 * GB, base=6.0) == mod._TERM_WAIT_MAX


@pytest.mark.parametrize("size", [None, 0])
def test_term_wait_falls_back_to_base_when_size_unknown(size):
    assert mod.term_wait_for(size, base=6.0) == 6.0


def test_term_wait_uses_the_resident_model(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_resident_model_id", lambda: "org/big")
    monkeypatch.setattr(mod, "_model_disk_bytes", lambda mid: 10 * GB)
    assert m._term_wait() == 6.0 + 2.0 * 10


def test_term_wait_is_base_when_nothing_is_resident(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_resident_model_id", lambda: None)
    assert m._term_wait() == m._TERM_WAIT


# --- is_up(alias): is the SELECTED model loaded, not just "a server answers" ---
def _served_mgr(monkeypatch, served, resident, **over):
    m = _mgr(**over)

    async def fake_served():
        return served

    monkeypatch.setattr(m, "list_served", fake_served)
    monkeypatch.setattr(m, "_resident_model", lambda: resident)
    return m


async def test_is_up_alias_false_when_a_different_model_is_resident(monkeypatch):
    # The reported bug: `-m qwen14` against a server holding qwencoder14 said
    # "up". /v1/models lists the whole HF cache, so the target appears in the
    # served list without being loaded.
    m = _served_mgr(monkeypatch,
                    served=["mlx-community/Qwen3-14B-4bit",
                            "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"],
                    resident="mlx-community/Qwen2.5-Coder-14B-Instruct-4bit")
    assert await m.is_up("qwen14") is False
    assert await m.is_up("qwencoder14") is True


async def test_is_up_alias_true_when_resident(monkeypatch):
    m = _served_mgr(monkeypatch, served=["mlx-community/Qwen3-14B-4bit"],
                    resident="mlx-community/Qwen3-14B-4bit")
    assert await m.is_up("qwen14") is True


async def test_is_up_alias_false_when_nothing_served(monkeypatch):
    m = _served_mgr(monkeypatch, served=[], resident=None)
    assert await m.is_up("qwen14") is False


async def test_is_up_alias_accepts_full_id(monkeypatch):
    m = _served_mgr(monkeypatch, served=["mlx-community/Qwen3-14B-4bit"],
                    resident="mlx-community/Qwen3-14B-4bit")
    assert await m.is_up("mlx-community/Qwen3-14B-4bit") is True


async def test_is_up_unknown_alias_is_not_up(monkeypatch):
    # An alias that doesn't resolve can't be loaded; report that instead of
    # raising, and let the caller surface the naming error.
    m = _served_mgr(monkeypatch, served=["mlx-community/Qwen3-14B-4bit"],
                    resident="mlx-community/Qwen3-14B-4bit")
    assert await m.is_up("nope") is False


async def test_remote_is_up_alias_trusts_served_list(monkeypatch):
    # No process to introspect on a remote box, so the served list is all we have.
    m = _remote_manager(monkeypatch, ["mlx-community/Qwen3-14B-4bit"])
    assert await m.is_up("qwen14") is True
    assert await m.is_up("qwen4i") is False


# --- the unsupported-architecture preflight --------------------------------
# Regression cover for 2026-08-02: an unpatched gemma-4-12b-coder-8bit declared
# model_type "gemma4_unified", mlx_lm raised inside its generate THREAD, the
# request was never answered, and locode span on the spinner for a whole turn.
# /v1/models answered 200 throughout, so nothing looked wrong from outside.

def test_interpreter_comes_from_the_launcher_shebang(tmp_path):
    launcher = tmp_path / "mlx_lm.server"
    launcher.write_bytes(b"#!/usr/bin/python3.11\nprint('hi')\n")
    assert mod._mlx_interpreter(str(launcher)) in (None, "/usr/bin/python3.11")


def test_interpreter_is_none_when_the_shebang_is_missing(tmp_path):
    launcher = tmp_path / "mlx_lm.server"
    launcher.write_bytes(b"not a script\n")
    assert mod._mlx_interpreter(str(launcher)) is None


def test_interpreter_is_none_when_the_launcher_is_absent(tmp_path):
    assert mod._mlx_interpreter(str(tmp_path / "nope")) is None


def test_the_probe_runs_for_real_and_declines_when_mlx_lm_is_absent(tmp_path):
    """End-to-end through a real shebang and a real subprocess.

    locode's own venv has no mlx_lm, so the honest answer here is None — the
    probe must reach exit 4 (find_spec raised on the parent package) and NOT
    mistake a missing mlx_lm for an unsupported architecture.
    """
    import sys
    launcher = tmp_path / "mlx_lm.server"
    launcher.write_bytes(f"#!{sys.executable}\n".encode())
    assert mod._mlx_interpreter(str(launcher)) == sys.executable
    assert mod.arch_supported("gemma4", str(launcher)) is None


@pytest.mark.parametrize("rc, expected", [
    (0, True),      # loader exists
    (3, False),     # no such loader — the only refusal
    (4, None),      # mlx_lm absent from that interpreter
    (1, None),      # probe crashed
    (2, None),      # anything else
])
def test_probe_exit_codes_map_to_the_tri_state(monkeypatch, rc, expected):
    monkeypatch.setattr(mod, "_mlx_interpreter", lambda b: "/bin/interp")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=rc))
    assert mod.arch_supported("gemma4_unified", "/bin/mlx") is expected


def test_a_probe_that_times_out_is_not_a_refusal(monkeypatch):
    monkeypatch.setattr(mod, "_mlx_interpreter", lambda b: "/bin/interp")
    def _slow(*a, **k):
        raise mod.subprocess.TimeoutExpired("interp", 15)
    monkeypatch.setattr(mod.subprocess, "run", _slow)
    assert mod.arch_supported("gemma4", "/bin/mlx") is None


def test_unknown_probe_result_never_blocks(monkeypatch):
    """Any doubt must fall open — refusing a working model is the worse bug."""
    monkeypatch.setattr(mod, "_mlx_interpreter", lambda b: None)
    assert mod.arch_supported("gemma4_unified", "/bin/mlx_lm.server") is None
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config",
                        lambda mid: {"model_type": "gemma4_unified"})
    m._check_arch_supported("org/whatever")  # must not raise


def test_a_probe_that_crashes_is_not_a_refusal(monkeypatch):
    monkeypatch.setattr(mod, "_mlx_interpreter", lambda b: "/bin/interp")
    def _boom(*a, **k):
        raise OSError("no such interpreter")
    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert mod.arch_supported("gemma4", "/bin/mlx_lm.server") is None


def test_an_empty_model_type_is_not_a_refusal():
    assert mod.arch_supported("", "/bin/mlx_lm.server") is None


def test_unsupported_arch_is_refused_before_launch(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config",
                        lambda mid: {"model_type": "gemma4_unified"})
    monkeypatch.setattr(mod, "arch_supported", lambda mt, b: False)
    with pytest.raises(RuntimeError, match="gemma4_unified") as e:
        m._check_arch_supported("mlx-community/gemma-4-12b-coder-8bit")
    msg = str(e.value)
    assert "no loader" in msg              # names the cause
    assert "hang the turn" in msg          # names what it prevented
    assert "language_model." in msg        # names the check that fixes it


def test_supported_arch_passes(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config", lambda mid: {"model_type": "gemma4"})
    monkeypatch.setattr(mod, "arch_supported", lambda mt, b: True)
    m._check_arch_supported("mlx-community/gemma-4-12b-coder-4bit")


def test_an_uncached_model_is_not_judged(monkeypatch):
    m = _mgr()
    monkeypatch.setattr(mod, "_model_config", lambda mid: None)
    m._check_arch_supported("org/not-downloaded-yet")
