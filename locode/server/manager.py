"""Single-GPU model server lifecycle (Apple Silicon / mlx_lm.server).

Ports prior shell-based server scripts: build the launch args (per-model prompt-cache
budget + enable_thinking kwarg from the capability profile), start/stop the
server, and — critically — wait for wired Metal memory to fall before starting
a different model, since MLX weights live in wired buffers and switching without
that wait can push past the memory ceiling and crash the machine.

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

import httpx

from locode.config import Config, CONFIG_PATH, STATE_DIR
from locode.model.profiles import Profile, profile_for
from locode.server import aliases

GB = 1024 ** 3
# MLX's wired working set runs somewhat above the raw on-disk weight size
# (framework buffers, the growing KV cache headroom); pad the estimate so the
# guard stays conservative rather than optimistic.
_WEIGHT_OVERHEAD = 1.15


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


def build_launch_argv(mlx_bin: str, model_id: str, host: str, port: int,
                      profile: Profile) -> list[str]:
    """Pure: the argv to launch mlx_lm.server for this model (testable)."""
    argv = [mlx_bin, "--model", model_id, "--host", host, "--port", str(port)]
    if profile.thinking_arg:
        argv += ["--chat-template-args", json.dumps({"enable_thinking": False})]
    argv += [
        "--max-tokens", "4096",
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
    async def is_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self._base}/v1/models")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

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
        return Status(up=bool(served), model_id=served[0] if served else None,
                      base_url=self._base)

    # --- lifecycle -------------------------------------------------------
    async def ensure_up(self, alias: str | None = None) -> str:
        """Ensure a server is serving. Returns the resolved model id in use.
        An already-running server is used as-is (no restart) unless `alias` names
        a model it isn't serving."""
        target = self.resolve(alias) if alias else None
        served = await self.list_served()
        if served:
            if target is None or target in served:
                return target or served[0]
            # A specific model was requested that isn't served -> switch.
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
        argv = build_launch_argv(self._mlx_bin, model_id, self._host, self._port, profile)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(STATE_DIR / "mlx-server.log", "ab")
        self._proc = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        await self._wait_up(model_id)
        return model_id

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
        ok, need, budget = memory_fits(
            model_bytes, profile.prompt_cache_bytes, total, int(reserve_gb * GB))
        if not ok:
            raise RuntimeError(
                f"refusing to load {model_id}: it needs ~{need / GB:.1f} GB "
                f"(weights {model_bytes / GB:.1f} GB + overhead + cache) but the "
                f"budget is {budget / GB:.1f} GB (RAM {total / GB:.1f} GB − "
                f"{reserve_gb:.0f} GB reserve). Loading it would likely thrash the "
                f"machine — free RAM, pick a smaller model, or lower "
                f"[server].memory_reserve_gb (0 disables this guard).")

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

    # Grace period for a SIGTERM'd server to exit before we SIGKILL it.
    _TERM_WAIT = 6.0
    _KILL_WAIT = 6.0

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
        if await self._wait_servers_gone(self._TERM_WAIT):
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
        # If the target is already being served, route to it without a
        # destructive stop/start (single-GPU restart is only needed to load a
        # model that isn't resident).
        if model_id in await self.list_served():
            return model_id
        if not self._managed:
            raise RuntimeError(
                f"cannot switch models on remote/unmanaged endpoint {self._base}; "
                f"it serves a fixed model. Requested {alias!r}.")
        await self.stop()
        return await self.start(alias)


def memory_fits(model_bytes: int, cache_bytes: int, total_ram: int,
                reserve_bytes: int, overhead: float = _WEIGHT_OVERHEAD):
    """Pure: does (weights × overhead + prompt cache) fit in (RAM − reserve)?
    Returns (ok, estimated_need_bytes, budget_bytes)."""
    need = int(model_bytes * overhead) + cache_bytes
    budget = total_ram - reserve_bytes
    return need <= budget, need, budget


def _total_ram_bytes() -> int | None:
    """Physical RAM in bytes via `sysctl hw.memsize` (macOS), else None."""
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    base = os.environ.get("HF_HOME")
    root = Path(base) if base else Path.home() / ".cache" / "huggingface"
    return root / "hub"


def _model_disk_bytes(model_id: str) -> int | None:
    """Sum the *.safetensors weight sizes in the HF cache for `model_id` (a good
    proxy for the wired memory it will need). None if it isn't cached locally —
    we can't estimate a model we haven't downloaded, so the guard skips it."""
    if "/" not in model_id:
        return None
    org, name = model_id.split("/", 1)
    snap = _hf_hub_dir() / f"models--{org}--{name}" / "snapshots"
    if not snap.is_dir():
        return None
    total, found = 0, False
    for st in snap.rglob("*.safetensors"):
        try:
            total += st.stat().st_size  # follows the symlink into blobs/
            found = True
        except OSError:
            pass
    return total if found else None


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
