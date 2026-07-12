# locode

A Claude Code–style **agentic CLI for local LLMs** served by an
OpenAI-compatible endpoint (e.g. [`mlx-lm`](https://github.com/ml-explore/mlx-lm)
on Apple Silicon). It reads, writes, and edits files, runs shell commands, and
can ask you multiple-choice questions — all driven by an on-device model, with a
**tolerant tool-use harness** built for local models that function-call
unreliably (mis-escaped JSON, fenced vs. native tool calls, flat vs. nested
argument schemas).

> ### 🚧 Work in progress
> locode is under active development. It's **installable today** (see
> [Install](#install)), but still early — interfaces may change.

## Why

Local models are cheap and private, but they call tools inconsistently. locode
wraps a local model server with a parser and agent loop that recover usable tool
calls from messy output, plus a permission layer so an autonomous model can't
touch sensitive paths.

## Features

- **Tolerant tool parsing** — handles native `tool_calls`, fenced ` ```tool `
  blocks, and best-effort salvage of malformed or mis-escaped JSON.
- **Filesystem, shell, and web tools** — `read_file`, `ls`, `glob`, `grep`,
  `write_file`, `edit_file`, `move_file`, `bash`, plus optional web search/fetch.
- **Permission layer** — read-only tools run automatically; mutating tools
  prompt; configurable `deny_paths` are hard-blocked even under `--yolo`.
- **Server lifecycle management** — starts/stops a local model server with
  per-model memory budgeting for single-GPU setups.
- **Interactive REPL and headless one-shot** modes.

## Requirements

- Python ≥ 3.10
- A local OpenAI-compatible model server. On Apple Silicon, that's
  [`mlx-lm`](https://github.com/ml-explore/mlx-lm):

  ```bash
  pip install mlx-lm
  mlx_lm.server --model <hf-model-id> --port 8081
  ```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/vszal/locode/main/install.sh | bash
```

`install.sh` clones the repo to `~/.local/share/locode/src` and installs locode
from it (via `pipx`, else `uv`, else `pip install --user`), recording how so
updates are one command.

> **Note:** PyPI publishing is deferred (the `locode` name on PyPI is an
> unrelated package), so for now the installer pulls from this git repo rather
> than from PyPI. `locode upgrade` does a `git pull` + reinstall.

```bash
locode upgrade            # update in place (git pull + reinstall)
locode upgrade --check    # show the install method + what it would run
locode uninstall          # remove it (add --purge to drop config/state too)
```

Run `./install.sh --help` for `--dev` (editable install from a checkout) and
`--dry-run`.

First run writes a short starter config to `~/.config/locode/config.toml`
(edit the model aliases to match what you've pulled). See
[`config.toml.example`](config.toml.example) for every available option and
its default.

## Development setup (from source)

```bash
git clone https://github.com/vszal/locode.git
cd locode
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
# or, equivalently:  ./install.sh --dev

# In another shell, start a local model server (see Requirements), then:
.venv/bin/locode                          # interactive REPL
.venv/bin/locode -p "summarize cli.py"    # headless, one turn
```

## Usage

- **Interactive:** `locode` — streaming output, `Esc`/`Ctrl-C` to interrupt a
  running turn, and slash commands (`/model`, `/server`, `/open`, `/clear`,
  `/help`, …).
- **Headless:** `locode -p "<task>"` (or pipe via stdin). ASK-gated tools
  (`write_file`, `edit_file`, `bash`) are denied unless pre-allowed with
  `--allow-tool write_file,bash`; read-only tools always run.
- **Model selection:** `locode -m <alias-or-hf-id>`. Define short aliases in
  your config; any value containing `/` is treated as a full Hugging Face id.
- **`--yolo`** flips ASK→AUTO (configured `deny_paths` are still enforced).
- **Reasoning toggle:** some local models emit chain-of-thought that locode
  can't stream (it looks like a hang). Override per model in `config.toml`:
  ```toml
  [thinking]
  # alias or model-id substring -> "on" | "off" | "auto"
  qythos9 = "off"   # suppress reasoning (enable_thinking=false)
  devstral24 = "on" # force it on for hard diagnosis
  ```
  Unlisted models use locode's per-model default; `"auto"` omits the kwarg and
  lets the model's own template decide.

- **Turn budget:** a turn runs until the model stops calling tools or a budget
  trips. For non-native (fenced ` ```tool `) callers the loop grounds one call
  per iteration, so `max_iterations` is roughly one file read/edit/test-run
  per count, not one logical step — a multi-file task can need dozens.
  Runaway loops are caught separately (`max_repeat_calls`, `max_error_stall`),
  so raising this only extends a turn that's still making progress:
  ```toml
  [agent]
  max_iterations = 50   # default; bump for large multi-file tasks
  ```

See [`MODELS.md`](MODELS.md) for guidance on choosing a local model per task and
[`architecture.md`](architecture.md) for the design.

## Permissions

Read-only tools are AUTO; mutating tools (`write_file`, `edit_file`,
`move_file`, `bash`) are ASK. Writes under `./sandbox` are auto-allowed, and
`deny_paths` (e.g. `~/.ssh`, `~/.aws`) are hard-denied even under `--yolo`.
Configurable in `~/.config/locode/config.toml`.

## Tests

```bash
.venv/bin/python -m pytest -q   # no network — the model server / HTTP is mocked
```

## Status

Early MVP, under active development. Packaged installation (`install.sh` +
`locode upgrade`/`uninstall`), web tools, and the permission model are in place;
concurrency (multi-model serving) and `bash` sandboxing remain on the roadmap,
and interfaces may change.
