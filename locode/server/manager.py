"""Single-GPU model server lifecycle (Apple Silicon / mlx_lm.server).

Ports prior shell-based server scripts: build the launch args (per-model prompt-cache
budget + enable_thinking kwarg from the capability profile), start/stop the
server, and — critically — wait for wired Metal memory to fall before starting
a different model, since MLX weights live in wired buffers and switching without
that wait can push past the memory ceiling and crash the machine.

"Crash the machine" is literal, and the preflight guard here exists because it
happened twice: a 24B model was admitted by a check that compared only against
free RAM while counting a flat 1.5 GB for the cache. Its real KV cache is ~6 GB
at our context, and the ceiling that actually matters is the macOS GPU wired
cap, not free RAM — the box took an IOGPUGroupMemory kernel panic both times.
So the guard now (a) sizes the KV cache from each model's own attention shape,
per layer type, and (b) budgets against the tighter of (RAM − reserve) and
sysctl iogpu.wired_limit_mb. Relatedly, the SIGTERM→SIGKILL escalation in
_terminate_servers scales its grace period with model size: hard-killing a
process that still holds tens of GB of live Metal buffers is its own hazard.

The PoolManager (concurrent mode) is a later milestone; this is the default.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from locode.config import Config, CONFIG_PATH, STATE_DIR
from locode.model.profiles import (
    Profile,
    lookup_thinking_override,
    profile_for,
    resolve_thinking,
)
from locode.server import aliases

GB = 1024 ** 3
# MLX's wired working set runs somewhat above the raw on-disk weight size
# (framework buffers, allocator slack); pad the estimate so the guard stays
# conservative rather than optimistic. The KV cache is NOT in here — it is
# estimated separately from the model's real shape, see kv_cache_bytes.
_WEIGHT_OVERHEAD = 1.15
# MLX holds the KV cache in fp16 regardless of weight quantization.
_KV_DTYPE_BYTES = 2
# Rough chars-per-token for the history budget. Deliberately low (i.e. yields
# MORE tokens for a given char budget) so the cache estimate errs large.
_CHARS_PER_TOKEN = 3.0
# macOS caps GPU-wired allocations at iogpu.wired_limit_mb. A value of 0 means
# "kernel default", which is ~75% of physical RAM.
_DEFAULT_WIRED_FRACTION = 0.75
# Extra SIGTERM grace per GB of resident weights, and the overall cap.
_TERM_SECS_PER_GB = 2.0
_TERM_WAIT_MAX = 60.0


@dataclass
class Status:
    up: bool
    model_id: str | None = None
    base_url: str = ""


def find_mlx_bin(configured: str = "") -> str:
    for cand in (configured, shutil.which("mlx_lm.server"),
                 "/opt/homebrew/bin/mlx_lm.server"):
        if cand and os.path.exists(cand):
            return cand
    # Fall back to the bare name; start() will surface a clear error if missing.
    return configured or "mlx_lm.server"


def _mlx_interpreter(mlx_bin: str) -> str | None:
    """The interpreter that owns mlx_lm, read from the launcher's shebang.

    locode's own venv does not have mlx_lm installed — the server runs under
    whatever python the `mlx_lm.server` console script points at — so an
    in-process `find_spec` would report "unsupported" for every architecture.
    Any doubt returns None, and the caller must then decline to judge.
    """
    try:
        with open(mlx_bin, "rb") as fh:
            first = fh.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].strip().decode("utf-8", "replace").split()
    if not parts:
        return None
    exe = parts[-1] if parts[0].endswith("env") and len(parts) > 1 else parts[0]
    return exe if os.path.exists(exe) else None


# Exit codes are the whole interface: 0 supported, 3 no such loader, 4 the
# question could not be asked (mlx_lm missing from that interpreter, so
# find_spec raises on the parent package rather than returning None). Anything
# else — a crash, a timeout — is likewise "cannot tell".
_ARCH_PROBE = (
    "import importlib.util as u, sys\n"
    "try:\n"
    "    ok = u.find_spec('mlx_lm.models.' + sys.argv[1]) is not None\n"
    "except Exception:\n"
    "    sys.exit(4)\n"
    "sys.exit(0 if ok else 3)\n")


def arch_supported(model_type: str, mlx_bin: str) -> bool | None:
    """Can the mlx_lm behind `mlx_bin` load this `model_type`?

    True/False when the probe answered; **None when we could not tell** — no
    interpreter, no model_type, a probe that crashed or timed out. The tri-state
    is the point: a guard that guesses "unsupported" on a failed probe would
    refuse models that work perfectly, which is worse than the hang it prevents.
    """
    if not model_type:
        return None
    interp = _mlx_interpreter(mlx_bin)
    if interp is None:
        return None
    try:
        rc = subprocess.run([interp, "-c", _ARCH_PROBE, model_type],
                            capture_output=True, timeout=15).returncode
    except (OSError, subprocess.SubprocessError):
        return None
    return True if rc == 0 else (False if rc == 3 else None)


_USE_PROFILE = object()  # sentinel: derive enable_thinking from the profile


def build_launch_argv(mlx_bin: str, model_id: str, host: str, port: int,
                      profile: Profile, thinking: Any = _USE_PROFILE,
                      max_tokens: int = 32768) -> list[str]:
    """Pure: the argv to launch mlx_lm.server for this model (testable).

    `thinking` is the resolved enable_thinking decision: True/False force the
    chat-template kwarg, None omits it, and the default sentinel derives it from
    the profile (so existing callers keep the profile-only behavior).

    `--max-tokens` is only the server's FALLBACK when a request omits the field;
    locode always sends config.model.max_tokens per request, so this just keeps
    the fallback from being a misleadingly-low cap. Pass the same config value.
    """
    if thinking is _USE_PROFILE:
        thinking = False if profile.thinking_arg else None
    argv = [mlx_bin, "--model", model_id, "--host", host, "--port", str(port)]
    if thinking is not None:
        argv += ["--chat-template-args",
                 json.dumps({"enable_thinking": bool(thinking)})]
    argv += [
        "--max-tokens", str(max_tokens),
        "--prompt-cache-size", "4",
        "--prompt-cache-bytes", str(profile.prompt_cache_bytes),
    ]
    return argv


class SingleGpuManager:
    def __init__(self, config: Config, alias_overrides: dict[str, str] | None = None):
        self._cfg = config
        self._base = config.base_url
        self._host = config.server.host
        self._port = config.server.port
        self._mlx_bin = find_mlx_bin(config.server.mlx_bin)
        self._overrides = alias_overrides or config.aliases
        self._proc: subprocess.Popen | None = None

    @property
    def _managed(self) -> bool:
        """True when locode owns the local server process (loopback / manage=yes).
        For a remote/unmanaged endpoint we never launch or kill a process."""
        return self._cfg.server.is_managed()

    # --- alias resolution (aliases come from config; full ids pass through) --
    def resolve(self, name: str) -> str:
        if name in self._overrides:
            return self._overrides[name]
        try:
            return aliases.resolve(name)  # "/"-id passthrough or built-in table
        except KeyError:
            known = ", ".join(self.known_aliases())
            hint = known or f"none configured — add an [aliases] table to {CONFIG_PATH}"
            raise KeyError(
                f"unknown model alias {name!r}; known: {hint} "
                f"(or pass a full org/model id)") from None

    def known_aliases(self) -> list[str]:
        """Aliases available now: the user's config [aliases] plus any built-ins."""
        return sorted(set(self._overrides) | set(aliases.known_aliases()))

    # --- status ----------------------------------------------------------
    async def is_up(self, alias: str | None = None) -> bool:
        """Bare: does *a* server answer at base_url?

        With `alias`, the narrower question the UI actually needs: is that model
        the one currently loaded? A reachable server proves nothing about which
        weights are resident — mlx serves one model at a time and /v1/models
        lists the whole HF cache — so the bare check reports "up" for a server
        holding a completely different model. An alias that doesn't resolve is
        not loaded by definition; the caller surfaces the naming error.
        """
        if alias is None:
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(f"{self._base}/v1/models")
                    return r.status_code == 200
            except httpx.HTTPError:
                return False
        try:
            target = self.resolve(alias)
        except KeyError:
            return False
        served = await self.list_served()
        if not served:
            return False
        # Same split as ensure_up: locally the process's --model arg is the only
        # truth; remotely we can't introspect, so the served list is all we have.
        if self._managed:
            return self._resident_model() == target
        return target in served

    async def list_served(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self._base}/v1/models")
                r.raise_for_status()
                return [m["id"] for m in r.json().get("data", [])]
        except httpx.HTTPError:
            return []

    async def status(self) -> Status:
        served = await self.list_served()
        if not served:
            return Status(up=False, model_id=None, base_url=self._base)
        # Report the *resident* model, not served[0] (the HF cache's first entry).
        loaded = self._resident_model() if self._managed else served[0]
        return Status(up=True, model_id=loaded or served[0], base_url=self._base)

    def _resident_model(self) -> str | None:
        """The model the local mlx_lm.server is actually serving, or None.

        mlx's /v1/models lists every cached model, not the resident one, so the
        only reliable source of what's *loaded* is the server process's own
        `--model <id>` launch argument."""
        return _resident_model_id()

    # --- lifecycle -------------------------------------------------------
    async def ensure_up(self, alias: str | None = None) -> str:
        """Ensure a server is serving. Returns the resolved model id in use.
        An already-running server is used as-is (no restart) unless `alias` names
        a model it isn't *resident* — in which case we switch to it."""
        target = self.resolve(alias) if alias else None
        served = await self.list_served()
        if served:  # a server is up
            # mlx's /v1/models lists the whole HF cache, NOT the resident model,
            # so we can't use `served` to tell what's actually loaded. For a
            # local server we read it from the process; for a remote one we can't
            # introspect, so we trust the served list.
            loaded = self._resident_model() if self._managed else served[0]
            if target is None:
                return loaded or served[0]
            if target == loaded:
                return target
            if self._managed:
                # Requested a model that isn't resident -> actually load it
                # (don't silently serve whatever is in memory).
                return await self.switch(alias)  # type: ignore[arg-type]
            # Remote: route if it serves the target, else it's a fixed-model box.
            if target in served:
                return target
            return await self.switch(alias)  # type: ignore[arg-type]
        if not self._managed:
            raise RuntimeError(
                f"no model server reachable at {self._base}. This is a "
                "remote/unmanaged endpoint — start the server there (or point "
                "[server] at a local one).")
        if not self._cfg.server.auto_start:
            raise RuntimeError(
                f"server not running at {self._base} and auto_start is off")
        return await self.start(alias or self._cfg.model.default)

    async def start(self, alias: str) -> str:
        if not self._managed:
            raise RuntimeError(
                f"refusing to launch a local server: {self._base} is a "
                "remote/unmanaged endpoint")
        model_id = self.resolve(alias)
        if not (os.path.exists(self._mlx_bin) or shutil.which(self._mlx_bin)):
            raise RuntimeError(
                f"mlx server binary not found ({self._mlx_bin}); install mlx-lm "
                "or set [server].mlx_bin")
        profile = profile_for(model_id)
        self._check_memory_budget(model_id, profile)
        self._check_arch_supported(model_id)
        override = lookup_thinking_override(self._cfg.thinking, model_id, alias)
        thinking = resolve_thinking(profile, override)
        argv = build_launch_argv(self._mlx_bin, model_id, self._host, self._port,
                                 profile, thinking, self._cfg.model.max_tokens)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(STATE_DIR / "mlx-server.log", "ab")
        self._proc = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        await self._wait_up(model_id)
        return model_id

    def _cache_bytes(self, model_id: str, profile: Profile) -> int:
        """KV-cache bytes to budget for this model at our peak context.

        Prefers the model's real attention shape over the profile's flat figure,
        which is a per-model *prompt-cache* budget and badly understates a big
        model's live cache: a 40-layer/8-KV-head/128-dim 24B costs 160 KB per
        token, so a 41k-token context is ~6 GB against a 1.5 GB profile figure.
        Never returns less than the profile figure.
        """
        measured = kv_cache_bytes(_model_config(model_id),
                                  context_tokens_for(self._cfg))
        if not measured:
            return profile.prompt_cache_bytes
        return max(profile.prompt_cache_bytes, measured)

    def _check_arch_supported(self, model_id: str) -> None:
        """Refuse a model mlx_lm has no loader for, instead of hanging on it.

        This failure is invisible from the outside and expensive: mlx_lm loads
        lazily inside the *generate* thread, so an unsupported `model_type`
        raises there, kills the thread, and leaves the HTTP request unanswered
        forever while `/v1/models` keeps returning 200. locode then sits on the
        spinner until the turn's wallclock runs out. Observed 2026-08-02 with
        gemma-4-12b-coder-8bit, whose config.json declares `gemma4_unified`.

        Silent unless the probe positively says "no loader" — see arch_supported.
        """
        cfg = _model_config(model_id)
        if not cfg:
            return
        model_type = str(cfg.get("model_type") or "")
        if arch_supported(model_type, self._mlx_bin) is not False:
            return
        raise RuntimeError(
            f"refusing to load {model_id}: its config.json declares "
            f"model_type {model_type!r}, and the mlx_lm behind {self._mlx_bin} "
            f"has no loader for it (no mlx_lm.models.{model_type} module). "
            "Loading it would fail inside the server's generate thread, which "
            "answers nothing and would hang the turn rather than erroring. "
            "If this repo is really a text-only quant mislabelled as "
            "multimodal — check whether every tensor in "
            "model.safetensors.index.json is language_model.* — patching the "
            "cached config.json's model_type to the text architecture makes it "
            "load; otherwise pick another model.")

    def _check_memory_budget(self, model_id: str, profile: Profile) -> None:
        """Refuse to launch a model that won't fit, instead of thrashing the box.
        Skips silently when the guard is disabled or the footprint can't be
        estimated (model not cached, or RAM size unknown)."""
        reserve_gb = self._cfg.server.memory_reserve_gb
        if reserve_gb <= 0:
            return
        model_bytes = _model_disk_bytes(model_id)
        total = _total_ram_bytes()
        if not model_bytes or not total:
            return
        cache_bytes = self._cache_bytes(model_id, profile)
        wired = _wired_limit_bytes(total)
        ok, need, budget = memory_fits(
            model_bytes, cache_bytes, total, int(reserve_gb * GB),
            wired_limit=wired)
        if ok:
            return
        # Name the ceiling that actually bound, so the suggested fix matches the
        # cause: lowering the reserve does nothing when the wired cap is binding.
        if wired is not None and wired <= total - int(reserve_gb * GB):
            ceiling = (f"the macOS GPU wired-memory cap "
                       f"(iogpu.wired_limit_mb = {wired // (1024 * 1024)})")
            remedy = ("pick a smaller model, shorten "
                      "[agent].max_history_chars, or raise the wired cap — but "
                      "raising it takes memory macOS itself needs, and "
                      "overshooting it panics the GPU driver")
        else:
            ceiling = (f"RAM {total / GB:.1f} GB − {reserve_gb:.0f} GB reserve")
            remedy = ("free RAM, pick a smaller model, shorten "
                      "[agent].max_history_chars, or lower "
                      "[server].memory_reserve_gb (0 disables this guard)")
        raise RuntimeError(
            f"refusing to load {model_id}: it needs ~{need / GB:.1f} GB "
            f"(weights {model_bytes / GB:.1f} GB × {_WEIGHT_OVERHEAD} + "
            f"{cache_bytes / GB:.1f} GB KV cache at "
            f"{context_tokens_for(self._cfg):,} tokens) but the budget is "
            f"{budget / GB:.1f} GB, set by {ceiling}. Loading it would risk "
            f"taking the machine down — {remedy}."
            + self._suggestion(budget, model_id))

    def _suggestion(self, budget: int, refused_id: str) -> str:
        """A concrete alias that does fit, appended to the refusal.

        Being told what won't work leaves the user to re-derive what will, one
        rejected `-m` at a time. Prefers the configured default (it's the one
        chosen on merit) and otherwise names the largest alias that fits, which
        is the closest thing to "most capable" we can read off disk. Silent when
        nothing fits or the scan fails — a suggestion is a courtesy and must
        never mask the refusal itself.
        """
        try:
            fits = self._fitting_aliases(budget, refused_id)
        except Exception:
            return ""
        if not fits:
            return ""
        default = self._cfg.model.default
        alias, need = next((f for f in fits if f[0] == default), fits[0])
        return (f" {alias} fits ({need / GB:.1f} GB) — "
                f"`/model {alias}`, or start with -m {alias}.")

    def _fitting_aliases(self, budget: int,
                         exclude_id: str = "") -> list[tuple[str, int]]:
        """(alias, estimated_need) for every cached alias inside `budget`,
        largest first. Uncached aliases can't be estimated and are skipped."""
        out: list[tuple[str, int]] = []
        for alias in self.known_aliases():
            try:
                mid = self.resolve(alias)
            except KeyError:
                continue
            if mid == exclude_id:
                continue
            weights = _model_disk_bytes(mid)
            if not weights:
                continue
            prof = profile_for(mid)
            need = int(weights * _WEIGHT_OVERHEAD) + self._cache_bytes(mid, prof)
            if need <= budget:
                out.append((alias, need))
        return sorted(out, key=lambda t: -t[1])

    async def _wait_up(self, model_id_substr: str, secs: int = 120) -> None:
        for _ in range(secs // 2):
            # If the process we launched has already exited, the server failed to
            # start (e.g. it couldn't bind the port because an old/wedged server
            # still holds it). Fail loudly rather than false-positive on a stale
            # server's cached /v1/models list.
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"mlx server exited during startup (exit code "
                    f"{self._proc.returncode}); see {STATE_DIR / 'mlx-server.log'}")
            served = await self.list_served()
            if any(model_id_substr in s for s in served):
                return
            await asyncio.sleep(2)
        raise TimeoutError(f"server did not come up serving {model_id_substr}")

    # Base grace period for a SIGTERM'd server to exit before we SIGKILL it.
    # The effective wait scales with the resident model — see _term_wait.
    _TERM_WAIT = 6.0
    _KILL_WAIT = 6.0

    def _term_wait(self) -> float:
        """SIGTERM grace period, scaled to the resident model's weight size.

        A flat 6 s is fine for a 2 GB model and far too short for a 14 GB one:
        unwiring tens of GB of Metal buffers takes real time, so the escalation
        fired on a server that was shutting down normally. SIGKILLing a process
        mid-teardown while it holds live IOGPU allocations is exactly the shape
        that panics the driver, so the wait has to cover an orderly exit.
        """
        model_id = self._resident_model()
        return term_wait_for(_model_disk_bytes(model_id) if model_id else None,
                             base=self._TERM_WAIT)

    async def stop(self) -> None:
        if not self._managed:
            # Never pkill: the endpoint is remote, and a global pkill would also
            # kill unrelated local mlx servers.
            return
        await self._terminate_servers()
        self._proc = None
        await self._wait_wired_floor()

    async def _terminate_servers(self) -> None:
        """SIGTERM the local mlx server(s); escalate to SIGKILL if they don't
        exit. A thrashing/wedged server sits in uninterruptible sleep and ignores
        SIGTERM — without the SIGKILL escalation it keeps holding the port and its
        memory, so the next start() can't bind and the machine stays pinned."""
        self._signal_servers(signal.SIGTERM)
        if await self._wait_servers_gone(self._term_wait()):
            return
        self._signal_servers(signal.SIGKILL)
        await self._wait_servers_gone(self._KILL_WAIT)

    def _signal_servers(self, sig: int) -> None:
        pkill_flag = "-KILL" if sig == signal.SIGKILL else "-TERM"
        subprocess.run(["pkill", pkill_flag, "-f", "mlx_lm.server"], check=False)
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                pass

    async def _wait_servers_gone(self, secs: float) -> bool:
        deadline = time.monotonic() + secs
        while True:
            if not _server_pids():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _wait_wired_floor(self, floor_kb: int = 300_000, tries: int = 15) -> None:
        """macOS: wait for wired Metal memory to drop before loading another
        model (RSS lies for MLX). No-op off macOS."""
        if platform.system() != "Darwin":
            return
        for _ in range(tries):
            wired = _wired_pages_kb()
            if wired is None or wired < floor_kb:
                return
            await asyncio.sleep(2)

    async def switch(self, alias: str) -> str:
        # Validate the alias BEFORE touching the server: a typo must not kill a
        # running server only to fail on resolve afterwards.
        model_id = self.resolve(alias)
        if not self._managed:
            # Remote box serves a fixed model: route if it's that one, else fail.
            if model_id in await self.list_served():
                return model_id
            raise RuntimeError(
                f"cannot switch models on remote/unmanaged endpoint {self._base}; "
                f"it serves a fixed model. Requested {alias!r}.")
        # Local: only skip the destructive stop/start if the target is *actually*
        # resident. We must NOT check list_served() here — it's the whole HF
        # cache, so it matches any cached model and we'd never reload (the model
        # would silently stay whatever was already in memory).
        if self._resident_model() == model_id:
            return model_id
        await self.stop()
        return await self.start(alias)


def memory_fits(model_bytes: int, cache_bytes: int, total_ram: int,
                reserve_bytes: int, overhead: float = _WEIGHT_OVERHEAD,
                wired_limit: int | None = None):
    """Pure: does (weights × overhead + KV cache) fit under the memory ceiling?

    The ceiling is the TIGHTER of (RAM − reserve) and the macOS wired-memory cap
    when one is known: a model can sit comfortably inside free RAM and still
    panic the GPU driver by exceeding iogpu.wired_limit_mb.

    Returns (ok, estimated_need_bytes, budget_bytes).
    """
    need = int(model_bytes * overhead) + cache_bytes
    budget = total_ram - reserve_bytes
    if wired_limit is not None:
        budget = min(budget, wired_limit)
    return need <= budget, need, budget


def term_wait_for(weight_bytes: int | None, base: float = 6.0) -> float:
    """Pure: seconds to let a SIGTERM'd server exit, given its weight size.
    Unknown size falls back to `base` — we only ever *extend* the wait on
    evidence, never shorten it."""
    if not weight_bytes or weight_bytes <= 0:
        return base
    return min(base + _TERM_SECS_PER_GB * (weight_bytes / GB), _TERM_WAIT_MAX)


def context_tokens_for(cfg: Config) -> int:
    """Peak context the agent can present to the model, in tokens: the whole
    history budget plus one full generation. This is what the KV cache has to
    hold at the worst moment, which is the moment that decides whether the load
    was safe."""
    return int(cfg.agent.max_history_chars / _CHARS_PER_TOKEN) + cfg.model.max_tokens


def _total_ram_bytes() -> int | None:
    """Physical RAM in bytes via `sysctl hw.memsize` (macOS), else None."""
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _wired_limit_bytes(total_ram: int | None) -> int | None:
    """macOS's GPU wired-memory cap in bytes, or None off Darwin / if unknown.

    This — not free RAM — is the ceiling MLX actually hits: weights and KV cache
    live in wired Metal buffers, and allocating past the cap is what takes the
    machine down rather than merely swapping. Raising it above the default is a
    common "make the big model fit" tweak that instead removes the headroom
    macOS itself needs, so the guard must read the live value.
    """
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"],
                             capture_output=True, text=True, timeout=5)
        mb = int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if mb > 0:
        return mb * 1024 * 1024
    return int(total_ram * _DEFAULT_WIRED_FRACTION) if total_ram else None


def _hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    base = os.environ.get("HF_HOME")
    root = Path(base) if base else Path.home() / ".cache" / "huggingface"
    return root / "hub"


def _snapshot_root(model_id: str) -> Path | None:
    """The HF cache `snapshots/` dir for `model_id`, or None if not cached."""
    if "/" not in model_id:
        return None
    org, name = model_id.split("/", 1)
    snap = _hf_hub_dir() / f"models--{org}--{name}" / "snapshots"
    return snap if snap.is_dir() else None


def _model_disk_bytes(model_id: str) -> int | None:
    """Sum the *.safetensors weight sizes in the HF cache for `model_id` (a good
    proxy for the wired memory it will need). None if it isn't cached locally —
    we can't estimate a model we haven't downloaded, so the guard skips it."""
    snap = _snapshot_root(model_id)
    if snap is None:
        return None
    total, found = 0, False
    for st in snap.rglob("*.safetensors"):
        try:
            total += st.stat().st_size  # follows the symlink into blobs/
            found = True
        except OSError:
            pass
    return total if found else None


def _model_config(model_id: str) -> dict[str, Any] | None:
    """The cached model's config.json as a dict, or None if unreadable."""
    snap = _snapshot_root(model_id)
    if snap is None:
        return None
    for cfg_path in snap.rglob("config.json"):
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def kv_cache_bytes(config: dict[str, Any] | None, tokens: int) -> int | None:
    """Bytes of KV cache a model holds at `tokens` of context, from its config.

    Both K and V are stored per layer at `kv_heads × head_dim`, in fp16 — MLX
    keeps the cache in half precision even when the *weights* are 4-bit, so
    quantization does not shrink it. Returns None when the config doesn't carry
    the shape (the caller then falls back to the profile figure).

    Layers are NOT uniform, and assuming they are overestimates badly enough to
    block models that run fine:
      - `full_attention` grows with the whole context.
      - `sliding_attention` is pinned at `sliding_window` tokens. Gemma-3 runs
        40 of its 48 layers on a 1024-token window, so the flat estimate came
        out ~5x high.
      - `linear_attention` (gated delta-net / Mamba-style, as in the Qwythos and
        Bonsai hybrids) keeps a fixed-size recurrent state that does not scale
        with context at all. Counted as zero here; it's a few MB, well inside
        the weight overhead.

    Vision-tower repos nest the language model's shape under `text_config`;
    prefer that so a VL-derived text model isn't measured against the wrapper.
    """
    if not isinstance(config, dict):
        return None
    tc = config.get("text_config")
    tc = tc if isinstance(tc, dict) else config
    try:
        layers = int(tc["num_hidden_layers"])
        kv_heads = int(tc["num_key_value_heads"])
    except (KeyError, TypeError, ValueError):
        return None
    head_dim = tc.get("head_dim")
    if not head_dim:
        try:  # derive it the usual way when the config doesn't state it
            head_dim = int(tc["hidden_size"]) // int(tc["num_attention_heads"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        head_dim = int(head_dim)
    except (TypeError, ValueError):
        return None
    if min(layers, kv_heads, head_dim) <= 0 or tokens <= 0:
        return None

    per_layer_token = 2 * kv_heads * head_dim * _KV_DTYPE_BYTES
    window = _sliding_window(tc)
    types = tc.get("layer_types")
    if isinstance(types, list) and types:
        total = 0
        for kind in types:
            if kind == "linear_attention":
                continue
            span = tokens
            if kind == "sliding_attention" and window:
                span = min(tokens, window)
            total += span * per_layer_token
        return total
    # No per-layer map: uniform attention, with a global window if one applies.
    span = min(tokens, window) if window else tokens
    return layers * span * per_layer_token


def _sliding_window(tc: dict[str, Any]) -> int | None:
    """The effective sliding-window size, or None if the model isn't using one.

    `sliding_window` may be set while `use_sliding_window` is false — Qwen2.5
    ships a 131072 window it does not apply — so the flag has to win when it is
    explicitly false.
    """
    if tc.get("use_sliding_window") is False:
        return None
    try:
        window = int(tc.get("sliding_window") or 0)
    except (TypeError, ValueError):
        return None
    return window if window > 0 else None


def _resident_model_id() -> str | None:
    """The model a local mlx_lm.server was launched with, read from its
    `--model <id>` argument via `ps`. mlx's /v1/models lists the whole HF cache,
    not the resident model, so the process command line is the only reliable
    source of what is actually loaded. None if no server runs or the arg is
    absent (also None off a system without a usable `ps`)."""
    try:
        out = subprocess.run(["ps", "-axo", "command"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "mlx_lm.server" not in line:
            continue
        parts = line.split()
        if "--model" in parts:
            i = parts.index("--model")
            if i + 1 < len(parts):
                return parts[i + 1]
    return None


def _server_pids() -> list[int]:
    """PIDs of running mlx_lm.server processes (best-effort, via pgrep)."""
    try:
        out = subprocess.run(["pgrep", "-f", "mlx_lm.server"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(p) for p in out.stdout.split() if p.isdigit()]


def _wired_pages_kb() -> int | None:
    """Approximate wired memory in KB via vm_stat (macOS), else None."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        if "wired" in line.lower():
            digits = "".join(ch for ch in line.split(":")[-1] if ch.isdigit())
            if digits:
                return int(digits) * 4  # 4KB pages -> KB
    return None
