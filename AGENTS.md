# AGENTS.md — working agreement for building `locode`

Policy for any agent (human or model) contributing to this repo. The headline
rule: **push work to the cheapest tier that can do it well, and keep only
genuinely hard judgment on the top tier.** See `architecture.md` for the design.

## Tiered delegation

Route each task to the lowest-cost tier that can do it *correctly*, then verify
up the chain. Three tiers:

### Tier 1 — local model (cheapest; on-device, free)
Defer **easy, well-specified, low-blast-radius** work to the *best local model
for the job*, served on `:8081`. Pick the model by task:
- **`qwencoder14`** (Qwen2.5-Coder-14B) — code: boilerplate, a single
  well-specified function/class, mechanical refactors, docstrings, type hints.
- **`qwen14`** — general text: drafting comments/README prose, summarizing.
- **`qwen4i` / `phi4`** — trivial/fast first passes (rename, format, one-liners).

Good Tier-1 tasks: "write the `glob` tool given this `Tool` ABC and signature",
"draft docstrings for these functions", "convert this table to TOML". Local
output is a **draft** — it must be read and tested before it lands.

### Tier 2 — Haiku subagent (cheap; reliable for mechanical work)
Route **mechanical-but-broader** work here when it needs more reliability than a
14B local model or spans multiple files: scaffolding test fixtures, bulk
multi-file edits with a stated pattern, collating/extracting across files,
first-draft commit messages, codebase fan-out searches. Fully specify the task
in the prompt (subagents start cold).

### Tier 3 — Opus (main thread; keep this scarce)
Keep on Opus **only** what truly needs it:
- Architecture, framing, and module-boundary decisions.
- The hard correctness cores: the tolerant tool parser (`model/toolparse.py`),
  the agent loop + cancellation (`agent/`), the server manager/router
  (`server/`), permission resolution (`permissions.py`).
- Anything adversarial, ambiguous, security-relevant (the SSRF/allowlist guard,
  the sandbox boundary), or context-heavy.
- **Deciding what to delegate, and verifying what comes back.**

## Verification (non-negotiable)
- Treat **all** Tier-1/Tier-2 output as a draft. Opus reviews and the tests
  pass before anything is considered done.
- Fall **up** a tier on doubt: if a local model's output is wrong twice, or the
  `:8081` server is down, escalate to Haiku, then Opus. Never ship unverified
  delegated code.
- Never let delegation silently swallow a task — if a tier can't do it, say so
  and escalate, don't paper over it.

## Testing
- **`pytest`** for all functional code. Tests live in `tests/`, mirroring the
  package (`tests/test_toolparse.py`, etc.).
- Every non-trivial functional module ships with tests **in the same change**.
  Priorities: `toolparse` (good/malformed model outputs), permission
  resolution, alias/config resolution, the fs tools (`edit_file` exact-match,
  path scoping), and the model client's message assembly + tool-call parsing
  (HTTP mocked — **tests never hit the network or `:8081`**).
- Run `pytest -q` before declaring a task complete; state real results (don't
  claim green without running).

## Conventions
- Python ≥3.10, standard library first; deps limited to `httpx` +
  `prompt_toolkit` (+ `pytest` for dev) unless a new dep is justified.
- Match surrounding style; keep modules lean (this is an MVP).
- **No git repo yet** — it'll be created later. Don't run git commands or assume
  history. When it lands, commits get the Claude co-author trailer.
- Ask before hard-to-reverse decisions (public API shape, dependency additions,
  on-disk formats); pick sensible defaults for the rest and note them.
