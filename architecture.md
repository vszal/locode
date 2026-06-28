# `locode` — Architecture & Specification

A Claude Code–style agentic CLI for **local** LLMs served by `mlx_lm.server`
on `:8081`. Standalone and self-contained: it manages the model server's
lifecycle (start / stop / switch), drives an agentic tool-use loop on top of
the local model, and ships with an install + self-update path. Targets
macOS (Apple Silicon, MLX) and Linux (any OpenAI-compatible `:8081`).

> Name: **`locode`** ("local code") — confirmed. It's the package name, the
> `locode` entrypoint, the config dir (`~/.config/locode`), and the PyPI
> project. Banner/splash in [§6.4](#64-splash-screen-uirenderpy).

---

## 1. Goals & non-goals

### Goals
- **Agentic coding tool.** The model can read/write/edit files and run shell
  commands through a controlled tool-use loop — the core of "all the basics
  in Claude Code."
- **Interactive REPL** with streaming output, conversation history, and a
  multi-turn session.
- **Multiple-choice questions** — the assistant can ask the user a structured
  question and get a selected answer back (the local analogue of Claude Code's
  `AskUserQuestion`).
- **Interruptible** — `Esc` (or `Ctrl-C`) cancels an in-flight generation or
  tool call cleanly without killing the session.
- **Self-contained lifecycle** — auto-start `:8081` if down; switch models on
  demand. Default single-GPU (stop-then-start); a configurable **concurrent
  mode** keeps multiple backends/models resident (see [§5.5](#55-serving-modes--concurrency)).
- **Editor integration** — open files and **diffs** in the user's local editor
  (`$EDITOR` / VS Code / etc.) for review and edit (see [§6.7](#67-editor-integration-uieditorpy)).
- **One-command install + upgrade** on unix/linux/mac.

### Non-goals (v1)
- No cloud model fallback — local only, by design (cost/privacy).
- No full IDE *extension* (LSP server, in-editor chat panel). v1 editor
  integration is **shell-out only** — open file/diff in the local editor; a
  deeper extension is future work ([§13](#13-open-questions)).
- No web UI (CLI/TUI only).
- Not a drop-in re-implementation of every Claude Code subcommand — we mirror
  the *interaction model*, not the exact surface.

> **Concurrency note.** The default target is a single Apple Silicon GPU, where
> calls are sequential and model-switching evicts. Multi-GPU / concurrent
> serving is **opt-in configuration**, not the default — it requires hardware
> or backends that support it (multiple GPUs, or several OpenAI-compatible
> endpoints). See [§5.5](#55-serving-modes--concurrency).

### Reuse vs. standalone
Per decision, the CLI is **standalone**: it bundles its own server-lifecycle
manager rather than depending on an external offload/server repo. Where existing
server scripts encode hard-won knowledge (alias table, per-model memory
budgets, wired-memory teardown wait), we **port that logic** into the package
(`locode/server/`) rather than shelling out to an external script. The bundled
manager is a faithful reimplementation, documented as such, so the two can
diverge independently. (See [§5](#5-model-server-lifecycle).)

---

## 2. The central constraint: weak local function-calling

`mlx_lm.server` (v0.31.x) is **pure inference**. It accepts an OpenAI-style
`tools` array and renders it through the model's chat template, returning
`tool_calls` when the model emits them — but in practice:

- Quality varies sharply by model. Qwen3-14B tool-calls reasonably; gemma /
  phi / smaller models emit malformed or spurious calls.
- Some models (esp. Qwen) **reflexively** emit tool calls even when none is
  warranted, or wrap JSON in prose.
- The server is **stateless** per request: no server-side conversation or tool
  state. The client owns the entire message history.

The existing offload tooling sidestepped this by dropping tools entirely
(`post-local.py`). We can't — an agentic coder needs tools. So the harness
must be **defensive about tool-calling** rather than trusting it. This is the
single most important design driver and shapes [§4](#4-tool-use-harness).

**Strategy:** a dual-path tool protocol with a tolerant parser:
1. **Native path** — send `tools` in the request; if the model returns
   well-formed `tool_calls`, use them.
2. **Harness path (fallback)** — also instruct the model, in the system
   prompt, to emit tool calls as a **fenced JSON block** when native calling
   isn't available/reliable. A tolerant parser extracts tool intent from
   either channel.

Both converge on the same internal `ToolCall` representation. Model-specific
quirks live in a small **capability profile** per model alias
([§5.3](#53-model-capability-profiles)).

---

## 3. High-level architecture

```
┌──────────────────────────────────────────────────────────────┐
│  CLI / TUI layer  (prompt_toolkit)                            │
│  - REPL, streaming render, Esc-interrupt, slash commands      │
│  - multiple-choice prompt widget                              │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│  Agent loop  (orchestrator)                                   │
│  - builds messages, calls model, parses tool intent           │
│  - dispatches tools, applies permission policy                │
│  - cancellation-aware (cooperative + hard cancel)             │
└───────┬───────────────────────────┬──────────────────────────┘
        │                           │
┌───────▼─────────┐        ┌────────▼─────────────────────────┐
│  Model client   │        │  Tool registry + executor        │
│  - HTTP→:8081   │        │  read_file, write_file, edit,    │
│  - stream/SSE   │        │  bash, ls, grep, glob, ask_user  │
│  - tool parse   │        │  - permission gate per tool      │
└───────┬─────────┘        └──────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│  Server lifecycle manager  (ported from prior shell scripts)  │
│  - alias table, start/stop/wait, model switch, mem budgets    │
└──────────────────────────────────────────────────────────────┘
```

### Package layout
```
locode/
  __init__.py
  __main__.py            # `python -m locode`
  cli.py                 # arg parsing, subcommands, entrypoint
  config.py              # config load/merge, paths (XDG)
  scaffold.py            # first-run starter config.toml + notice
  session.py             # conversation state, history persistence
  agent/
    loop.py              # the agentic orchestration loop
    messages.py          # message/role model, context assembly
    cancel.py            # cancellation tokens + signal handling
  model/
    client.py            # HTTP client to :8081 (stream + non-stream)
    toolparse.py         # dual-path tool-call extraction (tolerant)
    profiles.py          # per-model capability profiles
  tools/
    base.py              # Tool ABC, ToolCall/ToolResult, registry
    fs.py                # read_file, write_file, edit_file, ls, glob, grep
    shell.py             # bash (sandboxed, gated)
    web.py               # web_search (Tavily), web_fetch (allowlist+SSRF guard)
    ask.py               # ask_user multiple-choice tool
  server/
    manager.py           # lifecycle iface: SingleGpuManager | PoolManager
    router.py            # concurrent mode: model->backend routing, warm/evict
    aliases.py           # alias resolution rule (table lives in user config)
    memory.py            # per-model prompt-cache/wired budgets
  ui/
    repl.py              # prompt_toolkit REPL + key bindings
    render.py            # streaming markdown render, tool-call display
    choice.py            # multiple-choice selector widget
    editor.py            # open file/diff in local editor ($EDITOR/code/...)
    slash.py             # slash-command dispatch
  permissions.py         # permission policy + prompts (allow/ask/deny)
pyproject.toml
install.sh
README.md
```

---

## 4. Tool-use harness

### 4.1 Tool model
A tool is a Python class implementing:
```python
class Tool(ABC):
    name: str
    description: str
    schema: dict                  # JSON Schema for params (OpenAI tools format)
    permission: Permission        # default gate: AUTO | ASK | DENY
    def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...
```
`ToolResult` carries `{ ok, content, is_error, display }`. `ToolContext`
exposes the cwd, the cancellation token, and the permission resolver.

### 4.2 Built-in tools (v1)
| Tool | Purpose | Default permission |
|------|---------|--------------------|
| `read_file(path, [range])`     | Read a file (line-numbered).            | AUTO |
| `ls(path)` / `glob(pattern)`   | List / glob files.                      | AUTO |
| `grep(pattern, [path])`        | Ripgrep-style search.                   | AUTO |
| `write_file(path, content)`    | Create/overwrite a file.                | ASK  |
| `edit_file(path, old, new)`    | Exact-string replace (Claude-Code style).| ASK |
| `bash(cmd)`                    | Run a shell command.                    | ASK  |
| `web_search(query)`            | Web search via the **Tavily** API.      | ASK¹  |
| `web_fetch(url)`               | Fetch one URL (allowlist + SSRF guard). | AUTO¹ |
| `ask_user(question, options)`  | Multiple-choice question to the human.  | AUTO |

Read-only tools are AUTO; mutating/side-effecting tools (`write_file`,
`edit_file`, `bash`) default to ASK. Permissions are configurable and can be
"remembered" per session (see [§7](#7-permissions)).

¹ Web egress is **bounded by guards**, not absence — see
[§4.6](#46-external-access-web-tools). `web_search` defaults to **ASK** (each
call spends Tavily credits and the query is an outbound channel); the human
sees the query before it's sent. `web_fetch` is AUTO since it's already
constrained to the allowlist + SSRF guard. Either can be reset in config.

### 4.3 The loop
```
build system prompt (+ tool specs, +capability profile rules)
append user turn
loop:
    response = model.complete(messages, tools=profile.native_tools)
    stream tokens to UI (interruptible)
    calls = toolparse.extract(response, profile)     # native ∪ fenced
    if no calls:
        finalize assistant turn; break
    for call in calls:
        gate = permissions.resolve(call)             # AUTO/ASK/DENY
        if gate == DENY: result = denied
        elif gate == ASK: result = prompt_user() ? run : declined
        else: result = call.run()
        append tool_result message
    # continue loop so the model can react to tool results
```
Guards: a **max-iterations** cap (default 25) and a **max-wallclock** budget
prevent runaway loops on a model that keeps tool-calling. Both surface a
clear "stopped: budget exceeded" message rather than hanging.

### 4.4 Tolerant tool parsing (`toolparse.py`)
Extract `ToolCall`s from, in priority order:
1. The response's structured `tool_calls` (native path), if present and
   schema-valid.
2. A fenced block the system prompt asks for:
   ````
   ```tool
   {"name": "edit_file", "args": {...}}
   ```
   ````
3. Best-effort salvage: a single top-level JSON object that matches a known
   tool name (handles models that emit bare JSON without the fence).

Malformed calls don't crash the loop — they become a `tool_result` with
`is_error: true` and a short "couldn't parse your tool call; emit exactly one
```tool block" nudge, which empirically recovers most models. The parser is
unit-tested against captured good/bad outputs per model.

### 4.5 Context & prompt-cache discipline
The server reuses a shared **leading prefix** across sequential calls
(`--prompt-cache-size`). To benefit: keep the system prompt + tool specs
**stable and first**, and put the volatile conversation tail last. The agent
assembles messages so the cacheable prefix doesn't churn between turns. When
context approaches the model's window, older turns are **summarized**
(via a local-model summarization pass) rather than hard-truncated.

### 4.6 External access (web tools)
The agent reaches the outside world through two guarded tools, ported from the
offload repo's hardened implementations (`tools/web_fetch.sh`,
`tools/url_guard.py`, `tools/fetch-allowlist.txt`) and reimplemented in
`locode/tools/web.py`:

- **`web_search(query)` — pluggable provider.** Backends sit behind a
  `SearchBackend` interface (`name`, `enabled()`, `search()`), each normalizing
  its API response to `SearchResult(title, url, snippet)`. v1 ships **Tavily**,
  **Brave**, and **DuckDuckGo** (keyless, scrapes the HTML endpoint).
  `[web].search_provider` selects one (`"tavily"`, `"brave"`, `"duckduckgo"`) or
  `"auto"` — **a keyed provider if one is configured, else the keyless
  DuckDuckGo default**, so search works with zero setup. Keys come from env
  (`TAVILY_API_KEY` / `BRAVE_API_KEY`) or `[web].<provider>_api_key`. If an
  explicitly chosen keyed provider has no key the tool is **registered but
  disabled**, returning an actionable "set `<PROVIDER>_API_KEY`" error rather
  than vanishing. New providers = add a `SearchBackend` subclass to the
  `_BACKENDS` table (keyed first, keyless fallbacks last).
- **`web_fetch(url)` — hardened single-URL fetch.** Ports `url_guard.py`'s
  protections verbatim in spirit: the host must match `[web].fetch_allowlist`
  (suffix match); the URL must resolve to a **public** IP (private / loopback /
  link-local / metadata-endpoint addresses are refused — SSRF guard); the
  request is **pinned to the validated IP** (no DNS-rebinding) and **does not
  follow redirects**. A blocked host returns an actionable error naming the
  allowlist, never a silent failure.

**Why guards, not absence:** unlike `write`/`bash` (gated by the permission
prompt) and unlike a kernel sandbox (which can't domain-filter), web egress is
made safe by these deterministic guards. This is the same split the offload
repo documents — web is "direct but guarded," writes are "mediated."

**Residual channels (accepted):** the search *query* and the *prompt/URL* the
model emits are still outbound paths. Keep secrets out of prompts; the
allowlist + SSRF guard bound *where* data can go, not *what* the model decides
to send. For sensitive workspaces, set `web_search`/`web_fetch` to ASK.

### 4.7 File access vs. the sandbox
"Open/read files" and "run web search" must keep working *through* the
phase-2 `bash` sandbox ([§7](#7-permissions), [§13](#13-open-questions)) — the
sandbox confines **ambient** reads, it does not disable the agent. Concretely,
mirroring `run-local.sh`'s Seatbelt profile:
- **File reads are allowed within the workspace** (cwd subtree) and the
  per-task paths the user names; denied for ambient secret dirs (`~/.ssh`,
  `~/.aws`, keychains) unless explicitly added. `read_file`/`ls`/`grep`/`glob`
  operate on the allowed set; nothing here blocks normal file work.
- **Network egress stays open** at the kernel level (the sandbox can't
  domain-filter) and is bounded instead by the `web_fetch` allowlist + SSRF
  guard above. So `web_search`/`web_fetch` work even when `bash` is sandboxed.
- The sandbox's job is narrow: stop a `bash` command (or a misbehaving model)
  from reading `~/.ssh` and exfiltrating it — not to wall off the workspace.

---

## 5. Model server lifecycle

Ported into `locode/server/` (standalone). Mirrors the prior shell-based
server scripts' behavior.

### 5.1 Manager API (`server/manager.py`)
A common interface with **two implementations** chosen by `[serving].mode`
([§5.5](#55-serving-modes--concurrency)): `SingleGpuManager` (default) and
`PoolManager` (concurrent). The agent loop and `model/client.py` talk only to
this interface and a `route(model) -> Backend` resolver, so concurrency is
invisible above the server layer.
```python
manager.status()                 -> [Status(backend, up, model_id, host, port, pid)]
manager.ensure_up(alias=None)    -> ensure a backend is serving `alias`; wait /v1/models
manager.route(alias)             -> Backend(base_url) to send this request to
manager.start(alias, backend=…)  -> launch a server (mlx_lm.server, or [serving].start_cmd)
manager.stop(backend=…)          -> kill server+clients; (single-GPU) wait wired-mem to fall
manager.switch(alias)            -> single: stop()+start(); concurrent: route/warm, no evict
manager.list_served()            -> union of GET /v1/models across backends
```
- `ensure_up()` is called lazily on first model use; an already-serving
  endpoint is used as-is (no restart).
- **Single-GPU** `switch()` is explicit (a `/model` slash command or
  `locode --model`) and **evicts** the loaded model — never silent. Its
  **teardown wait** ports the `vm_stat` wired-memory poll from `mlx_stop`: MLX
  weights live in wired buffers, so RSS lies; we wait for wired memory to drop
  below a floor before starting the next model, preventing an OOM that can
  crash the machine.
- **Concurrent** `switch()` does **not** evict — it routes to (or warms) a
  backend that's already holding the model; the wired-memory wait is skipped
  (it's macOS-single-GPU-specific). Eviction happens only under a configured
  resident-model cap (LRU).

### 5.2 Alias resolution (`server/aliases.py` + config)
Aliases are a **pure user-config concern** — locode ships **no** built-in model
table (so it's portable to anyone's machine, with no dependency on an external
server repo). `server/aliases.py` is just the resolution *rule*:
`resolve(alias_or_id)` returns a value containing `/` verbatim (it's already a
full HF id), looks a bare name up in the built-in table (empty by default, kept
as an extension point), and otherwise raises pointing the user at `config.toml`.

The user's models live in `config.toml` `[aliases]`; `SingleGpuManager.resolve`
checks those first, then falls back to `aliases.resolve`, and
`SingleGpuManager.known_aliases()` lists the merged set (what `/models` shows).

**First run** (`scaffold.py`): when no `config.toml` exists, locode writes a
commented starter template (with a set of example MLX aliases + a `[model]
default`) and prints a one-line notice telling the user to edit it. Nothing is
auto-pulled until they actually send a turn, so the examples are a safe
starting point, not a commitment.

### 5.3 Model capability profiles (`server/memory.py` + `model/profiles.py`)
Each alias carries a profile:
```python
Profile(
  alias="qwen14",
  native_tools=True,        # trust server-side tool_calls?
  thinking_arg=False,       # pass {"enable_thinking": false}?
  prompt_cache_bytes=...,   # per-model wired budget (ported memory rules)
  tool_reliability="good",  # good|fair|poor -> drives harness aggressiveness
  notes="best dense Qwen; supports tools",
)
```
- **Memory budgets** port the reference scripts' per-model `prompt-cache-bytes`
  (e.g. 1GB for the 15GB gemma-27b, 1.5GB otherwise) and the **hard rule**:
  never serve a model that pushes wired memory past the ~20GB ceiling
  (the documented Qwen3-32B crash). The manager refuses unknown models above
  a size threshold with a clear warning.
- **`thinking_arg`** ports the `enable_thinking:false` chat-template kwarg for
  Qwen3 / gemma-4 (and the rule that 2507-Instruct and non-reasoning models
  must *not* receive it, or the template rejects the request).
- **`tool_reliability`** decides how hard the harness leans on native tool
  calls vs. the fenced-block fallback. `poor` ⇒ harness path primary; an
  explicit warning that agentic edits may be unreliable on this model, with a
  suggestion to `/model qwen14`.

### 5.4 Default model choice
Default to a **tool-reliable** model for an agentic tool (`qwen14`), overriding
the offload repo's `gemma12` default — gemma is a strong *reasoner* but a weak
*tool-caller*, the wrong tradeoff for an agentic coder. Configurable.

### 5.5 Serving modes & concurrency
`[serving].mode` selects how models are served. **`single` is the default** and
preserves today's Apple-Silicon behavior exactly; `concurrent` is opt-in for
hardware/backends that can hold more than one model or serve parallel requests.

| | `single` (default) | `concurrent` (opt-in) |
|--|--|--|
| Backends | one managed mlx server (`:8081`) | a **pool** of OpenAI-compatible endpoints (multiple ports/hosts/GPUs) |
| Resident models | one at a time | up to `max_resident` (LRU evict) |
| `switch()` | stop → wired-mem wait → start (evicts) | route/warm; no eviction unless over cap |
| Request parallelism | sequential (1 GPU) | up to `max_inflight` concurrent requests |
| Teardown wired-mem wait | yes (`vm_stat`) | skipped |
| Prompt cache | one shared prefix on `:8081` | per-backend prefix; router pins a model to a backend for cache stability |

**Backend pool.** In concurrent mode the manager owns a list of backends from
config — each is either *managed* (locode launches it: `mlx_lm.server` per GPU
via env like `CUDA_VISIBLE_DEVICES` / Metal device, or a custom `start_cmd`) or
*external* (already running; locode just routes to it). A **router** maps each
request's requested model → a backend that holds it (warming one on miss,
evicting LRU past `max_resident`). Because prompt-cache benefit is
prefix-locality, the router **pins a given model to a stable backend** so its
cache survives across turns.

**Where parallelism is used.** With `max_inflight > 1` the agent loop may issue
**independent** sub-requests concurrently — e.g. fanning out read-only
sub-queries, or running the context-summarization pass ([§4.5](#45-context--prompt-cache-discipline))
on a second backend without blocking the main turn. Tool execution that
mutates state stays serialized regardless. A bounded `asyncio` semaphore
(`max_inflight`) caps total in-flight model calls so a multi-GPU box isn't
oversubscribed.

**Multi-GPU shapes supported** (via config, not hardcoded):
- *One model per GPU* — N managed mlx/vLLM/llama.cpp servers, one per device;
  switch is instant routing. (Apple Silicon has one GPU, so this is really a
  Linux/CUDA or multi-host story; on a Mac the pool is typically one backend.)
- *One large model sharded across GPUs* — a single backend launched with
  tensor-parallel args (e.g. vLLM `--tensor-parallel-size`); locode treats it
  as one endpoint and passes the launch args through `start_cmd`.
- *External fleet* — point locode at a load-balanced OpenAI-compatible
  endpoint and set `mode=concurrent`, `backends=[{base_url=…, external=true}]`;
  locode does no lifecycle, only routing + the agentic harness.

**Safety carry-over.** The single-GPU memory ceiling rules ([§5.3](#53-model-capability-profiles))
apply **per backend**: locode won't co-resident two models on one Apple GPU
that together blow the wired limit — `max_resident` on a Metal backend is
effectively 1 unless the models are small enough, and the manager refuses a
warm that would exceed the budget rather than crashing the machine.

---

## 6. Interactivity

### 6.1 REPL (`ui/repl.py`)
Built on **`prompt_toolkit`**: multiline input (Enter submits, **Esc+Enter** for
a newline), history (persisted to `~/.local/state/locode/history`),
reverse-search, **slash-command tab-completion**, and a custom key-binding layer
for interrupt. The **input area is enclosed in a box** — a titled top rule
(`╭─ <model> ───`), a `│` left edge on each line, and a bottom rule (`╰───`)
printed on submit so the frame persists in scrollback — making it easy to tell
input from output. A **bottom status toolbar** shows `server up/down · ctx
~tokens · cwd` while composing. Streaming assistant output renders incrementally; with
`[ui].markdown` on it's line-buffered and lightly styled (```code blocks,
# headings, **bold**/`code`), and a dim per-turn trailer reports
`~tok · Ns · tok/s` (`[ui].timing`).

UX details that matter under local-model latency:
- **Wait spinner (`ui/spinner.py`).** The long silent gaps — cold model load, a
  `/model` switch (stop + wired-memory wait + start), and time-to-first-token —
  animate a spinner driven off the loop's `busy_start`/`busy_stop` and
  `assistant_start` events; a short initial delay means fast ops never flash it,
  and the first streamed token clears it.
- **Diff-preview approvals.** A `write_file`/`edit_file` ASK prompt shows the
  actual unified diff of the proposed change (`render.format_change`) above the
  yes/always/no selector — you approve a concrete change, not just a path.
- **Friendly errors.** A connection failure becomes "can't reach the model
  server at <url> — …", branching on managed vs. remote ([§8.1](#81-remote--non-local-model-endpoints)).
- **Tool events** render as compact colored lines (`render.format_run/_result`),
  and the raw ```tool fence a fenced-path model emits is suppressed from the
  stream (`render.StreamSink`). Color honors `$NO_COLOR` and non-TTY.
- `/retry` re-runs your last prompt (or `/retry <new text>`).

### 6.2 Interrupt / Esc (`agent/cancel.py`)
The hard requirement. Design:
- A `CancellationToken` threads through the model client and tool executor.
- **`Esc`** (and `Ctrl-C` once) sets the token: the streaming HTTP read loop
  checks it between chunks and closes the connection; an in-flight `bash` tool
  gets `SIGTERM`→`SIGKILL` to its process group; the loop unwinds to the
  prompt with a "⛔ interrupted" line. The **session survives** — history is
  intact, you can type the next turn.
- `Ctrl-C` twice in quick succession (or `Ctrl-D` at an empty prompt) exits.
- Because generation runs as an async task on `prompt_toolkit`'s event loop
  (runtime core is async-first, [§15](#15-development-workflow)) while the UI
  owns the keyboard, the keypress is observed even mid-stream. The model
  request carries a client-side timeout as a backstop.

### 6.3 Multiple-choice questions (`ui/choice.py` + `tools/ask.py`)
Two entry points to the same selector widget:
- **Model-initiated:** the `ask_user` tool. The model emits
  `ask_user(question, options[])`; the harness renders an arrow-key/numbered
  selector; the selection returns to the model as the tool result. This is the
  local analogue of Claude Code's `AskUserQuestion`.
- **Harness-initiated:** permission prompts ("Run this bash command? [y/once/
  always/no]") and model-switch confirmations reuse the same widget.

The widget supports single-select (v1) with numbered + arrow-key navigation
and an "other / free text" escape. Multi-select is a v2 extension.

### 6.4 Splash screen (`ui/render.py`)
On interactive start (suppressed in `-p`/pipe mode and when `--no-splash` or
`NO_COLOR`/non-TTY is detected), `locode` prints the banner, then a one-line
status row resolved at runtime (served model, server up/down, cwd):

```
██╗     ██████╗  ██████╗  ██████╗ ██████╗ ███████╗
██║     ██╔═══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
██║     ██║   ██║██║     ██║   ██║██║  ██║█████╗
██║     ██║   ██║██║     ██║   ██║██║  ██║██╔══╝
███████╗╚██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
        local-first agentic coding · mlx · :8081

  ● qwen14   ○ server: starting…   ~/Code/llm-cli      v0.1.0
  type a task, /help for commands, Esc to interrupt
```

The banner is a stored constant (`ui/banner.py`); the `█` block letters render
in the model-accent color when the terminal supports it and degrade to plain
mono otherwise. The status dots are live: `●` model loaded, `○` server
down/starting. A `--logo` hidden subcommand prints just the banner (handy for
README/screenshots).

### 6.5 Slash commands (`ui/slash.py`)
In-REPL commands (not model-visible):
| Command | Action |
|---------|--------|
| `/model [alias]`     | Show or switch the served model (triggers `switch()`). |
| `/models`            | List served + known aliases. |
| `/server [up\|down\|status\|restart]` | Manage backend(s); shows the pool in concurrent mode. |
| `/open <path>`       | Open a file in the local editor ([§6.7](#67-editor-integration-uieditorpy)). |
| `/diff [path]`       | Open the last proposed change (or a file's working diff) in the diff viewer. |
| `/clear`             | Reset conversation context (keep session). |
| `/save [name]` / `/resume [name]` | Persist / restore a session. |
| `/permissions`       | Show/edit per-tool permission policy. |
| `/cwd [path]`        | Show or change the agent's working directory. |
| `/cost` / `/tokens`  | Show token usage + timing for the session. |
| `/help`              | List commands. |

### 6.6 Non-interactive / pipe mode
`locode -p "prompt"` (or stdin pipe) runs **one** agent turn headless and
prints the result — for scripting and for use as a subagent runner. In this
mode, ASK-gated tools default to **deny** (no human to ask) unless
`--allow-tool write,bash` is passed, mirroring the offload repo's "supervised
vs. autonomous" distinction. Read-only tools still run. Editor integration is
**disabled** here (no interactive session to open into).

### 6.7 Editor integration (`ui/editor.py`)
Shell-out integration with the user's local editor — no IDE extension required.
Editor resolution: `[editor].command` → `$LOCODE_EDITOR` → `$VISUAL` →
`$EDITOR` → first found of `code -w`, `nvim`, `vim`, `nano`. Two uses:

- **Open a file** — `/open <path>`, or an `open_in_editor` affordance offered
  after the agent references/creates a file. Launches the editor on the path;
  if the editor supports a line target (`code -g file:line`, `vim +N`), jumps
  there. Non-blocking by default.
- **Review a diff** — when `[editor].open_diffs = true`, a `write_file`/
  `edit_file` proposal can be opened as a **diff** (proposed vs. on-disk)
  instead of (or alongside) the inline terminal diff preview. The diff tool is
  `[editor].diff_tool` if set, else auto-detected (`code --diff a b`,
  `git difftool --no-index`, or `$EDITOR` on a unified-diff temp file). With
  `wait = true` locode **blocks until the editor closes**, then applies the
  change *as the user saved it* — so the human can hand-edit the model's patch
  in their own editor before it lands. `/diff [path]` reopens the last proposal
  or shows a file's current working diff.

This rides the permission gate ([§7](#7-permissions)): "review in editor" is an
option alongside once/always/no on a `write`/`edit` prompt. Apply-after-save
reads the file back post-close, so edits made in the editor are honored. If no
editor resolves (e.g. headless), it degrades to the inline terminal diff. macOS
`open`/Linux `xdg-open` are used only as a last-resort for non-text or when no
text editor is configured.

---

## 7. Permissions (`permissions.py`)

A three-state gate per tool call: **AUTO** (run silently), **ASK** (prompt),
**DENY** (refuse). Resolution order:
1. Explicit CLI/session override (`--allow-tool`, `/permissions`).
2. Config policy file (`config.toml` `[permissions]`).
3. Tool's default (`read_*`=AUTO, `write/edit/bash`=ASK).

Prompts offer **once / always(session) / no / no(always)**; "always" updates
the in-memory session policy. `bash` additionally shows the exact command;
`write/edit` show a **diff preview** before applying and offer **review in
editor** ([§6.7](#67-editor-integration-uieditorpy)) — open the proposed diff
in the local editor and apply it as saved. Path-scoped rules
(e.g. auto-allow writes under `./sandbox/`, never under `~/.ssh`) port the
spirit of `offload-policy.json`'s `auto_allow_under` / `deny_paths`. A global
`--yolo` / `dangerously_skip_permissions` flag flips ASK→AUTO for trusted
runs (loudly warned), matching Claude Code's bypass mode.

> v1 does **not** sandbox `bash` at the kernel level. The macOS `sandbox-exec`
> read-confinement from `run-local.sh` is a documented **phase-2** hardening
> ([§13](#13-open-questions)); v1 relies on the permission gate + diff preview.

---

## 8. Configuration (`config.py`)

XDG paths:
- Config: `~/.config/locode/config.toml`
- State/history/sessions: `~/.local/state/locode/`
- Logs: `~/.local/state/locode/logs/`

`config.toml` (all keys optional; shown with defaults):
```toml
[server]
host = "127.0.0.1"     # hostname or IP of the model server
port = 8081
scheme = "http"        # "http" | "https" (https for a remote/proxied endpoint)
base_url = ""          # full override, e.g. "https://gpu-box.lan:8081"; wins over
                       # scheme/host/port. Empty => built from scheme://host:port.
auto_start = true      # launch a LOCAL mlx server if none is reachable (managed only)
manage = "auto"        # own the server process? "auto" (yes iff loopback) | "yes" | "no"
mlx_bin = "/opt/homebrew/bin/mlx_lm.server"   # auto-detected on Linux

[serving]
mode = "single"          # "single" (default, 1 Apple GPU) | "concurrent"
# --- concurrent mode only (ignored when mode = "single") ---------------
max_resident = 1         # models kept warm across the pool (LRU evict past this)
max_inflight = 1         # max parallel model requests (asyncio semaphore)
# Backend pool. Omit to auto-manage one mlx server on [server].host:port.
# Each backend is managed (locode launches start_cmd) or external (route only).
backends = [
  # { base_url = "http://127.0.0.1:8081", managed = true,
  #   start_cmd = "mlx_lm.server --port 8081", device = "gpu:0" },
  # { base_url = "http://127.0.0.1:8082", managed = true,
  #   start_cmd = "CUDA_VISIBLE_DEVICES=1 vllm serve ... --port 8082" },
  # { base_url = "http://fleet.internal:8000", external = true },
]

[model]
default = "qwen14"
max_tokens = 4096
temperature = 0.3

[agent]
max_iterations = 25
max_wallclock_seconds = 600
context_summarize_at = 0.8        # fraction of window before summarizing

[permissions]
read_file  = "auto"
write_file = "ask"
edit_file  = "ask"
bash       = "ask"
web_search = "ask"                 # shows the query before spending a Tavily call
web_fetch  = "auto"                # already bounded by allowlist + SSRF guard
auto_allow_under = ["./sandbox"]
deny_paths = ["~/.ssh", "~/.aws", "~/.config/locode"]

[web]
search_provider = "auto"           # "auto" (keyed if set, else keyless DuckDuckGo)
                                   #   | "tavily" | "brave" | "duckduckgo"
tavily_api_key = ""                # else read from $TAVILY_API_KEY
brave_api_key  = ""                # else read from $BRAVE_API_KEY
# DuckDuckGo needs no key (keyless default). max_results caps returned hits.
max_results = 5
fetch_allowlist = [               # host suffixes web_fetch may reach
  "github.com", "raw.githubusercontent.com",
  "pypi.org", "docs.python.org",
]
# Private/loopback/link-local/metadata IPs are ALWAYS refused regardless of
# this list (SSRF guard); redirects are never followed.

[editor]
command = ""                       # else $LOCODE_EDITOR, $VISUAL, $EDITOR
open_diffs = true                  # open write/edit proposals as a diff to review
diff_tool = ""                     # e.g. "code --diff", "git difftool"; auto-detect if empty
wait = true                        # block until the editor closes (review-then-apply)

[ui]                               # interactive REPL polish (TTY only; honors $NO_COLOR)
markdown = true                    # line-buffered markdown styling of answers (--no-markdown)
spinner  = true                    # animated wait for model load / first token
timing   = true                    # per-turn "~tok · Ns · tok/s" trailer

[aliases]                          # YOUR models (no table is shipped in code)
qwen14  = "mlx-community/Qwen3-14B-4bit"
mymodel = "org/Some-Model-4bit"
```
A full `org/model` id always works with no alias defined. The whole `[aliases]`
block is scaffolded with examples on first run (see [§5.2](#52-alias-resolution-serveraliasespy--config)).

Precedence: CLI flags > env (`LOCODE_*`) > `config.toml` > built-in defaults.

### 8.1 Remote / non-local model endpoints
The server need not be on `localhost`. Point locode at any host/IP (and port),
including an `https` reverse proxy, three ways (highest precedence first):

- **CLI:** `--base-url https://gpu-box:8081` (or `--host 10.0.0.5 --port 8000`).
- **Env:** `LOCODE_BASE_URL=https://gpu-box:8081` (one-shot), or the granular
  `LOCODE_HOST` / `LOCODE_PORT` / `LOCODE_SCHEME`; `LOCODE_MANAGE_SERVER=auto|yes|no`.
- **Config:** `[server].host/port/scheme/base_url/manage`.

**Managed vs. remote.** `manage` decides whether locode owns the server
*process*. In `"auto"` (the default) a **loopback** endpoint is managed —
`ensure_up` will launch a local `mlx_lm.server`, and `/model` switching
stops/starts it (with the wired-memory wait). A **non-loopback** endpoint
(or `manage = "no"`, or any `base_url` to a remote host) is treated as
**remote/unmanaged**: locode only *uses* it — it never launches a process, never
`pkill`s (so it can't disturb unrelated local servers), and `ensure_up` against
a down endpoint returns an actionable error instead of trying to start one.
`/model` may **route** to a model the remote already serves but cannot evict/load
one (that's the remote's job). Set `manage = "yes"` to force local management of
a non-loopback bind (e.g. `0.0.0.0`). The model **client** (`model/client.py`)
is endpoint-agnostic — it just talks to `Config.base_url` — so remote use needs
no client changes.

---

## 9. Model client (`model/client.py`)

- Sends each request to the backend the manager's `route(model)` resolves —
  `http://127.0.0.1:8081/v1` in single mode, or the routed pool member in
  concurrent mode (always a local/configured OpenAI-compatible endpoint, never
  a cloud one). Pure stdlib `urllib` is sufficient (as `post-local.py` proves)
  but we use `httpx` for clean streaming + timeout + cancellation, and its
  async client backs `max_inflight` parallel calls in concurrent mode. One
  dependency, worth it for interrupt + concurrency support.
- `complete(messages, tools, stream=True)` yields token deltas and a final
  assembled message (content + any `tool_calls`).
- Strips the `mlx:` client prefix from model ids if present (as `post-local.py`
  does).
- Handles the reasoning-model fallback: if `message.content` is empty but
  `reasoning` is present (thinking left on), surface `reasoning` rather than
  erroring (ported from `post-local.py`).
- Per-request client timeout (default 600s) as an interrupt backstop;
  cancellation closes the stream immediately.

---

## 10. Install & upgrade

**Distribution: PyPI is the primary channel; a git/source path exists for
development.** Released to PyPI as `locode`; the repo stays installable
directly for contributors and bleeding-edge users. Both paths land in an
**isolated environment** and write an install-method marker so `upgrade`
knows how it was installed.

### 10.1 End-user install (PyPI) — `install.sh`
A single curl-able script (`curl -fsSL …/install.sh | bash`) that:
1. Checks prerequisites: Python ≥3.10, and on macOS `mlx_lm`
   (`pip install 'mlx-lm>=0.31'` if absent; on Linux, prints guidance to run
   any OpenAI-compatible server on `:8081`).
2. Installs **from PyPI** in an isolated environment — prefer `pipx install
   locode` (or `uv tool install locode`); fall back to a dedicated venv at
   `~/.local/share/locode/venv` (`pip install locode`) with a shim in
   `~/.local/bin/locode`. Writes `~/.local/share/locode/install-method`
   (`pipx` | `uv` | `venv`).
3. Puts `locode` on `PATH` (`~/.local/bin`), warns if that dir isn't on PATH.
4. Writes a default `config.toml` if none exists.
5. Prints next steps (`locode` to start; first run auto-pulls the default
   model via mlx on first generation).

Power users can skip the script: `pipx install locode`. Pin a version with
`locode==X.Y.Z`. Idempotent and re-runnable.

### 10.2 Developer install (git/source)
For contributors and pre-release testing — no PyPI round-trip:
```bash
git clone <repo> && cd locode
pipx install --editable .        # or: uv tool install --editable .
#   plain venv alt:  python -m venv .venv && .venv/bin/pip install -e .
```
`install.sh --dev [--ref <branch|tag>]` automates this: clones (or uses the
cwd if already a checkout), installs **editable** from source, and writes the
install-method marker as `git` with the checkout path. Editable means code
edits are live without reinstall. Same `locode` entrypoint either way.

### 10.3 Upgrade — `locode upgrade`
A built-in subcommand (the reason install uses an isolated env + marker). It
reads `~/.local/share/locode/install-method` and dispatches:
- `pipx`  → `pipx upgrade locode`
- `uv`    → `uv tool upgrade locode`
- `venv`  → `pip install -U locode` into the managed venv
- `git`   → `git -C <checkout> pull` then re-sync the editable install
- `locode upgrade --check` reports current vs. latest (PyPI for released
  installs; `git fetch` ahead/behind for dev checkouts) without applying.
- `locode upgrade --pre` opts into pre-release versions on the PyPI paths.
- Version is `locode --version`; `CHANGELOG.md` summarizes releases.

### 10.4 Release flow (maintainer)
Tag `vX.Y.Z` → CI builds an sdist + wheel and publishes to PyPI (trusted
publishing / OIDC, no stored token). `pyproject.toml` is the version source
(single-sourced into `locode.__version__`). Semantic versioning; pre-releases
(`X.Y.ZrcN`) reach only `--pre` users.

### 10.5 Uninstall
`locode uninstall` (or documented manual steps): removes the venv/pipx pkg,
the `~/.local/bin/locode` shim; leaves user config/sessions unless `--purge`.

---

## 11. Cross-platform notes

- **macOS / Apple Silicon:** the primary target. MLX server, `vm_stat`-based
  wired-memory teardown wait, per-model memory budgets all apply. One GPU ⇒
  `single` serving mode is the natural default; `concurrent` here usually means
  one backend (co-residency is bounded by the wired limit).
- **Linux / multi-GPU:** MLX is Apple-only, so the manager targets *any*
  OpenAI-compatible backend (llama.cpp `llama-server`, vLLM, Ollama's OpenAI
  endpoint). The manager's "start" is configurable (`[serving].backends[].start_cmd`);
  the wired-memory poll is macOS-specific and skipped. This is where
  `concurrent` mode pays off: one managed backend per GPU
  (`CUDA_VISIBLE_DEVICES`), or one tensor-parallel backend across GPUs (vLLM
  `--tensor-parallel-size`), or an external fleet — see [§5.5](#55-serving-modes--concurrency).
  The **client, agent loop, tools, and UI are platform-agnostic.**
- Process-group signaling for `bash` cancellation uses POSIX `os.killpg`
  (works on both). The editor shell-out ([§6.7](#67-editor-integration-uieditorpy))
  uses `$EDITOR`/`code`/`xdg-open` (Linux) / `open` (macOS). No Windows support
  (the requirement is unix/linux/mac).

---

## 12. Testing & safety

`pytest`; tests in `tests/` mirror the package. **Tests never touch the network
or `:8081`** — the model client is exercised against a mocked HTTP transport.

- **Unit:** `toolparse` against captured good/malformed model outputs per
  alias; permission resolution; alias resolution; config precedence;
  `edit_file` exact-match + path scoping; client message assembly + tool-call
  parsing (mocked transport).
- **Integration (live, opt-in):** with `:8081` up, a real agent turn that
  reads a file and proposes an edit round-trips end-to-end. Auto-skips if the
  server is down (mirrors `test-suite.sh --no-live`).
- **Safety invariants:** read-only tools never mutate; ASK tools never run
  without approval in interactive mode and never run in `-p` mode without an
  explicit `--allow-tool`; `deny_paths` are honored even under `--yolo`.
- **Cancellation:** a test that a long generation + a long `bash` both abort
  on the cancellation token within a bounded time and leave the session usable.

Functional code lands **with its tests in the same change**, and `pytest -q`
runs green before a task is called done.

---

## 13. Open questions / deferred

> **MVP note.** The sub-questions below (sandbox allow-set scope, OIDC vs.
> token, summarization model, IDE depth, concurrency defaults, multi-select,
> offload-repo sharing) are **deferred** — none blocks the M1–M3 MVP. They're
> tracked here to be revisited as their milestone comes up.

1. ~~**Name.**~~ **Resolved: `locode`.** Baked into the entrypoint, config dir
   (`~/.config/locode`), package name, and PyPI project.
2. **`bash` sandboxing (accepted as phase-2).** v1 gates via permissions +
   diff preview only; phase-2 ports the `sandbox-exec` (macOS Seatbelt)
   read-confinement from `run-local.sh` (Linux would need
   bubblewrap/namespaces — bigger lift). **Hard requirement on that work:** the
   sandbox must confine only *ambient* secret reads — workspace file access and
   web egress (`web_search`/`web_fetch`) keep working through it (see
   [§4.6](#46-external-access-web-tools)–[§4.7](#47-file-access-vs-the-sandbox)).
   Open sub-question: how to scope the workspace allow-set — cwd subtree only,
   or also the user-named `--read-root`/`-f` paths as the offload runner does.
3. ~~**Distribution.**~~ **Resolved: PyPI primary + git/source for dev** — see
   §10. Open sub-question: trusted-publishing (OIDC) vs. a stored PyPI token
   for the release CI.
4. **Context summarization model.** Summarize-on-overflow with the *same*
   served model (simple, but evicts nothing) vs. a tiny model like `qwen06`.
   On single-GPU a separate model means a costly switch ⇒ lean: same model. In
   `concurrent` mode this is free — run the summarizer on a second backend
   ([§5.5](#55-serving-modes--concurrency)).
5. **IDE integration depth.** v1 is shell-out (`/open`, diff review in
   `$EDITOR`/VS Code — [§6.7](#67-editor-integration-uieditorpy)). A deeper
   integration (a VS Code extension with an inline chat panel, or an LSP/MCP
   bridge so an editor drives `locode`) is future work — which surface first?
6. **Concurrent-mode sub-questions.** (a) Default `max_resident`/`max_inflight`
   when a user sets `mode=concurrent` without tuning — start at 1/1 (safe) and
   make them raise explicitly, or infer from detected GPUs? (b) Router policy
   on a cache-cold backend: prefer prompt-cache locality (pin) vs. load-balance
   for latency. Lean: pin for cache, since prefill dominates locally.
7. **Multi-select questions** and **richer TUI** (split panes, persistent
   tool-output pane) deferred to v2.
8. **Session sharing with the offload repo.** Should `locode -p` register
   itself as the `__RUNNER__` for the existing `local-offload` subagent, so
   the two ecosystems converge? Possible later; standalone for now.

---

## 14. Milestones

- **M1 — Conversational core:** ✅ model client + REPL + streaming +
  Esc/Ctrl-C interrupt + `SingleGpuManager` lifecycle (status/ensure_up/
  start/stop/switch).
- **M2 — Tool harness:** ✅ tool registry, `read_file`/`ls`/`grep`/`glob`,
  tolerant `toolparse` (native + fenced + salvage), agent loop with
  iteration/wallclock budgets + malformed-call nudge.
- **M3 — Mutating tools + permissions:** ✅ `write_file`/`edit_file`/`bash`
  (process-group cancel), permission gate, `ask_user` multiple-choice, editor
  `/open` + diff helpers ([§6.7](#67-editor-integration-uieditorpy)). Streaming
  tool-fence suppression done (`ui/render.py` `StreamSink`). _Deferred: inline
  diff preview in the ASK prompt._
- **M4 — Web tools:** ✅ `web_search` (Tavily, ASK, self-disables w/o key) +
  hardened `web_fetch` (`tools/web.py`: allowlist + resolve-all-public SSRF
  guard + IP-pinned connection w/ SNI + no redirects + size cap). Inline,
  arrow-navigable approval UX and compact colored tool/result rendering.
- **M5 — Install/upgrade + polish:** ✅ `install.sh` (PyPI + `--dev` + `--pre`
  + `--dry-run`), `locode upgrade` (`--check`/`--pre`), `locode uninstall`
  (`--purge`), install-method marker (`install.py`), version single-sourcing,
  `-p` headless mode, sessions persistence (`session.py`, `/save`+`/resume`),
  `CHANGELOG.md`.
- **M6 — Concurrency:** `PoolManager` + router, `[serving] mode=concurrent`,
  `max_resident`/`max_inflight`, multi-backend `/server` status.
- **M7 — Hardening (phase 2):** `bash` sandboxing, context summarization,
  capability-profile tuning per model.

---

## 15. Development workflow

- **MVP scope = M1–M3** (conversational core → tool harness → mutating tools +
  permissions + editor). M4–M7 follow. Web tools, install/upgrade, concurrency,
  and sandboxing are explicitly out of the first cut.
- **Runtime core = async-first** (`asyncio` + `httpx.AsyncClient` +
  `prompt_toolkit`'s native event loop). Generation is a cancellable task; Esc
  sets a cancel event observed between stream chunks. This is also the substrate
  for the M6 `max_inflight` semaphore. (Decided 2026-06-24.)
- **Tests ship with code** (`pytest`, `tests/` mirrors the package; HTTP
  mocked). `pytest -q` green before a task is done.
- **No git yet** — repo is created later; until then, no git operations.
