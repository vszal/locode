"""Configuration: defaults <- config.toml <- env (LOCODE_*) <- CLI overrides.

CLI overrides are applied by cli.py via Config.override(); this module handles
the first three layers and the XDG paths. Tolerant by design — a missing or
partial config.toml just falls back to defaults so the tool always starts.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _xdg(env: str, default: Path) -> Path:
    val = os.environ.get(env)
    return Path(val) if val else default


HOME = Path.home()
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "locode"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "locode"
CONFIG_PATH = CONFIG_DIR / "config.toml"
HISTORY_PATH = STATE_DIR / "history"
DATA_DIR = _xdg("XDG_DATA_HOME", HOME / ".local" / "share") / "locode"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8081
    scheme: str = "http"          # "http" | "https" (for remote/proxied endpoints)
    base_url: str = ""            # full override (e.g. https://gpu-box:8081); wins
    auto_start: bool = True
    # Manage a LOCAL mlx_lm.server process (start/stop/switch)? "auto" => yes only
    # for a loopback endpoint; "no" treats the endpoint as remote/unmanaged.
    manage: str = "auto"          # "auto" | "yes" | "no"
    mlx_bin: str = ""  # auto-detected if empty
    # Refuse to load a local model whose estimated footprint (weights × overhead
    # + prompt cache) won't fit in (total RAM − this reserve), so a too-big model
    # can't thrash the machine. 0 disables the guard.
    memory_reserve_gb: float = 5.0

    def endpoint(self) -> str:
        """Resolved base URL: an explicit base_url wins, else scheme://host:port."""
        if self.base_url:
            return self.base_url.rstrip("/")
        return f"{self.scheme}://{self.host}:{self.port}"

    def is_managed(self) -> bool:
        """Whether locode owns the server process (can start/stop it locally)."""
        if self.manage == "yes":
            return True
        if self.manage == "no":
            return False
        host = (urlsplit(self.base_url).hostname if self.base_url else self.host) or ""
        return host in _LOOPBACK


@dataclass
class ModelConfig:
    default: str = "qwen14"
    # Per-turn generation ceiling. A whole write_file/edit_file call — the file
    # body included — must fit in ONE completion, and on a reasoning distill the
    # <think> preamble eats into the same budget, so a tight cap truncates the
    # tool call mid-write (the model then "chunks" a large file).
    #
    # But this is also a WALLCLOCK setting in disguise, which is easy to miss.
    # A 9B MXFP8 model on an M4 Max streams ~26 tok/s, so the old 32768 ceiling
    # let ONE reply run for ~21 minutes — twice the entire turn budget — and the
    # loop's iteration/repeat guards can't see it, because they only run
    # BETWEEN iterations (measured 2026-07-21: a design-doc run spent 860 of its
    # 900 seconds inside a single completion and produced nothing).
    #
    # 6144 tokens is ~4 minutes of generation and still comfortably fits the
    # largest thing the loop legitimately emits at once — a ~2500-word design
    # document or a ~300-line module, both around 4k tokens. A model that needs
    # more gets a truncation nudge and writes the rest in a second call, which
    # is a far better failure mode than losing the whole turn.
    max_tokens: int = 6144
    temperature: float = 0.3


@dataclass
class AgentConfig:
    # The loop executes ONE tool call per iteration for non-native (fenced
    # ```tool) callers — see loop.py's `trimmed` grounding logic — so this is
    # ~one iteration per file read/edit/test-run, not per logical step. A
    # genuinely multi-file refactor can easily need 30-40 calls; the real stuck
    # loops are already bounded by max_repeat_calls/max_error_stall/wallclock,
    # so this ceiling only needs to catch a model that's truly never going to
    # finish, not cut off one that's still making progress.
    max_iterations: int = 50
    max_wallclock_seconds: int = 600
    max_malformed_retries: int = 3  # bail if the model keeps emitting bad tool JSON
    # How many times a reply cut off at the token limit may be re-nudged. More
    # than one because a genuinely long deliverable (a full design document) can
    # legitimately need two passes to fit under model.max_tokens — one-shot
    # would return the half-written second attempt as if it were the answer.
    max_truncated_retries: int = 2
    max_repeat_calls: int = 3        # bail if it repeats the same call w/o progress
    max_error_stall: int = 3         # nudge/bail if edits keep hitting the same error
    # Bail if the model keeps trying to end the turn without EVER having
    # attempted a write_file/edit_file call for a deliverable it was explicitly
    # asked to produce (e.g. "writing a PLAN.md") — as opposed to having tried
    # and failed/been denied, which is trusted after a single nudge.
    max_missing_deliverable_retries: int = 3
    # How many times a model that tries to end the turn with tasks still open
    # in its OWN update_plan list gets pushed back to the work. Bounded because
    # a task it genuinely cannot finish must not become an infinite loop; the
    # nudge explicitly offers "mark it done and say why" as the way out.
    max_open_task_retries: int = 3
    # Catches a model burning wallclock on slow/rambling completions WITHOUT
    # advancing iterations — a different failure mode than simply running out of
    # time. Nudged once (never a hard stop; the wallclock/iteration caps above
    # already bound the turn) when the fraction of iterations consumed falls
    # below slow_progress_ratio x the fraction of wallclock consumed. Held off
    # until BOTH grace thresholds pass, so first-iteration cold-start / first-
    # token latency can't skew the ratio into a false positive.
    slow_progress_ratio: float = 0.5
    slow_progress_grace_seconds: float = 60.0
    slow_progress_grace_iterations: int = 1
    # Hard ceiling on total history size (chars, summed across all messages —
    # a cheap proxy for tokens at ~4 chars/token; no tokenizer dependency).
    # locode has no context compaction: history only shrinks via an explicit
    # reset, so a long session (or a stuck loop re-appending similar content
    # each turn) grows it unboundedly. A local mlx server doesn't reliably
    # reject an over-budget prompt — observed in practice: a stuck edit loop
    # grew the prompt cache past 5GB and mlx_lm hard-crashed with a Metal
    # "Insufficient Memory" abort instead of returning an error. Default is
    # conservative for a ~32K-token local model's context window; raise it for
    # models with a much larger window (e.g. a 1M-ctx model).
    max_history_chars: int = 100_000
    # Soft threshold (fraction of max_history_chars) that triggers structural,
    # deterministic compaction (agent/compact.py) BEFORE the hard stop above
    # can fire — no model call involved, so a weak local model can't stall or
    # hallucinate its way through it. Stale tool-result dumps collapse to a
    # one-line summary and bulky tool-call args (a write_file's full file
    # body) get shrunk; the system prompt, every real user prompt, file-change
    # receipts, and a trailing window of compact_keep_recent messages are
    # always kept verbatim. The same logic backs the explicit /compact command.
    auto_compact_ratio: float = 0.75
    # How many of the most recent messages auto-compact / /compact always
    # leave untouched (the current work in progress).
    compact_keep_recent: int = 8


@dataclass
class PermissionsConfig:
    # tool name -> "auto" | "ask" | "deny"
    tools: dict[str, str] = field(default_factory=lambda: {
        "read_file": "auto", "ls": "auto", "glob": "auto", "grep": "auto",
        "write_file": "ask", "edit_file": "ask", "move_file": "ask", "bash": "ask",
        "web_search": "ask", "web_fetch": "auto",
        # Bookkeeping only — update_plan touches nothing but the agent's own
        # in-memory task list, so prompting for it would be pure noise. It must
        # be listed explicitly: unlisted tools resolve to "ask" regardless of
        # the tool class's own `permission` attribute, which headless means
        # "silently denied".
        "update_plan": "auto",
    })
    auto_allow_under: list[str] = field(default_factory=lambda: ["./sandbox"])
    deny_paths: list[str] = field(default_factory=lambda: [
        "~/.ssh", "~/.aws", "~/.config/locode",
    ])


@dataclass
class EditorConfig:
    command: str = ""
    open_diffs: bool = True
    diff_tool: str = ""
    wait: bool = True


@dataclass
class WebConfig:
    # web_search provider: "auto" (a keyed provider if configured, else the
    # keyless "duckduckgo" default), "tavily", "brave", or "duckduckgo".
    search_provider: str = "auto"
    # Per-provider keys (env TAVILY_API_KEY / BRAVE_API_KEY also honored). With
    # no key a provider stays registered but disabled with an actionable error.
    tavily_api_key: str = ""
    brave_api_key: str = ""
    max_results: int = 5
    # web_fetch egress allowlist — host SUFFIXES the model may fetch. Empty =>
    # fail closed (every fetch refused). See tools/web.py for the SSRF guard.
    fetch_allowlist: list[str] = field(default_factory=lambda: [
        "docs.python.org", "developer.mozilla.org", "en.wikipedia.org",
        "pkg.go.dev", "docs.rs", "example.com",
    ])
    fetch_max_bytes: int = 5_000_000
    fetch_timeout: int = 20


@dataclass
class UIConfig:
    markdown: bool = True       # line-buffered markdown styling of answers (TTY+color)
    spinner: bool = True        # animated wait indicator for model load / first token
    timing: bool = True         # per-turn `~tok · Ns · tok/s` trailer


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    editor: EditorConfig = field(default_factory=EditorConfig)
    web: WebConfig = field(default_factory=WebConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    aliases: dict[str, str] = field(default_factory=dict)  # extends the built-in table
    # Per-model reasoning override: alias or model-id substring -> "on" | "off"
    # | "auto". Layers over the capability profile's default at server launch.
    # "on"/"off" force the chat-template enable_thinking kwarg; "auto" omits it.
    thinking: dict[str, str] = field(default_factory=dict)

    # --- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls()
        raw = _read_toml(path if path is not None else CONFIG_PATH)
        if raw:
            cfg._merge_toml(raw)
        cfg._apply_env(os.environ)
        return cfg

    def _merge_toml(self, raw: dict[str, Any]) -> None:
        _assign(self.server, raw.get("server", {}))
        _assign(self.model, raw.get("model", {}))
        _assign(self.agent, raw.get("agent", {}))
        _assign(self.editor, raw.get("editor", {}))
        _assign(self.web, raw.get("web", {}))
        _assign(self.ui, raw.get("ui", {}))
        perms = raw.get("permissions", {})
        # Per-tool keys live flat in [permissions] alongside the list keys.
        for k, v in perms.items():
            if k == "auto_allow_under":
                self.permissions.auto_allow_under = list(v)
            elif k == "deny_paths":
                self.permissions.deny_paths = list(v)
            else:
                self.permissions.tools[k] = v
        self.aliases.update(raw.get("aliases", {}))
        self.thinking.update(raw.get("thinking", {}))

    def _apply_env(self, env: dict[str, str]) -> None:
        # A small, documented set of env overrides for the common knobs.
        if "LOCODE_MODEL" in env:
            self.model.default = env["LOCODE_MODEL"]
        if "LOCODE_PORT" in env:
            self.server.port = int(env["LOCODE_PORT"])
        if "LOCODE_HOST" in env:
            self.server.host = env["LOCODE_HOST"]
        if "LOCODE_SCHEME" in env:
            self.server.scheme = env["LOCODE_SCHEME"]
        # One-shot remote endpoint: LOCODE_BASE_URL=https://gpu-box:8081 overrides
        # scheme/host/port entirely (and, via is_managed(), marks it remote).
        if "LOCODE_BASE_URL" in env:
            self.server.base_url = env["LOCODE_BASE_URL"]
        if "LOCODE_MANAGE_SERVER" in env:
            self.server.manage = env["LOCODE_MANAGE_SERVER"]
        if "LOCODE_NO_AUTOSTART" in env:
            self.server.auto_start = False
        if "LOCODE_EDITOR" in env:
            self.editor.command = env["LOCODE_EDITOR"]
        # Search keys: explicit config wins; otherwise fall back to standard envs.
        if not self.web.tavily_api_key and env.get("TAVILY_API_KEY"):
            self.web.tavily_api_key = env["TAVILY_API_KEY"]
        if not self.web.brave_api_key and env.get("BRAVE_API_KEY"):
            self.web.brave_api_key = env["BRAVE_API_KEY"]

    def override(self, **kw: Any) -> "Config":
        """Return a copy with top-level CLI overrides applied (highest priority)."""
        model = self.model
        schanges: dict[str, Any] = {}
        if kw.get("model"):
            model = replace(model, default=kw["model"])
        if kw.get("port"):
            schanges["port"] = kw["port"]
        if kw.get("host"):
            schanges["host"] = kw["host"]
        if kw.get("base_url"):
            schanges["base_url"] = kw["base_url"]
        server = replace(self.server, **schanges) if schanges else self.server
        achanges: dict[str, Any] = {}
        if kw.get("max_iterations"):
            achanges["max_iterations"] = kw["max_iterations"]
        if kw.get("max_wallclock"):
            achanges["max_wallclock_seconds"] = kw["max_wallclock"]
        agent = replace(self.agent, **achanges) if achanges else self.agent
        return replace(self, model=model, server=server, agent=agent)

    @property
    def base_url(self) -> str:
        return self.server.endpoint()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError):
        # A broken config shouldn't prevent startup; fall back to defaults.
        return {}


def _assign(obj: Any, data: dict[str, Any]) -> None:
    """Set only known dataclass fields from a TOML table; ignore extras."""
    known = obj.__dataclass_fields__
    for k, v in data.items():
        if k in known:
            setattr(obj, k, v)
