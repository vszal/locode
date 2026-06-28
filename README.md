# locode

A Claude Code–style **agentic CLI for local LLMs** served by an
OpenAI-compatible endpoint (e.g. [`mlx-lm`](https://github.com/ml-explore/mlx-lm)
on Apple Silicon). It reads, writes, and edits files, runs shell commands, and
can ask you multiple-choice questions — all driven by an on-device model, with a
**tolerant tool-use harness** built for local models that function-call
unreliably (mis-escaped JSON, fenced vs. native tool calls, flat vs. nested
argument schemas).

> ### 🚧 Work in progress
> locode is under active development and **not yet packaged for general use.**
> **Installation instructions are coming soon.** The notes below describe a
> from-source development setup and are subject to change.

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

## Development setup (from source)

```bash
git clone https://github.com/vszal/locode.git
cd locode
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

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

Early MVP, under active development. Packaged installation, expanded web
tooling, concurrency, and `bash` sandboxing are on the roadmap, and interfaces
may change.
