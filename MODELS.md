# Choosing and driving local models

Practical guidance for picking a model per task on Apple Silicon (single GPU,
~20 GB wired ceiling on a 24 GB machine). Aliases live in
`~/.config/locode/config.toml`; a value containing `/` is a full HF id and works
without an alias.

## TL;DR

- **Default to `devstral24` for almost everything.** It both *analyzes* and
  *edits* reliably — for most "analyze X and fix it" prompts you never switch.
- **Keep `qwencoder30` as a planner/analyst**, not an editor. Reach for it only
  on *large/architectural* work where its planning depth earns the model-swap
  cost; then hand off to `devstral24` to apply the changes.

## The two workhorses

### `devstral24` — the editor (default)
`lmstudio-community/Devstral-Small-2507-MLX-4bit` · ~13 GB · Mistral agentic
coder. Emits clean, **correctly-escaped** tool JSON and applies multi-step edits
without corruption. This is the one to make actual changes with.

> Profile note: it is configured **fenced-only** (`native_tools=False`).
> Passing the OpenAI `tools` param makes `mlx_lm` render Mistral's
> `[AVAILABLE_TOOLS]` protocol, which conflicts with locode's `\`\`\`tool` prompt
> and returns an **empty** response. Fenced-only avoids that and works cleanly.

### `qwencoder30` — the planner/analyst
`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` · ~17 GB (MoE, ~3B active, so
fast) · strong agentic reasoner. **Caveat:** it mis-escapes quotes/newlines
inside `edit_file`/`write_file` code payloads, so it's unreliable at *applying*
edits (locode's tolerant parser recovers a lot, but not everything). Its
read/navigate calls (`ls`, `read_file`, `grep`) are clean — those args have no
embedded quotes — so it's great for **read + reason** work whose output is prose.

Good uses: architecture/refactor planning, code review, "what's the best way
to…", multi-file analysis. Avoid using it to make the edits.

## Prompt style for `qwencoder30` (keep it out of its failure zone)

Tell it explicitly **not to edit**, and ask for structured output:

> Read `kivy_toe.py` and any related files. Do NOT modify anything. Produce a
> numbered refactor plan: for each problem, give the location, why it's wrong,
> and the concrete change to make (described in words). Order the steps so they
> don't conflict.

This keeps it in read+reason mode and sidesteps the edit-JSON weakness entirely.

## Plan → execute handoff

`/model` swaps the served model **but keeps your conversation context** (it does
not clear history; that's `/clear`). So a plan made by one model is visible to
the next:

1. Plan with `qwencoder30` (prompt above).
2. `/model devstral24` — context carries over.
3. "Implement the plan above. Make the edits." — `devstral24` executes it with
   clean, approve-able diffs.

**Cost caveat:** on a single GPU, switching models evicts and reloads weights
(~1–2 min each way, plus a prompt-cache re-warm). So only split plan/execute
across models when the task is big enough to be worth it. For small/medium
changes, stay on `devstral24` and do the whole thing in one model.

## Other aliases (context)

From the starter config — edit to match what you've pulled:

| alias         | role                                  |
|---------------|---------------------------------------|
| `devstral24`  | **edit + analyze (default)**          |
| `qwencoder30` | large-refactor planning / analysis    |
| `qwencoder14` | lighter coder; decent edits           |
| `qwen14`      | general dense Qwen; reliable tool use  |
| `qwen4i`      | fast trivial first-pass               |
| `gemma12` / `gemma27` | strong reasoners, **weak tool-callers** — not for edits |

To make `devstral24` the startup model, set `default = "devstral24"` under
`[model]` in `~/.config/locode/config.toml`.
