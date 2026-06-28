# locode

A Claude Code-style **agentic CLI for local LLMs** served by `mlx_lm.server` on
`:8081`. Reads/writes/edits files, runs shell commands, and asks you
multiple-choice questions — all driven by an on-device model, with a tolerant
tool-use harness built for models that function-call unreliably.

See [`architecture.md`](architecture.md) for the full design,
[`MODELS.md`](MODELS.md) for which local model to use per task (planning vs.
editing) and how to drive them, and [`AGENTS.md`](AGENTS.md) for the
contributor/delegation policy.

> **Status:** MVP (milestones M1–M3). Web tools, install/upgrade packaging,
> concurrency, and `bash` sandboxing are later milestones.

## Quick start (dev)

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
# Ensure a local server is up (Apple Silicon):
#   pip install mlx-lm && mlx_lm.server --model lmstudio-community/Devstral-Small-2507-MLX-4bit --port 8081
.venv/bin/locode                       # interactive REPL
.venv/bin/locode -p "summarize main.py"  # headless, one turn
```

## Usage

- **Interactive:** `locode` — splash, prompt, streaming output, `Esc`/`Ctrl-C`
  to interrupt a running turn, slash commands (`/model`, `/server`, `/open`,
  `/clear`, `/help`, …).
- **Headless:** `locode -p "<task>"` (or pipe stdin). ASK-gated tools
  (`write_file`, `edit_file`, `bash`) are denied unless pre-allowed with
  `--allow-tool write_file,bash`. Read-only tools always run.
- **Model:** `locode -m qwencoder30` (alias or full HF id). `devstral24` default
  — see [`MODELS.md`](MODELS.md) for picking a model per task.
- **`--yolo`** flips ASK→AUTO (deny_paths still enforced).

## Permissions

Read-only tools are AUTO; mutating tools (`write_file`, `edit_file`, `bash`)
are ASK. Writes under `./sandbox` are auto-allowed; `deny_paths` (`~/.ssh`,
`~/.aws`, …) are hard-denied even under `--yolo`. Configurable in
`~/.config/locode/config.toml`.

## Tests

```bash
.venv/bin/python -m pytest -q   # 82 tests; no network (HTTP is mocked)
```
