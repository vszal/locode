# AGENTS.md — working agreement for building `locode`

Policy for any agent (human or model) contributing to this repo. The headline
rule: **push work to the cheapest tier that can do it well, and keep only
genuinely hard judgment on the top tier.** See `architecture.md` for the design.

## The delegation contract

`locode` is built by its own kind of model, so the build loop mirrors the
product: **Opus plans, scopes, and verifies; local models implement.** Concretely:

- **Opus (top tier)** owns framing: decompose the milestone into well-bounded
  units, write a precise per-task spec (files to touch, signatures, behavior,
  edge cases, the exact test command), then **review every diff and run the
  tests** before anything lands.
- **Local models (`:8081`)** do the **execution — production code *and* its
  pytest tests.** This is their default role now, not just trivia: given a tight
  spec they write the module and the tests in the same change. Use them
  *extensively*; the win is real and the cost is ~zero.
- A local model's output is always a **draft** until Opus has read it and
  `pytest -q` is green. Never let a delegated diff land unread.

## Tiered delegation

Route each task to the lowest-cost tier that can do it *correctly*, then verify
up the chain. Three tiers:

### Tier 1 — local model (cheapest; on-device, free)
The **primary executor.** Drive it headless: `locode -p "<spec>" -m <alias>
--allow-tool edit_file,write_file,bash`. Pick the model by weight of task:
- **`qwen38`** (Qwen3.8-27B 3-bit, ~11 GB, the config **default** since
  2026-09-06) — the everyday execution workhorse. Beat qwythos9 on `repro-only`,
  the one eval case with headroom left, 20/20 perfect runs to 9/20 (p=0.0138
  even granting qwythos9 its best sweep; ROADMAP §5.138). Ties at ceiling on the
  other three cases. Needs ~5-6 iterations where qwythos9 needs ~9, which makes
  it **faster in wallclock despite decoding ~4x slower per character** — judge a
  local model by time-to-done, not tokens/sec (rule 88); the step count is why
  it wins, not the verdict itself.
- **`qwythos9`** (Qwen3.5-9B Claude-distill, ~9.6 GB, the previous default) —
  still the reliable editor and the right pick on a memory-tight machine, where
  qwen38's ~11 GB will not fit. Clean fenced tool JSON, correct arg keys,
  reliable multi-step editor with zero edit-match misses in eval.
- **`devstral24`** (Mistral-Small 24B agentic coder, ~14 GB) — the heavier
  executor: reach for it on broader multi-file changes, or when you want it to
  narrate its reasoning as it works. Capability-equal to qwythos9 on probes so
  far; the extra capacity is insurance for tasks bigger than those surfaced.
  Untested against qwen38.
- **`qwen4i` / `phi4`** — trivial/fast first passes (rename, format, one-liners).
- **`sushicoder`** — evaluated 2026-09-06 and **rejected**: 0.800/0.500 against
  qwythos9's 0.967/1.000, clean-finish 0.25, and a nudge histogram dominated by
  repetition loops. Newer and RL-tuned did not translate. Don't reach for it.

Good Tier-1 tasks: "implement `locode/install.py` with this marker read/write
API and ship `tests/test_install.py` covering each install method", "write the
`glob` tool given this `Tool` ABC and signature + its tests", "draft docstrings
for these functions". **Spec the test command explicitly** (e.g.
`.venv/bin/python -m pytest tests/test_install.py -q`) — the model runs it.

**Write the spec in PROSE, not pseudo-code.** Describe the API in words — the
functions, their arguments, what each returns, the edge cases — with as few
literal braces as possible. A brace-dense spec (inline `{...}`, f-strings,
code blocks) *corrupts these models' tool-call JSON*: they start emitting
malformed `}}`, the harness can't parse it, and the run stalls or fabricates
success. The identical task as plain prose runs clean. (Verified both ways,
2026-06-28.) Two corollaries: **always re-run the tests yourself** — they will
claim "N passed" without having written or run anything; and check `git diff`
afterward, since they sometimes touch files you told them to leave alone, and
the loop can silently drop a trailing tool call (file shown in the log but never
written). See the `locode-delegation-workflow` note for the full playbook.

### Tier 2 — Sonnet or Haiku subagent (cheap; reliable for mechanical work)
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
  `:8081` server is down, escalate to Sonnet or Haiku, then Opus. Never ship unverified
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
- Eval work is governed by numbered methodology rules indexed in **`RULES.md`**
  (`ROADMAP.md` holds the reasoning). Read it before designing or grading a
  sweep, and coin any new rule there, stated in full.
- **Cases live in two roots and both are one suite.** `evals/cases/` holds the
  research fixtures; `locode/bench/cases/` holds the four that ship in the wheel
  for `locode bench`. `discover_cases` reads both and refuses duplicate ids, so
  a sweep sees all of them and every historical result keyed by case id still
  lines up. The grader contract (`CheckCtx`) is defined once, in
  `locode/bench/runner.py`, and the harness imports it — those four cases are
  graded by both, and two copies would drift silently. When adding a case, ask
  who it is for: a user picking a model, or a lever you are measuring.
- **Report time-to-done to users, iterations to yourself** (rule 88). Wall-clock
  is what the user waits through; iterations is the metric that survives a
  degraded box. They disagree in 26% of archived model-pair comparisons.
- **Neither time nor iterations decides anything until correctness has gated
  it** (rule 89): iterations flatters the one-huge-reply model, wall-clock
  flatters the model that quits early, and a give-up run is the fastest run on
  the board.
- **Score only what a model can earn** (rule 90). A check that is already true
  of the *untouched seed* — the suite is intact, the fixture data is unedited —
  is a guard, not a component of the score: declare it in the grader's `GUARDS`
  so it vetoes to 0.0 rather than pays. Flat-averaging them paid an untouched
  seed 0.500 on three of the four shipped cases, and paid the same 0.500 to a
  run that neutered every assert in the suite. `DERIVED` names aggregates like
  `fully_fixed`, reported but kept out of the mean.
- **A graded run gets a flat wallclock** (rule 91). Interactively a turn's
  `max_wallclock_seconds` is a *floor*: real progress — a tool-call batch new to
  the turn, a bash that exited 0, a plan task completed — extends it by
  `progress_grant_seconds`, with no absolute ceiling, so a long-running agentic
  loop is not cut off for taking a while. A sweep must not work that way: it
  would make time-to-done incomparable with the archive and would let one
  degenerate model run overnight. Headless `-p` defaults the grant to 0 and both
  `locode bench` and `evals/harness.py` go through `-p`, so this is the default
  you get — **a new case must keep it that way.**
- Run `pytest -q` before declaring a task complete; state real results (don't
  claim green without running).

## Conventions
- Python ≥3.10, standard library first; deps limited to `httpx` +
  `prompt_toolkit` (+ `pytest` for dev) unless a new dep is justified.
- Match surrounding style; keep modules lean (this is an MVP).
- **Git is live.** Branch off `main` for changes; commits carry the Claude
  co-author trailer. Commit at logical checkpoints on your own; push only when the user asks.
- Ask before hard-to-reverse decisions (public API shape, dependency additions,
  on-disk formats); pick sensible defaults for the rest and note them.
- **Keep `config.toml.example` in sync.** It's a comprehensive, defaults-
  annotated reference for every `Config` field (distinct from the minimal
  starter `scaffold.py` writes on first run). Any change to a dataclass in
  `locode/config.py` — new field, renamed key, changed default — must land in
  `config.toml.example` in the same change, or the reference goes stale.
