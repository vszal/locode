# Choosing and driving local models

Practical guidance for picking a local model per task. Aliases live in
`~/.config/locode/config.toml`; any value containing `/` is treated as a full
Hugging Face id and works without an alias. The model *traits* below matter more
than any specific model — substitute whatever you've pulled.

## Principles

- **Match the model to the task.** A model that reasons well about code does not
  necessarily *apply edits* reliably, and vice versa.
- **On local hardware the bottleneck is usually memory, then tool-call
  reliability — rarely raw intelligence.** A model that fits comfortably in
  memory and emits clean tool JSON will out-deliver a bigger, "smarter" one that
  thrashes or mangles its edits.
- **Prefer one model that both analyzes and edits** for everyday work. Only split
  "plan with model A, apply with model B" when a task is big enough to justify
  the model-swap cost (see below).

## Editor vs. planner

Two rough roles a local coding model can play:

- **Editor** — applies multi-step `edit_file` / `write_file` changes. This needs
  clean, correctly-escaped tool JSON. Many models mis-escape quotes or newlines
  inside code payloads and corrupt edits; locode's tolerant parser recovers a
  lot, but a model with naturally clean JSON output is far nicer to edit with.
- **Planner / analyst** — reads and reasons (`ls`, `read_file`, `grep`) and
  produces prose: refactor plans, code review, "what's the best way to…". These
  calls carry no code payloads, so even a model with weak edit-JSON hygiene is
  reliable here.

A model can be strong in one role and weak in the other. When evaluating a
candidate, run it on a real edit and watch whether its `edit_file` calls apply
cleanly.

## Tool-call format gotchas

Local models disagree on *how* they emit tool calls, and the wrong setting can
make a capable model look broken:

- Some expect a **native** tool protocol (the OpenAI `tools` param, rendered into
  the model's own chat template); others do best with locode's **fenced**
  ` ```tool ` blocks. Mixing the two can yield empty responses or garbled,
  hybrid output. Each model's capability profile sets `native_tools` accordingly
  — if a model returns empty or malformed calls, try flipping it.
- Argument schemas also vary (nested `{"args": {…}}` vs. flat
  `{"name": …, "path": …}`); locode tolerates both.
- **Reasoning models can look like they hang.** A model that emits chain-of-thought
  in a separate `reasoning` field (e.g. Qwythos-9B) produces no `content` while it
  thinks — locode streams only `content`, so a long think shows nothing, and if it
  hits the token cap `content` comes back empty and the turn fails. Set such a
  model's profile `thinking_arg=True` so locode launches the server with
  `enable_thinking=false`; turns that spent seconds "thinking" then drop to instant,
  direct answers. Verify with `scripts/model_reliability_probe.sh <alias>`.
  Built-in profiles cover the known models, but you can override the launch-time
  decision per model in `config.toml` without touching source:
  ```toml
  [thinking]
  # alias or model-id substring -> "on" | "off" | "auto"
  qythos9 = "off"      # suppress (enable_thinking=false)
  "gpt-oss" = "off"
  devstral24 = "on"    # force enable_thinking=true
  ```
  `"on"`/`"off"` force the kwarg; `"auto"` omits it entirely (template default),
  which is how you *undo* a profile that bakes in `enable_thinking=false`. An
  unlisted model keeps its profile default. This is the user-facing knob for the
  same mechanism the profiles use internally.

## Driving a weak edit-JSON model (keep it in read+reason mode)

If a model reasons well but mangles edits, use it as a planner and tell it
explicitly **not to edit**:

> Read `<file>` and any related files. Do NOT modify anything. Produce a numbered
> plan: for each problem, give the location, why it's wrong, and the concrete
> change to make (described in words). Order the steps so they don't conflict.

This keeps it in read+reason mode and sidesteps the edit-JSON weakness entirely.

## Plan → execute handoff

`/model` swaps the served model **but keeps your conversation context** (it does
not clear history — that's `/clear`). So a plan made by one model is visible to
the next:

1. Plan with your strongest reasoner (prompt above).
2. `/model <editor>` — context carries over.
3. "Implement the plan above. Make the edits." — the editor applies it with
   clean, approve-able diffs.

**Cost caveat:** on a single GPU, switching models evicts and reloads weights
(often a minute or more each way, plus a prompt-cache re-warm). Only split
plan/execute across models when the task is big enough to be worth it; for
small/medium changes, stay on one model.

## Memory headroom on single-GPU setups

On a single shared-memory GPU (e.g. Apple Silicon), the model weights, the KV
cache, and transient working buffers must all fit at once. A model sized right at
your memory ceiling may handle a short prompt but crash on a larger agentic
context. Leave headroom: the largest model that fits *comfortably* beats a bigger
one that sits at the edge.
