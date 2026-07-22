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

## Round 1 — the measurement layer pays for itself immediately

**Goal:** get one real number out of the harness built in Round 0, and fix
whatever the number exposes.

### The finding

The first smoke run (`design-doc` / `qythos9`) was killed by the harness at its
900-second ceiling having produced **nothing**. The event log showed why in two
lines:

```
{"seq": 9,  "t": 41.348, "phase": "iteration", "n": 1, "elapsed": 6.9}
{"seq": 10, "t": 41.348, "phase": "assistant_start"}
```

No further events. **860 of the run's 900 seconds were spent inside a single
completion.** Without `--log-events` this is indistinguishable from "the model
is slow" or "the harness hung", which is exactly why Round 0 came first.

### Root cause: a token budget that was really a time budget

`model.max_tokens` was `32768`. Measured directly against the running server —
1200 tokens, tiny prompt, warm cache:

```
chunks=1200 first_token=0.56s total=45.4s -> 26.4 tok/s
```

At 26.4 tok/s a 32768-token reply takes **~21 minutes**. The turn budget is 600
seconds. So *one* reply was allowed to overrun the entire turn by 2×, and none
of the loop's guards could notice: `max_iterations`, `max_repeat_calls`,
`max_error_stall` and the wallclock check all run **between** iterations. The
old comment on the field said the loop's guards "still bound a runaway model" —
they did not, and could not.

This is the general lesson worth keeping: **on local hardware, generation-length
settings are wallclock settings.** A ceiling copied from a hosted-model config
means something completely different at 26 tok/s.

### Decisions

| # | Decision | Why |
|---|---|---|
| D7 | `model.max_tokens` 32768 → **6144** | ~4 min of generation. Still fits the largest thing the loop legitimately emits in one call (a ~2500-word design doc, a ~300-line module — both ≈4k tokens). |
| D8 | Client surfaces the server's `finish_reason` | "cut off at the limit" and "chose to stop" are identical in the text. The existing unclosed-fence heuristic only sees the tool-call case; prose cut mid-sentence was being returned as a confident final answer. |
| D9 | Truncation nudge allowed **twice** (`agent.max_truncated_retries = 2`) | With a tighter cap, a long deliverable can legitimately need two passes; one-shot would return the half-written *second* attempt as the answer. |
| D10 | Stream case stdout to disk instead of capturing | A ten-minute case was a black box until it exited. Also removed a `bytes`/`str` crash in the `TimeoutExpired` path that destroyed the whole run's result. |
| D11 | `config.toml.example` sync is a **test**, not a rule | AGENTS.md required it; nothing checked it. `tests/test_config_example.py` asserts field-for-field parity *and* that documented values equal the defaults. |

### Measured improvement

Same case, same model, same prompt — only the harness changed:

| | `smoke-01` (before) | `probe-02` (after) |
|---|---|---|
| outcome | killed at 900s, no artifact | **clean finish** |
| score | 0.00 | **0.93** |
| wallclock | 900s (timeout) | **176s** |
| iterations | 1 | 4 |
| nudges | 0 | 1 (`slow progress`) |

### Obstacles / debugging notes

- **Misread the run's own progress twice.** Concluded "hung for 15+ minutes"
  from file mtimes when `date` showed 3 and 6 minutes. Fix: read `date` and the
  server process's CPU, don't infer elapsed time from artifacts.
- **A checker false negative.** `covers_claim_expiry` required "lease" adjacent
  to "expire" and scored a document *false* that used `expires_at` on a claim
  row in six places. The concept was covered; only the vocabulary differed.
  Widened the synonym set. This is the second time the "verify every check
  independently against the artifact" rule has caught a grading bug — a checker
  that is too strict silently caps the achievable score.
- **Design quality is not graded.** The 0.93 document confidently specifies
  `SELECT ... FOR UPDATE`, which SQLite does not have. The coverage checkers
  cannot see this. Noted as a known limitation, not fixed this round.

**Tests:** 332 → 355 green.

---
## Round 2 — the model gets a task list

**Goal:** stop the loop accepting a confident "done" while the work the model
itself said it would do is still outstanding.

### The change (`bd592f8`)

An `update_plan` tool plus `agent/plan.py`: the model writes its own task list,
and the loop refuses a final answer while that list has open tasks, nudging with
the specific unfinished items instead. The point is not the plan document — it
is that the model's own stated intent becomes a *checkable* artifact the loop can
hold it to, rather than prose the loop has to interpret.

### Decisions

| # | Decision | Why |
|---|---|---|
| D12 | `update_plan` is a real tool, not a prompt convention | A convention is invisible to the loop. A tool call is an event, so "did it decompose the task, and did it finish what it listed" becomes a metric (`plan_updates`) instead of a reading exercise. |
| D13 | Open tasks nudge, capped, rather than block | An unbounded "you're not done" is its own infinite loop with a weak model that cannot close the task. |

### Obstacle: `Tool.permission` is decorative

`update_plan` did nothing under the headless eval until it was given an explicit
entry in the permissions table. The policy resolves an unlisted tool to **ask**
and never consults the `permission` attribute the `Tool` class advertises — so a
tool that declares itself safe is still silently gated. Worked around for the
eval; the attribute is either wired up or deleted. **Still open.**

**Tests:** 355 → 373 green.

---

## Round 3 — stalls are cycles, not repeats

**Goal:** make the stuck-detectors fire on how weak models actually get stuck.

### The finding

`exec-stall-trap` / `qwencoder14` burned all 50 iterations, 321 seconds, and
emitted **zero nudges** — while alternating a no-op `edit_file` with an identical
`pytest` invocation. Both stuck-detectors compared each iteration only to the one
immediately before it, so they could see a period-1 stall and nothing else. No
two *adjacent* iterations matched, so both counters reset every single turn.

This is the general shape worth keeping: **a detector keyed on "same as last
time" only catches the degenerate case.** Real stalls have a period. Keying each
detector off a streak *per signature* makes whatever is interleaved irrelevant.

### Decisions

| # | Decision | Why |
|---|---|---|
| D14 | Both detectors key off a per-signature streak | Immune to interleaving; each distinct call and each distinct error accumulates its own streak. |
| D15 | A repeat counts only when the **result** is also unchanged | Without it, interleaving-immunity misfires on ordinary work — re-running the same test between three different edits is progress, not a stall. |
| D16 | `harness.py rescore` re-grades a finished sweep from its preserved workdirs | Fixing a checker used to poison every comparison against an older sweep: baseline graded by the old ruler, candidate by the new. Re-running is not the fix — it costs an hour of GPU and, the model being sampled, would not reproduce those runs anyway. |
| D17 | `exec-stall-trap` scores iterations spent, not "no stall nudge fired" | The old check paid out precisely when the detectors were broken, and would have scored a nudged-then-recovered run *worse*. A check that rewards the bug it is meant to catch is worse than no check. |

**Tests:** 373 → 389 green.

---

## Round 4 — the gate reports a verdict it had no standing to reach

**Goal:** measure Round 3. Instead, learned that the measurement was invalid —
and that the gate said "FAIL" anyway.

### The finding

The `r3-cycle` sweep scored **0.591 against the baseline's 0.857**, clean-finish
0.92 → 0.38, and the gate printed `❌ REGRESSION GATE: FAIL` naming four cases.
Taken at face value that is an instruction to revert Round 3. All of it was
wrong, for three independent reasons:

1. **The sweep never finished.** 8 of 12 runs — every `qythos9` row after
   `e2e-spec-to-code` is missing. `results.json` is written incrementally, so an
   interrupted sweep still produces a scorable-looking file, and `overall_score`
   then averages *a different set of cases* than the baseline's. The four
   missing rows scored 1.00, 1.00, 1.00 and 0.71 in the baseline; dropping them
   alone moves the headline number.

2. **The box was degraded.** `design-doc`/`qythos9` died with "wallclock
   exceeded during a single reply (~6,199 chars)" after **572 seconds in one
   completion** — about **11 chars/s**, against the ~106 chars/s (26.4 tok/s)
   measured in Round 1. A ten-fold throughput collapse. In the baseline the same
   case took 462s of a 600s budget; it was already at 77% of the ceiling, so any
   slowdown pushes it over. Both `qythos9` failures are wallclock deaths, not
   quality regressions, and the qwencoder14 half of the sweep (22:36–22:53) ran
   over an hour before the qythos9 half (23:55–00:50).

3. **The two flagged qwencoder14 stops were the fix working.** Replaying the
   event logs against the detector logic: `plan-doc` wrote a **byte-identical**
   `PLAN.md` four times, and `exec-from-plan` cycled an identical
   `edit_file`(`old` not found)/`read_file` pair. Both are true positives —
   precisely the interleaved cycles Round 3 set out to catch. They cost the
   `clean_finish` flag and, on `exec-from-plan`, a score the model had already
   thrown away by getting stuck. The run that *does* measure the fix directly:
   `exec-stall-trap`/`qwencoder14`, **0.17 → 0.67, 50 iterations → 3, 321s →
   35s.**

### The general lesson

**A gate that returns FAIL on data that could not have returned PASS is worse
than one that admits it does not know.** The verdict was not a wrong number — it
was a confident answer to a question the data could not address, and its only
possible action was to revert a good change. Round 1's lesson was that on local
hardware generation-length settings are wallclock settings; the corollary is
that **throughput is a confounder for the entire suite**, because every budget in
the loop is a wallclock budget. At half the tok/s the same model doing the same
work misses deadlines it previously cleared, and the sweep reads as a regression
that no code change caused.

### Decisions

| # | Decision | Why |
|---|---|---|
| D18 | `assistant_end` carries `chars`; the harness derives `gen_chars_per_sec` | Throughput was invisible after the fact. Native `tool_calls` count toward it too, or a model would read as stalled on exactly the turns it was working. |
| D19 | `compare` returns **INCONCLUSIVE** (exit 2) on a missing-rows or throughput confound | Distinct from PASS and FAIL because the correct response is distinct: re-run, don't revert. Deltas still print, explicitly labelled "not as a verdict". |
| D20 | Missing-rows check is one-directional | Losing a baseline row means the sweep broke; gaining one means the suite grew. |
| D21 | Throughput check is one-directional and skipped when either side is unknown | A *faster* box cannot manufacture a passing score from a failing change, and sweeps recorded before D18 must compare as unknown rather than as infinitely slow. |

### Obstacles / debugging notes

- **Nearly reverted Round 3 on the gate's word.** The per-row deltas looked
  damning until the event logs were replayed against the detector's own logic.
  The reflex to trust a red gate is the thing that needed guarding here.
- **The replay initially disagreed with the run**, showing no streak reaching
  the trigger. Cause: when the repeat nudge fires, the loop `continue`s without
  running the calls, so those iterations emit no `run`/`result` events and are
  invisible in the event stream. The skipped batches have to be inferred from
  the gap.
- **n=1 per cell, on a sampled model.** Even a complete sweep on a quiet box
  cannot support a per-row verdict at n=1; `exec-from-plan` scoring 1.00 then
  0.25 is well within what sampling alone produces.

**Tests:** 389 → 410 green.

---
## Round 5 — a valid sweep, and an uncompletable plan

**Goal:** re-measure Rounds 2–3 on a complete sweep and a quiet box, now that
Round 4 made an invalid sweep say so.

### The sweep

`r4-clean`: all 12 runs, **72.8 chars/s** pooled — against the ~11 chars/s of the
sweep Round 4 threw out. (Note that 72.8 is the honest *sweep-level* figure; the
~106 chars/s from Round 1 was a synthetic probe with a tiny prompt and a warm
cache, and is not the number to compare a real run against. 72.8 is the new
reference.) Overall 0.857 → 0.801, gate FAIL at −0.056 against a −0.05 threshold.

Almost everything held or improved — `design-doc`/`qythos9` **0.80 → 1.00 and
462s → 139s**, `plan-doc`/`qwencoder14` 0.71 → 0.79, `exec-stall-trap`/
`qwencoder14` 0.17 → 0.33 with 50 iterations → 11. The entire regression was one
case, `e2e-spec-to-code`, on both models.

### The finding: an uncompletable plan kills the turn

`e2e-spec-to-code`/`qythos9` scored **0.00 while reporting a clean finish** — 216
seconds, four nudges, nothing produced. The model sent `tasks` as a truncated
fragment:

```
["[>] Write DESIGN.md — the approach
```

`update_plan`'s string-recovery path splits on newlines. That fragment has none,
so it became a **one-task plan whose text was the raw JSON garbage**. It carried
no recognized status marker, so it parsed as *open* — and no subsequent call
could ever mark it done. Round 2's completion gate then refused every final
answer for the rest of the turn, nudged `open plan tasks` to its cap of three,
and the turn ended with the model's reply still mid-tool-call.

The lesson generalizes past this parse bug: **Round 2 gave the model's own output
authority over when the turn may end, which makes any unparseable plan a
turn-killer.** A leniency that quietly *adopts* malformed input is far more
dangerous once that input gates completion than it was when it only shaped a
display string. Leniency has to stop where authority begins.

### Decisions

| # | Decision | Why |
|---|---|---|
| D22 | A `tasks` string opening with `[` is tried as JSON first | Also fixes the correctly-JSON-encoded-but-stringified array, which the newline split mangled just as badly. |
| D23 | If it neither parses nor opens with a *recognized* status marker, reject it | Failing loudly costs one iteration. Adopting it cost the entire turn. |
| D24 | The discriminator is a new `plan.has_status_marker`, not `_MARKER_RE` | The regex is deliberately permissive and matches `["[>] Write…` with a marker group of `"[>` — exactly how the fragment got adopted. Permissive is right for *parsing* a task and wrong for deciding whether a string is a task list at all. |

### Verification, and its limit

`r5-planfix` re-ran the case: `e2e-spec-to-code`/`qythos9` **0.00 → 0.80**, above
its 0.70 baseline, with zero `open plan tasks` nudges.

**But that run never called `update_plan` at all**, so it does not isolate the
fix — it shows the case can score well, not that the fixed path works. What
proves the fix is `test_tool_rejects_a_truncated_json_array`. Worth recording
plainly: an eval score moving in the right direction is not evidence that the
change you just made is the reason.

### Obstacles / debugging notes

- **The first cut of the fix broke a working path, and its own test caught it.**
  Keying off `startswith("[")` alone rejected the legitimate newline-joined
  `[x] a\n[ ] b` recovery. Writing the regression test for the *old* behavior
  before the new one is what surfaced it.
- **Every repeat-detector stop traced so far has been a true positive.** Across
  r3 and r4: a byte-identical `PLAN.md` written four times, an identical
  `edit_file`/`read_file` cycle, and `envcfg.py` rewritten with byte-identical
  content three times between edits — which is why its `pytest` never moved past
  `..FFFF`. Round 3 stands.
- **`e2e-spec-to-code` is now the weakest case on both models**, and both are
  stopped by the repeat detector rather than finishing. It is the obvious next
  target, and unlike the rows around it, its failure is not sampling noise.
- **n=1 remains the suite's real limitation.** `e2e`/`qwencoder14` read 0.90,
  0.70, 0.60 across three sweeps of the same code. No per-row verdict at this
  sample size means anything.

**Tests:** 389 → 415 green.

---
