# Harness improvement loop — running log

Goal: make locode good enough that a weak local model can take a high-level
spec, produce a design document, produce a plan with milestones and detailed
tasks, and then execute the code — without stalling, repeating itself, or
quietly giving up.

Method: `evals/harness.py` drives real `locode -p` runs against real local
models, mines the JSONL event log for process metrics, and gates changes
against a saved baseline. Every round records what changed, what broke, and
what the numbers did.

---

## Round 0 — baseline infrastructure (2026-07-21)

**Starting state.** `main` @ `10a5d71`, 304 tests green. Milestones M1–M5 done.
The loop already had stall detectors, a repeat detector, structural compaction,
and five kinds of nudge — all added blind, with no way to measure whether they
help.

**The gap that framed the round.** locode had no machine-readable output. The
agent loop narrates itself through `on_event` for the UI, but headless mode
(`-p`) passed no `on_event` at all, so every run's process detail was thrown
away. You cannot gate on regressions you cannot measure, so telemetry came
first.

### Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Tee `on_event` to a JSONL file behind `--log-events PATH` rather than parsing stdout | stdout is markdown-styled prose meant for humans; the events already exist and carry exactly the fields an eval needs |
| D2 | Telemetry must never raise into the loop | a broken log file killing a turn would be a worse bug than the one it was added to find |
| D3 | Eval cases live in `evals/`, not `tests/` | they need a live model server, take minutes, and are nondeterministic — the opposite of what `pytest -q` should be |
| D4 | Score = outcome (checks passed); metrics = friction (iterations, nudges, stalls) — reported separately | a change that holds score flat while cutting nudges and iterations is still a real win, and one number would hide that |
| D5 | Target models: **qwencoder14** + **qythos9** | see model evidence below |
| D6 | Added `--max-iterations` / `--max-wallclock` CLI overrides | the e2e case needs a bigger budget than a one-file bugfix; also useful outside eval |

### Model evidence (mined from the `code-tests` session, 2026-07-19)

A prior benchmark ran 4 pytest-gated agentic tasks × 3 repeats, plus a separate
planning benchmark graded by Opus. Findings that set D5:

| Model | Execution | Planning | Note |
|---|---|---|---|
| qwencoder14 | **12/12** — only perfect scorer | ranked **last** of 4 | median ~49s |
| qythos9 | 11/12 | ranked **2nd** | fastest, median ~24s |
| bonsai27 | 2/4 | ranked **1st** | best pure planner, weak executor, ~180s plans |
| qwen14 | 10/12 | 3rd | feature task flaky |
| devstral24 | 1/4 | — | narrates intent, emits no tool call |
| qwencoder30 | never ran | — | memory guard refuses: needs ~19.4 GB vs 19.0 GB budget |
| gemma12 | timeout | — | 6-backtick fence → 165 identical `pytest` calls |

**qwencoder14 + qythos9 chosen** because their strengths are *opposite* on the
two halves of this goal — best executor / worst planner, versus 2nd-best
planner / near-best executor. A harness change that only helps one of them is
visibly not a general improvement, which is exactly the diversity the user
asked for.

### Inherited failure-mode backlog

Six harness-mitigable failures were identified in that session. Status now:

1. **Silent stop-without-tool-call** — model narrates "let me examine the
   file:" then stops, and the loop returns that as a final answer. *Open.*
   Partially covered by the missing-deliverable nudge, but only when the
   request named a file to write.
2. **Malformed fence variants** (6 backticks) → repeat loop. Repeat detector
   now exists; *fence tolerance unverified.*
3. **Reasoning field discarded on thinking=on** — **fixed** since;
   `model/client.py` falls back to `reasoning_content`/`reasoning` when
   `content` is empty.
4. **Self-correction spiral** (48 edit→test→edit cycles) — *partially fixed*
   by `max_error_stall`, which keys off an unchanged error signature.
5. **Load-failure rows read as capability failures** in reports — handled: the
   harness records `stop_reason` verbatim rather than collapsing to pass/fail.
6. **False-positive keyword grading** — addressed by matching with
   word-boundary regexes over synonym sets in every `check.py`.

### Built this round

- `locode/telemetry.py` — `EventLog` (JSONL, clipped fields, degrades to no-op)
  and `tee()` to compose it with the UI callback. 9 tests.
- `--log-events` wired into both headless and REPL paths.
- `turn_start` / `turn_end` / `iteration` events and per-tool-call `seconds`
  added to the loop, so a run's shape is reconstructable.
- `evals/harness.py` — case discovery, scratch-workspace runs, event mining,
  scoring, reporting, and a `compare` regression gate.
- Cases: `design-doc`, `plan-doc`, `e2e-spec-to-code` (+ three execution
  fixtures under construction).

### Obstacles

- **Interactive REPL construction needed a new kwarg.** `Repl.__init__` built
  its `AgentLoop` with a hardwired `on_event`; adding an optional `event_log`
  parameter and composing through `tee` kept the change to two lines and left
  the default path byte-identical.
- **Config had no per-run budget override.** `Config.override()` only handled
  model/host/port; extended it to agent budgets rather than making the eval
  mutate the user's real `~/.config/locode/config.toml`.

**Tests:** 304 → 313 green.

---
