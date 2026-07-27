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

## Round 6 — the first honest baseline (`r6-baseline`, HEAD `0ca50bf`, n=3)

**36 runs = 12 rows × `--repeat 3`.** Clean tree, AC power, generation rate
**73.4 chars/s** against the 72.8 reference — so for the first time the numbers
are known to have been measured on a healthy box rather than assumed to be.

| | r1-baseline (n=1) | r4-clean (n=1) | **r6-baseline (n=3)** |
|---|---|---|---|
| overall score | 0.857 | 0.801 | **0.736** |
| clean-finish | 0.917 | 0.750 | **0.500** |
| gen rate | *unrecorded* | 72.8 ch/s | 73.4 ch/s |

The gate returns **FAIL** against both. It is wrong, and the reason it is wrong
is the point of this round: **it is comparing a mean of three against a single
sample, and the single samples were optimistic.**

### Variance is concentrated, not uniform

The useful surprise from n=3 is how *little* most rows move. Eight of twelve
returned the identical score three times running (`e2e`/qwencoder14 0.70×3,
`exec-bugfix`/qwencoder14 0.50×3, `exec-stall-trap` 0.33×3 and 1.00×3,
`exec-from-plan` 1.00×3 both models, `design-doc`/qythos9 0.93×3) — two of them
with byte-identical tool trajectories, at temperature 0.3. Variance lives in two
rows only, and there it is not noise but **bimodality**: `plan-doc`/qythos9 ran
0.08 / 0.93 / 0.08. Averaging that row reports 0.36, a value it never produced.

So the earlier worry — "no per-row verdict at n=1 means anything" — was both
right and wrong. Most rows were reproducible all along; the cross-sweep drift
recorded in Round 5 came from comparing *different commits*, not from sampling.

### Both gate flags are n=1 optimism, traced to the run

- **`exec-bugfix`/qwencoder14 1.00 → 0.50.** Not a regression. All three runs
  scored 0.50, and run 3 is decisive: it took the repeat nudge, recovered,
  worked through every task in its own plan, finished clean — **and still scored
  0.50**. r4's 1.00 came from a flail that happened to end in a full-file
  `write_file` rewrite. 0.50 is the row's true value; 1.00 was the lucky sample.
- **`plan-doc`/qythos9 0.93 → 0.36.** The 0.93 reproduces exactly. The 0.08 runs
  are a distinct failure mode, below.

### The real finding: models stall in prose, and nothing watches for it

Seven of the eighteen unclean finishes are `budget: wallclock exceeded during a
single reply`, all on qythos9, all on the three document cases. The reply sizes
name the cause:

```
plan-doc/qythos9 run 3 — replies (seconds, chars):
  (5.7, 162) (5.4, 164) (245.5, 18709) (265.8, 18709) (77.6, 4534)
```

Two replies, **byte-identical at 18,709 chars**, 245s and 266s of a 600s turn,
with no tool call in either. The model wrote the whole of PLAN.md *as chat
prose* instead of calling `write_file`, was nudged, and regenerated the same
document verbatim. `wrote_plan_doc: False` — after ten minutes of work the file
never existed. Run 2, which called `write_file` on its third reply, scored 0.93.
`design-doc`/qythos9 dies the same way at ~21,506 and ~20,886 chars.

Every stall detector we have keys on a **tool-call** signature: `batch_sig` is
computed inside the `if calls:` branch, and a reply with no calls is handled
separately at `loop.py:310`. A model that repeats *itself* rather than a *call*
is therefore invisible to all of them. Round 3 closed the loop on repeated
calls; this is the same failure one level up.

The arithmetic makes it unforgiving. `max_tokens=6144` is ~21–24k characters
observed; at 73–95 chars/s that is 225–265 seconds. Against
`max_wallclock_seconds=600`, **a turn holds roughly two max-length replies.**
Two wasted ones end it.

### Decisions

| # | Decision | Why |
|---|---|---|
| D25 | `r6-baseline` replaces `r1-baseline` as the reference | r1 was recorded on a **dirty tree** (`git_dirty: true`) at n=1 with no throughput data. It cannot support a verdict and should not have been the gate's baseline this long. |
| D26 | A FAIL against an n=1 baseline is advisory, not a verdict | Both flags this round were the baseline's sampling luck, not the candidate's regression. Confirmed per-run, not inferred from means. |
| D27 | Report bimodal rows by their distribution, not their mean | `plan-doc`/qythos9's 0.36 is a number the row never produced. The mean hides that the failure is total (no file) rather than partial. |
| D28 | Next target is prose-repeat detection, ahead of `e2e-spec-to-code` | It costs three of twelve rows their clean finish and is the single largest source of lost score. `e2e` remains next after it. |
| D29 | Throughput telemetry stays, and earned its keep immediately | 73.4 vs 72.8 is what licenses reading this sweep at all; without it, Round 6 would be indistinguishable from the degraded r3-cycle. |

### Obstacles / debugging notes

- **The plan fix (`a46d226`) was briefly a suspect** for the score drop, since it
  is the only production change between r4 and r6. Ruled out by counting: 3
  `update_plan` errors in 125 calls across r6 (r4: 2 in 37). It is not burning
  turns.
- **Round 3 continues to hold.** `exec-stall-trap`/qwencoder14 sits at 0.33
  against r1's 0.17, and qythos9 clears it 3/3 in 24 seconds.
- **`--repeat 3` costs ~2.5× wallclock for information concentrated in two
  rows.** Worth it here to establish which rows are stable; not obviously worth
  it every sweep. Consider n=3 on the bimodal rows and n=1 elsewhere.

**Tests:** 424 green.

---

## Round 7 — catching a model that repeats *itself* (`2ed5b07`, `18e5974`)

Round 6's finding: every stuck-detector keys on a tool-call signature, so a
reply that makes no call is invisible to all of them. Two changes.

**Detection.** A repeated no-tool-call reply now ends the turn on the *first*
repeat. Reaching that point twice with the same text means a nudge was appended
in between and the model produced the same output anyway — the nudge is proven
inert. That is stronger evidence than a repeated tool call, which can be an
honest retry, and much costlier to sit through. Scoped to the branches that
nudge and continue (truncation, missing deliverable, open plan tasks), and gated
on `PROSE_REPEAT_MIN_CHARS` because the harm scales with what regenerating the
reply costs — a terse `done` that gets nudged and repeated wastes nothing.

**Cause.** The missing-deliverable nudge told a model that had just composed an
entire document that it had "only looked around". That is false, and a model
that just spent a quarter of the turn budget composing the document answers it
by composing it again. When the reply was a substantial draft, the nudge now
names what happened and gives the one action left: call `write_file` and pass
the text you already wrote.

### The first verification run caught the fix not working

`r7-prose` (HEAD `2ed5b07`) was killed after one run, because that run died on
wallclock exactly as before. The model had regenerated a 25,391-character
document that differed in **one character, 13,659 in** — a real newline where
the first copy had a literal backslash-n — and byte-exact matching, even
whitespace-normalized, called it a different reply.

**Byte-identical detection does not survive contact with a sampled model.** r6's
`plan-doc` repeat happened to be exact; that was luck, not the rule. `18e5974`
matches on a normalized opening plus a length within 2%. The length half is
load-bearing rather than incidental: what a truncation nudge *asks for* is a
shorter document, and a shorter document opens exactly the same way — so on the
prefix alone, complying would be indistinguishable from stalling.

### `r7b-prose` (HEAD `18e5974`, 6 runs, 73.4 chars/s)

| row | r6-baseline | r7b-prose |
|---|---|---|
| `design-doc`/qythos9 | 0.93 ×3, clean 0.00, 591s | **1.00 ×3, clean 0.67, 328s** |
| `plan-doc`/qythos9 | 0.08 / 0.93 / 0.08, clean 0.33, 450s | **0.08 ×3, clean 0.00, 525s** |

The detector fires on precisely the target failure, 3 of 3: an 18,098 /
19,566 / 19,002-char reply re-emitted, caught at ~525s with `the model repeated
the same reply without making progress` instead of dying mid-reply at 600s. No
false positive — `design-doc` run 3 answered its truncation nudge with genuinely
different, progressively smaller `write_file` calls (1,699 → 4,250 → 6,269 →
7,378 chars) and was correctly left alone.

**It did not improve the score, and it was never going to.** Stopping early is
honest, not productive: `plan-doc` still ends with no PLAN.md, 75 seconds
sooner. Two things must be said plainly rather than claimed as wins:

- **`design-doc` 0.93 → 1.00 is not attributable to Round 7.** No prose repeat
  and no missing-deliverable nudge occurred in any of those runs, so neither
  change was reached. The delta is `covers_tradeoffs` flipping true — model
  variance.
- **`plan-doc` 0.36 → 0.08 is not a regression.** It is the same bimodal row
  drawing 0 successes from 3 where r6 drew 1. At p≈1/3 that happens 30% of the
  time. The detector cannot cause it: it only fires on a repeat, and the
  successful run has none.

### The real blocker, now unmistakable

Both document cases fail the same way, and it is not a detection problem:

    plan-doc/qythos9 — one reply of ~19,000 chars, cut at the token limit
    design-doc/qythos9 run 3 — 23,264 chars, cut at the token limit

The model wants to emit an 18–25k-character document through
`model.max_tokens = 6144` (~24k chars). It does not fit, `write_file` is
truncated, and the file never lands. `_nudge_truncated` already gives the right
advice — write the first sections, append the rest — and the model *sometimes*
takes it (design-doc run 3) and sometimes re-emits the whole document verbatim
(all three plan-doc runs).

This is Round 1's tradeoff coming due. Lowering `max_tokens` 32768 → 6144 was
correct on the evidence then — generation length is a wallclock setting — but
for document cases the cap now sits *below the artifact size*, so the artifact
can never be written in one call at all.

### Decisions

| # | Decision | Why |
|---|---|---|
| D30 | Repeated prose ends the turn on the FIRST repeat | An intervening nudge produced the same output, so the nudge is inert. Waiting for a second costs another ~250s, which the turn does not have. |
| D31 | Gate it on reply size | The harm is proportional to regeneration cost. Without the gate it hijacks three existing paths — caught by the existing tests, not by review. |
| D32 | Match on opening + length, never byte equality | A one-character diff 13,659 in defeated exact matching in the wild. Length is what distinguishes "regenerated it" from "complied and shortened it". |
| D33 | Keep Round 7 despite a flat score | It fires 3/3 on the target, has produced no false positive, and converts a silent 600s death into a labelled 525s stop. Legibility is worth landing; it is not worth *claiming* as a score win. |
| D34 | `max_tokens` vs document size is the next round — decide it with a sweep, not a hunch | Raising it trades these rows against every row's wallclock, and Round 1 lowered it *after* measuring. It is a `config.py` default, so `config.toml.example` moves with it. |

### Obstacles / debugging notes

- **The verification run is what found the bug in the fix.** The unit tests were
  green and the logic read correctly; only real sampled output showed that
  "identical" is not a property model replies have. Round 5's lesson inverted:
  there, a passing eval failed to prove the fix worked; here, a failing eval
  proved it did not.
- **A 2-row targeted sweep gates as INCONCLUSIVE**, correctly — it is missing 10
  of 12 baseline rows. Round 4's machinery behaving as intended.
- **`design-doc` is far more variable than r6 suggested.** r6 read 0.93 ×3;
  across r7/r7b it produced 0.07, 1.00, 1.00, 1.00. Whether the model writes a
  ~9.5k-char document (fits, clean finish ~200s) or a ~24k one (truncated, dies)
  looks close to a coin flip, and it dominates the row's score.

**Tests:** 424 → 436 green.

---

## Round 8 — a bigger budget, and the tool nobody used (`r8-append`, HEAD `3f7fc05`, n=3)

Two changes shipped on the D34 question: an `append_file` tool, so writing a
document in pieces is actually possible, and `model.max_tokens` 6144 → 8192
(~32k chars, ~440s at the measured rate — the top of what fits in a 600s turn).
Plus local-first install steering (`tools/installhint.py`) and refusal text that
tells a headless model a tool is gone for good.

    overall score     : 0.736 -> 0.752  (+0.016)
    clean-finish rate : 0.500 -> 0.639  (+0.139)
    total nudges      : 78 -> 39
    total iterations  : 496 -> 414
    ❌ GATE FAIL: design-doc/qythos9 0.93 -> 0.38; exec-bugfix/qwencoder14 0.50 -> 0.33

### The finding that reframes the round

**`append_file` was called zero times across all 36 runs.** Neither model ever
reached for it, including on the two cases whose entire failure mode is a
document that will not fit in one call. So *every* delta in this sweep — the
wins and the losses alike — is attributable to the `max_tokens` raise alone. The
tool is in the catalog, in the truncation nudge, and in the allow-list of all
six cases, and it is inert.

The reason is visible in the traces: `_nudge_truncated` is the only thing that
mentions chunking, and it fires *after* the model has already spent ~450 seconds
generating to the cap. By then the turn is over. Advice that arrives after the
cost has been paid is not advice. The lever has to act **before** generation
starts — in the `write_file` description — not as a correction afterwards.

### Row 1: `design-doc`/qythos9 0.93 → 0.38 — real, and caused by the cap

Per-run: 0.07 / 0.07 / 1.00. The two zeros are one story:

    assistant_end chars = 35,726   ->  nudge "tool call truncated"
                                   ->  nudge "slow progress vs wallclock"
    assistant_end chars = 0        ->  turn_end (no result)

The model generates flat into the 8192-token ceiling, the `write_file` JSON is
cut mid-string, and the next request comes back **empty** — mlx-server
disconnecting (`Server disconnected without sending a response`). Run 3 wrote
9,912 chars and scored 1.00. Raising the cap did not make the document fit; it
gave a model that expands to fill its budget enough room to reach an
infrastructure ceiling.

Note what the row's *clean-finish* did: 0.00 → 1.00. That is the harness lying,
not an improvement — see below.

### Row 2: `exec-bugfix`/qwencoder14 0.50 → 0.33 — not attributable

Per-run 0.5 / 0.25 / 0.25; the two 0.25s lost `kept_all_tests`. The model never
opened a test file. What it did was leave `textkit.py` with

    IndentationError: expected an indented block after 'if' statement on line 71

so `test_textkit.py` could not be imported, pytest exited 2 with a **collection
error**, and `passed + failed >= EXPECTED_TESTS` read false. Nothing was
deleted. A weak 14B model made a syntax-breaking edit, then hit `edit_file`
"`old` and `new` are identical" twice and was stopped by the repeat detector.
No Round 8 code path is on that trace — no denials, no install hints, no
truncation.

There is also a confound of my own making: every qwencoder14 row ran 30–40%
slower than baseline (32–48 ch/s vs 42–72), because I was running pytest, pty
forks, git and a 1.2 MB log read on the same machine while it swept. That half
of the sweep is measured under load and does not deserve to be believed.

### Two measurement bugs this exposed

- **`clean_finish` counts an infrastructure death as clean.** The turn ended on
  a transport error, not a detector, so `stopped is None` and the row scores a
  perfect 1.00 clean-finish rate on two runs that produced nothing. A turn that
  ends with an empty reply and no result is the *least* clean outcome there is.
- **`kept_all_tests` is a false accusation on a collection error.** "The model
  deleted tests" and "the module no longer imports" are different failures with
  different fixes, and the checker cannot tell them apart.

### Decisions

| # | Decision | Why |
|---|---|---|
| D35 | Keep `max_tokens = 8192`; do **not** revert | 6144 was already known-insufficient (R7: 19–25k documents truncated, artifact never landed). Reverting reinstates a known failure to avoid a new one that has a narrower fix. |
| D36 | Bound the write size in the `write_file` **description**, not in a nudge | Zero `append_file` calls in 36 runs is proof the after-the-fact nudge is inert. The only instruction a model can act on before it starts generating is the one in the tool it is about to call. |
| D37 | Accept the gate FAIL: one row attributable, one not | `design-doc`/qythos9 is real and gets the D36 fix. `exec-bugfix`/qwencoder14 is a syntax-breaking edit plus a misnamed check, measured under load I created. A red gate is a question; both questions are now answered. |
| D38 | An empty reply / transport death must not score as a clean finish | Otherwise the metric rewards exactly the outcome it exists to detect, and the worst two runs of the sweep carried its best clean-finish number. |
| D39 | Never run anything else on the machine during a sweep | 30–40% throughput loss is enough to change stop-detector outcomes, which are wallclock-gated. Half of this sweep is now unusable as evidence. |

### Obstacles / debugging notes

- **Both gate rows had to be read run-by-run to be understood, and both differed
  from what the number said.** Fourth round running where the gate's headline
  was not the finding.
- `plan-doc`/qythos9 **0.36 → 0.93** (3/3, 5 iterations, ~150s) is the round's
  real result and the case D34 was about — the bigger budget lets the document
  land in one call. `design-doc`/qwencoder14 0.73 → 1.00 in 3.0 iterations /
  52.8s, against 15.3 / 274s at baseline.
- The two denied `bash` calls in the sweep (`plan-doc`/qwencoder14 r2) took the
  new headless wording and the model moved on both times without retrying —
  the refusal-text change works, on a sample of two.

**Tests:** 436 → 469 green.

---

## Round 9 — moving the instruction to where it can act (`r9-writesize`, HEAD `c00e8a4`, n=3)

D36's change: the size guidance moved out of `_nudge_truncated` (which fires
after ~450s of doomed generation) and into `write_file`'s own description —
"keep content under about 6000 characters; longer documents go write_file then
append_file". Shipped alongside the Ctrl-C/Esc/denial-visibility fixes and the
`ask_user` permission wiring.

    overall score     : 0.736 -> 0.807  (+0.071)
    clean-finish rate : 0.500 -> 0.667  (+0.167)
    total nudges      : 78 -> 47
    ✅ GATE PASS vs r6-baseline
    (vs r8-append: +0.055 / +0.084, one flagged row — plan-doc/qwencoder14 0.81 -> 0.64)

### The target row, fixed

    design-doc/qythos9   0.38 -> 0.98,  3/3 clean,  4.0 iters,  189s  (was 451s)

No infrastructure deaths, no truncation nudges, half the wallclock. This is the
row that cost Round 8 its gate.

### But not by the mechanism the instruction described

**`append_file` was called zero times again — 0 for 72 runs across two sweeps.**
And qythos9 did not obey the 6000-character number either: its design docs came
in at 11,675 / 11,882 / 13,557 characters, *larger* than the 9,662 of r8's one
surviving run. What the sentence actually did was stop the runaway — 35,726 and
32,654 became ~12k — without changing anything it literally asked for. The
useful content of the instruction is "do not emit one enormous document"; the
number and the chunking recipe are both being ignored.

That matters, because the same sentence read to the weaker model said something
else entirely.

### The flagged row: the instruction backfiring (`plan-doc`/qwencoder14 0.81 -> 0.64)

    r8:  one write_file of 1,438 / 2,821 / 1,904 chars, 10 iters, 253s
    r9:  write_file 632, then 33, then 166 — then 6-9 edit_file calls, 26 iters, 507s

qwencoder14 obeyed the cap by writing a **stub** and then trying to grow it with
`edit_file`. Run 3 never produced a plan at all: it cycled `edit_file
taskq/queue.py` → `update_plan` five times, each edit larger than the last,
until the turn's 600s ran out. A ceiling a weak model can satisfy by writing
less is a trap — "shorter" and "incomplete" are the same move if nothing says
otherwise.

The cycle also slipped every stuck-detector, because each `edit_file` carried
growing content (new signature every time) and each `update_plan` returned the
same 597-char reply between them. Period-2 alternation with a mutating limb is
still uncaught.

### A measurement correction

I read document sizes off the event log's `args.content` and got a suspicious
2,021 characters for six different runs across two cases and two models.
`telemetry.MAX_FIELD_CHARS = 2000` clips logged strings and appends
`…<clipped N chars>` — so every one of those was the clip, not the document.
The real length is recoverable from the suffix, and the numbers above use it.
`assistant_end.chars` is a count, not a clipped string, so Round 8's 35,726 /
32,654 / 9,912 figures stand.

### Decisions

| # | Decision | Why |
|---|---|---|
| D40 | Keep the instruction in the tool description; it works | +0.071 overall and the target row 0.38 -> 0.98 at half the wallclock. The placement was right even though the wording was read two different ways. |
| D41 | Lead with COMPLETENESS, make the ceiling a branch not a budget | The observed failure is a stub plus an edit loop, so the sentence must forbid stubs by name. "Write COMPLETE content — never a placeholder you intend to fill in later with edit_file", then the ceiling as a branch to append_file. Ceiling raised 6000 -> 8000, since 6000 was ignored upward and over-obeyed downward. |
| D42 | Stop asserting a cause the exception does not know | `DeadlineExceeded` carries the TURN's deadline, so it fires both on one long reply and on a turn that spent its budget elsewhere. The message claimed the former in both cases and printed "~0 chars generated during a single reply" — a sentence that cannot be true. |
| D43 | Do not treat "append_file is unused" as a wording problem any more | Two rounds, two phrasings, 72 runs, zero calls. If Round 10 does not move it, the next lever is structural (the loop offering the continuation itself), not another sentence. |

### Obstacles / debugging notes

- **The gate passed and the interesting finding was still in the flagged row.**
  Fifth round running.
- `plan-doc`/qythos9 r2 wrote **22,860 characters in one call** and hit the
  wallclock stop, so the runaway is reduced, not eliminated.
- The new `⏹ infrastructure:` label did its job: zero infrastructure deaths this
  sweep, which is itself the evidence that r8's two were caused by the 8192 cap
  meeting a 35k-character reply.

**Tests:** 473 → 480 green.

---

## Round 10 — the same idea, reworded, loses half the sweep (`r10-complete`, HEAD `db87b26`, n=3)

D41's change: `write_file`'s description was rewritten to lead with completeness
("write COMPLETE content — never a placeholder or stub you intend to fill in
later with edit_file") and to turn the character cap into a branch (6000 → 8000,
"if the finished file would run past roughly 8000 characters, write its first
complete sections now and add each remaining section with append_file"). The
target was r9's one flagged row, `plan-doc`/qwencoder14, which had answered the
old cap with a 632-char stub and an edit loop.

    overall score     : 0.807 -> 0.651  (-0.156)
    clean-finish rate : 0.667 -> 0.444  (-0.223)
    design-doc/qythos9: 0.98 -> 0.07

It fixed the row it aimed at — `plan-doc` went 0.64 → 0.86 (qwencoder14, 4.0
iterations, 0 nudges) and 0.95 → 1.00 (qythos9) — and cost more than twice that
everywhere else.

### The mechanism, unambiguous

    r9  (cap stated flatly at 6000): replies ~12k, documents 11.7k-13.6k, 3/3 landed
    r10 (completeness first, 8000):  replies 36,563 / 41,560 / 33,774, ZERO write_file
                                     calls in three runs, 3/3 dead on the turn budget

Every run generated a whole design document into the token ceiling, had its
`write_file` JSON cut mid-string, got the truncation nudge, and ran out of turn.
Exactly the Round 8 failure, restored by a wording change.

So the brake is **the low number stated flatly**, and not the reasoning around
it. This is worth stating precisely because it is not what the sentence says:

- qythos9 has never obeyed 6000 — under it, documents came in at 11.7k-13.6k.
- `append_file` has now been called **zero times in 108 runs** across three
  sweeps and two phrasings.

The number works by pulling the target down, not by being followed. The moment
"COMPLETE" outranked it in the sentence, the pull disappeared and the model went
back to emitting everything at once. D43's bet is settled the wrong way: this is
not a wording problem, and no further sentence is going to produce an
`append_file` call.

### A latent crash the sweep exposed

`e2e-spec-to-code`/qythos9 r1 died 19 iterations in with the logged text
`'new'`. That is `KeyError('new')` from `args["new"]` in `EditFile.run`: the
model emitted an `edit_file` call without a `new` field, the exception escaped
`tool.run`, escaped `_run_calls`, escaped `run_turn`, and ended the turn. Any
tool raising anything unexpected could do this, and had nothing to do with
Round 10 — the new `⏹ infrastructure:` label from Round 9 is what made it
visible at all, having been an unexplained blank row before.

### Unattributed movement

Four execution rows moved without a mechanism I can point to:
`exec-from-plan`/qwencoder14 1.00 → 0.50, `exec-from-plan`/qythos9 1.00 → 0.67,
`exec-stall-trap`/qythos9 1.00 → 0.72, `exec-bugfix`/qythos9 1.00 → 0.83. These
cases write no documents, and the only shipped change is a tool description plus
a terminal stop message. Two of the r10 traces show the model planning and never
executing (`exec-from-plan`/qythos9 r2: two reads, four `update_plan`, zero
edits). The honest reading is that a tool-catalog edit reshuffles sampling for
every case, and that three runs per row cannot separate that from variance. It
is not evidence for the rewording; it is a reason the next sweep repeats a known
configuration.

### Decisions

| # | Decision | Why |
|---|---|---|
| D44 | Revert to the r9 wording verbatim; do not soften it again without a sweep | It is the only version that has passed a gate, and the failure mode of the alternative is total (0/3 documents written). The comment in `fs.py` now carries the measurement so the next reader does not re-run this experiment. |
| D45 | A tool raising must never end the turn | A missing argument is an ordinary bad call. The model recovers from a tool error; it cannot recover from the loop exiting. Caught and handed back as an error result, with `CancelledByUser` / `DeadlineExceeded` still propagating. |
| D46 | Settle D43: stop trying to talk the model into `append_file` | 108 runs, three sweeps, two phrasings, zero calls. The next attempt must be structural — the loop continuing a truncated write itself — or the tool should be removed rather than left as inert catalog weight. |
| D47 | The next sweep repeats a known configuration | Four rows moved with no mechanism. Re-running r9's wording measures how much of a 12-row sweep at n=3 is noise, which every verdict so far has been assuming rather than knowing. |

### Obstacles / debugging notes

- **This is the first round where the gate's headline WAS the finding**, and it
  took reading three event logs to establish that the cause was the wording
  rather than the eval. The per-run trace remains non-optional; it is what
  separated "the model wrote a shorter document" (r9) from "the model wrote no
  document at all" (r10).
- The reworded stop message earned its keep immediately: `budget: the turn's
  wallclock ran out while generating (~12,264 chars into this reply)` reads
  correctly on rows where the old text would have claimed a single reply ate
  the whole budget.

**Tests:** 480 → 482 green.

---

## Round 11 — the null sweep fails its own gate (`r11-repeat`, HEAD `a299f9f`, n=3)

D47's sweep. It tests nothing: same wording as r9, same cases, same models. The
only shipped delta is D45's crash guard. If the harness measured what six rounds
of write-ups have assumed it measured, this should have reproduced r9.

    overall score     : 0.807 -> 0.667  (-0.140)
    clean-finish rate : 0.667 -> 0.472  (-0.195)
    ❌ REGRESSION GATE: FAIL
       design-doc::qythos9        0.98 -> 0.38
       exec-bugfix::qythos9       1.00 -> 0.50
       exec-from-plan::qwencoder14 1.00 -> 0.17

r10 — a real change, judged a failure and reverted — scored −0.156. Repeating a
configuration against itself scores −0.140. **The two are indistinguishable.**

### What this invalidates

`design-doc`/qythos9's longest reply, by round:

| round | wording | run 1 | run 2 | run 3 |
|---|---|---|---|---|
| r6 | (pre-`append_file`) | 23,340 | 24,130 | 25,971 |
| r8 | append nudge | 35,726 | 32,654 | 9,912 |
| r9 | flat "about 6000" | **11,994** | **12,271** | **13,911** |
| r10 | softened to 8000 | 36,563 | 41,560 | 33,774 |
| r11 | flat "about 6000" | 39,969 | 32,718 | **11,206** |

The distribution is bimodal — the model either obeys and writes 11–14k, or
ignores the cap and writes 33–42k — and **r9 drew the short mode three times in
a row.** Under the identical prompt r11 drew it once. Fisher exact on
r9+r11 (4/6 short) against r10 (0/3 short) gives p ≈ 0.19.

So D44 — "the brake is the low number stated flatly" — is **unsupported**. Not
disproven: r10 has never produced a short reply in three runs, and the flat
number has produced four in six. The wording may well help. The claim that it
was *measured* is what was wrong, and it was written into `fs.py` as settled
fact with an instruction not to revisit it. That comment now says what is
actually known.

The other two flagged rows are not the same phenomenon and were not variance.

### The bug the null sweep found

`exec-from-plan`/qwencoder14, 1.00 → 0.17. The model wanted to *add* a function,
so it called `edit_file` with `old` set to the **empty string**:

    edit_file(old="", new="def median(...)")     -> "`old` appears 867 times;
                                                    pass replace_all"
    edit_file(old="", new=..., replace_all=True) -> "edited"
    edit_file(old="", new=..., )                 -> "`old` appears 273105 times"
    edit_file(old="", new=..., replace_all=True) -> "edited"
    edit_file(old="", new=..., )                 -> "`old` appears 79746660 times"

`"".count(text)` is `len(text)+1`, so an empty `old` reads as *ambiguous*, and
the ambiguity message tells the model to pass `replace_all`. `text.replace("",
new)` splices `new` between every character. 867 chars → 273,104 → 79,746,659,
a ~300x blowup per obeyed retry. The arithmetic closes exactly:
`273,104 + 273,105 x 291 + 1 = 79,746,660`, the third reported count.

Run 1 of that row died with the checker's `pytest` timing out after 180s — it
could no longer parse the file. It scored 0.00.

**The harness was instructing the model to destroy the file, and the model was
doing as it was told.** Empty `old` appears in r9 too (7 calls) — not one
escalated to `replace_all`, which is the whole of why that row read 1.00. Zero
occurrences in the eight rounds before r9.

Fixed at both layers: `try_edit` returns a new `empty_old` status before any
matching tier runs, and `edit_file` answers with "to ADD text use append_file …
to CHANGE text copy the exact existing lines" — no `replace_all` advice on the
one input where it is destructive. The matcher guard is the load-bearing one:
the ASK diff preview calls `try_edit` directly, so unguarded it would render a
79 MB blowup as the change a user is asked to approve.

This also answers D46 from the other direction. `append_file` has zero calls in
144 runs not because the model never wants to append — it wants to append here,
and reaches for `edit_file(old="")` to do it. It was never choosing between the
two tools; it was never finding the second one.

`exec-bugfix`/qythos9 (1.00 → 0.50) is ordinary sampling: r9's third edit landed
on the buggy span and fixed it, r11's landed one line off, "succeeded", left the
test red, and the model then re-sent it verbatim until the repeat detector fired.

### Decisions

| # | Decision | Why |
|---|---|---|
| D48 | An empty `old` is refused at the matcher, never routed to `replace_all` | A tool must not answer malformed input with advice that multiplies the file 300x. Reachable from a plain intent ("add this function"), and it destroyed a run before anyone noticed. |
| D49 | Treat every past per-row verdict as provisional; n=3 does not resolve a 0.15 gate threshold | The null sweep moved 0.14 overall and flipped three rows. Any finding rounds 6–10 rested on a single row's delta needs re-measuring before it is trusted. |
| D50 | Reverse the reading of a gate failure: it flags rows to trace, and no row is a mechanism until the event log shows one | Both real findings this round came from tracing. The gate ranked the noise row (`design-doc`) above the row hiding a data-destroying bug (`exec-from-plan`). |
| D51 | Raise n before running another wording experiment; do not re-litigate D44 at n=3 | Six runs across two sweeps cannot separate a bimodal 40/60 split from a real effect. Anything smaller than the r10-sized collapse is currently unmeasurable. |

### Obstacles / debugging notes

- The sweep that was designed to measure nothing is the most productive round
  so far. Both findings were invisible to the aggregate: one row's collapse was
  pure variance and the other was a live bug, and they scored within 0.2 of each
  other.
- The gate's own threshold is now suspect. It flags at 0.15; the null sweep's
  overall move was 0.14 and three individual rows moved 0.48–0.83. A threshold
  tuned below the noise floor reports mostly noise.
- The checker's `pytest` timeout (180s) is doing real work as a safety net, but
  it scores an infrastructure kill as 0.00, which reads as a model failure.
  Same class of bug as c00e8a4; worth the same treatment.

**Tests:** 482 → 485 green.

---

## Round 12 — salvage the truncated write; the fix lands its target (`r12-salvage`, HEAD `96fa783`, n=8)

D46 asked for a structural answer to the truncated-write failure — "the loop
continuing a truncated write itself" — instead of another sentence aimed at the
model. This is it, and it doubles as D51's raised-n sweep (n=3 → **n=8**).

**The fix.** A large document written as one `write_file` truncates at the token
limit: the content JSON string never closes, `extract()` recovers nothing, and
the whole partial reply evaporates — qythos9's `design-doc` "long mode writes 40k
and lands nothing" (r11 scored 0.38, with 0.07 in long mode). Two salvage paths,
both scoped to `write_file`/`append_file` only (a half-formed `edit_file`/`bash`
is unsafe to run; a partial *document* is strictly better landed than lost):

1. `finish_reason=length` → `toolparse.salvage_truncated_write` recovers the
   partial (targeting the unclosed fence `_fence_blocks` deliberately skips),
   lands it, then `_nudge_continue_salvaged` steers the model to `append_file`
   the rest. Multi-turn completion. Bounded by `max_salvaged_writes=4`.
2. `DeadlineExceeded` mid-write → land `e.partial` before stopping (no budget to
   continue). This was the *actual* r11 death path: wallclock at ~24k chars.

    overall score     : 0.667 -> 0.784  (+0.117)
    clean-finish rate : 0.472 -> 0.562  (+0.090)
    ❌ REGRESSION GATE: FAIL
       e2e-spec-to-code::qwencoder14  0.80 -> 0.60
       exec-bugfix::qwencoder14       0.42 -> 0.25
       exec-from-plan::qythos9        1.00 -> 0.69

**The target moved, decisively.** `design-doc`/qythos9: **0.38 → 0.98** (all 8
runs "ok", zero STOPPED, the bimodal collapse gone). `exec-bugfix`/qythos9
1.00, `exec-from-plan`/qwencoder14 0.17 → 1.00.

**The gate FAIL is baseline noise, proven by mechanism (D50).** The salvage path
fired in exactly **5 runs, all qythos9** — `e2e-spec-to-code` (r1/r5/r6/r7) and
`plan-doc` (r5), traced via `continue truncated write` / `landed its partial file
first` in the event logs. Both cases *held or improved* (e2e-qythos9 0.70 → 0.76,
plan-doc-qythos9 0.96). **None of the three flagged rows had any salvage
activity** — the change cannot have caused them:

- `exec-bugfix`/qwencoder14 is a tiny targeted-edit case with no large write at
  all; salvage physically cannot fire. 0.42 → 0.25 is n=3 → n=8 resampling.
- `exec-from-plan`/qythos9's r11 1.00 was 3/3 luck (its qwencoder14 twin was 0.17
  at n=3 and 1.00 at n=8 — n=3 is unreliable in both directions).
- `e2e`/qwencoder14 never triggered salvage; its wallclock death at ~20,400 chars
  was not a `write_file`.

This is D49/D50/D51 vindicated in one shot: the gate ranked three noise rows a
"regression" while the change's real effect (+0.60 on the targeted row) is
invisible to it, and only mechanism-tracing separates the two.

**Decision: do not revert; establish r12 as the n=8 baseline.** Unlike r10 (a
real change with a total failure mode), this change is verified by tests, hits
its target, lifts overall and clean-finish, and provably does not touch any
flagged row.

| # | Decision | Why |
|---|---|---|
| D52 | Salvage a truncated `write_file`/`append_file` instead of losing the partial; steer the model to `append_file` the rest | Answers D46's demand for a structural fix. A document generated to the token/wallclock limit is real work; discarding it because the JSON never closed turned a near-miss into a 0.00. |
| D53 | Never salvage a truncated `edit_file`/`bash`/web call | A half-formed `new` corrupts a targeted edit and a half-formed command is dangerous; only a whole-file *document* is safe to land partially. |
| D54 | A gate FAIL against an n=3 baseline is not evidence of regression; require a mechanism in the event log before crediting one | The change fired in 5 traceable runs, none of them a flagged row. Per D50, the flag is a pointer to trace, and the trace here exonerates the change on all three. |
| D55 | r12 (n=8) is the new baseline; retire r9/r11 (n=3) as comparison points | n=8 per row is the first sweep where a per-row delta is worth reading. Future gates compare against r12, not the noisy n=3 rounds. |

### Obstacles / open threads

- **The large-write problem persists for EDITS.** Salvage is scoped to
  `write_file`/`append_file` by design, so a truncated `edit_file` of a big span
  (the e2e/qwencoder14 ~20,400-char wallclock death) still loses everything. The
  edit path needs its own answer — likely steering big edits toward small
  `old` snippets rather than salvaging a partial `new` (which is unsafe).
- **The "repeated the same tool call" nudge dominates** (42 of 107). `exec-bugfix`
  and `exec-from-plan` still show the edit-lands-one-line-off loop from r11; that
  is the next real target, unrelated to writes.
- The checker's 180s `pytest` timeout scoring an infra kill as 0.00 is still open
  (c00e8a4 class).
- Gate threshold 0.15 still below the per-row noise floor even at n=8 for the
  high-variance cases (e2e); worth pairing with a variance-aware rule.

**Tests:** 485 → 497 green.

---

## Round 13 — no-op edit_file guidance lands its target on qwencoder14 (`r13-edithelp`, HEAD `e526efe`, n=6)

Targeted validation of build 21 (P0 = *editing successfully*, user, 2026-07-24).
Two cases only (`exec-bugfix` + `e2e-spec-to-code` — where the no-op flails live),
both models, `--repeat 6`. Compared like-for-like against the same two cases in
r12-salvage by mining `events/*.jsonl`: pair each `run`/`result` `edit_file`,
classify the failure on `result.content` (`error` is a bool flag, not the text),
and mark a failure *unrecovered* if no later `edit_file` on the same path succeeds.

**qwencoder14 — the model that owned the no-op problem — the fix worked:**

| metric | r12 (before) | r13 (after) |
|---|---|---|
| edit_file calls | 116 | 106 |
| fail rate | 41% | **20%** |
| no-op fails | 29 | **8** |
| **unrecovered no-op dead-ends** | **20** | **1** |

The prevention text (`new` must DIFFER from `old`) + the sharper no-op message did
exactly what they were designed to do: the dead-end that never recovered is gone.

**But task scores barely moved:** exec-bugfix qwencoder14 0.25 → 0.29,
e2e 0.60 → 0.63. Landing edits ≠ computing correct fixes. qwencoder14 stops
burning iterations on no-ops but still can't produce the right bugfix — the
capability wall (M3/3.1), not a loop, is what caps this case now.

**qythos9 looks slightly worse and it is not explained away:** edit fail 24% → 32%,
unrecovered no-ops 3 → 9 (spread across 3 runs, concentrated in the wallclock-death
e2e case), exec-bugfix 1.00 → 0.92, e2e 0.76 → 0.60. n dropped 8 → 6 and the e2e
truncations confound it, so this is *plausibly* noise — but not proven noise. Flagged
for re-check if qythos9 edit reliability surfaces again; do not credit the new message
with harming the good editor without a mechanism in the logs (per D50/D54).

| # | Decision | Why |
|---|---|---|
| D56 | Credit build 21 with fixing the qwencoder14 no-op dead-end; mark 1.4 done | Measured, like-for-like, against the exact metric it targeted: unrecovered no-ops 20 → 1, fail rate halved. |
| D57 | Do not chase the exec-bugfix score with more edit-path work | The edit-reliability lever is spent (fail 41%→20%) yet score is flat 0.25→0.29. Remaining headroom is fix *correctness* = capability (3.1), not the edit tool. |

**Tests:** 513 green (unchanged from build 21 commit).

---

## Round 14 — the syntax lever works but the ceiling is capability (`r14-syntax`, HEAD `489ea5e`, n=6)

Validation of build 22 (3.1a: inline `SyntaxError at line N` on `.py` writes).
e2e-spec-to-code only, both models, `--repeat 6`, vs the same case in r13-edithelp.

**The mechanism works.** qythos9 runs that reached pytest with a SyntaxError:
**5/6 → 0/6.** The inline warning fired in 3 runs and the model fixed the syntax
before ever running the tests. A real, general robustness win — a malformed `.py`
now names its own bad line one call after it lands, instead of surfacing later as
an opaque `<frozen importlib>` pytest *collection* traceback.

**But it did not lift the score.** `own_tests_pass` and `independent_spec_check`
are **0/12 across BOTH r13 and r14.** Removing the syntax roadblock just exposed
that the code underneath is *also* logically wrong — the wall moved from "won't
parse" to "parses but wrong," which is exactly where qwencoder14 already sat.

**The apparent overall gain is noise, not the lever.** e2e mean 0.61 → 0.69, but
it is entirely doc-stage variance: qwencoder14's `plan_has_tasks` swung 0/6 → 6/6
and `wrote_plan_doc` 2/6 → 6/6 (PLAN.md formatting, untouched by a syntax check),
lifting it 0.63 → 0.80; qythos9 swung the *other* way on the same check
(3/6 → 0/6), 0.60 → 0.58. A fake +0.17 on one row from n=6 — the cleanest
demonstration yet of why 2.1 (variance-aware gate) matters.

| # | Decision | Why |
|---|---|---|
| D58 | Keep 3.1a on general merit; do NOT credit it with an e2e score gain | Mechanism proven (pytest-SyntaxError 5/6→0/6) and it helps any coding task, but own_tests_pass stayed 0/12. The score move was doc-stage noise. |
| D59 | Close 3.1: the e2e ceiling is model capability, harness levers exhausted | Two rounds, both models, own_tests_pass 0/12. Docs near-maxed; stage 3 is reasoning (coercion/precedence, correct-once-parsing). Further gains need a stronger executor, not harness code. |
| D60 | Round 14 is the reference case for 2.1 | A +0.17 row swing from pure doc-format variance at n=6, with the mechanism under test provably not responsible. Any gate must not read this as signal. |

**Tests:** 519 green.

---

## Round 15 — the qythos9 edit-drop, and the tool the eval forgot to allow (`r20-replacelines-live`, HEAD `4635030`, n=6)

Builds 23–36 landed through commits (salvage, single-quote recovery, the
replace_lines fallback, the no-change fast-stall path) without full narrative
rounds; this round picks the thread back up at **build 37** and folds in a set
of this-session diagnostic sweeps (`r17-lookfirst-before` … `r19`) that were
superseded by the clean `r20` baseline below.

**The lever the feedback asked for did not reproduce.** The opening ask was to
"trim the opening noop guess" — the model blind-editing before gathering ground
truth. Measured on build 36 it is a **non-problem: 0/24** blind opens across the
baseline, **0/12** on a purpose-built raw-error probe. Builds 34–36 had already
closed it. Per D58/D59 discipline — no lever without a measured problem — no
prompt line was shipped. (D61.)

**Measuring that non-problem surfaced the real one.** The probe hands the model
a *misreported* `SyntaxError` (Python blames line 29/32; the true fault is a
six-quote docstring on line 24) with no "read first" steering. qythos9 failed
it **6/6** — but not by blind-editing (0/12). Its *correct* `edit_file` call was
emitted as Python single-quoted JSON that **dropped the closing `'`**, leaving a
trailing `}}`; the unterminated string ran to EOF and **swallowed the closing
`` ``` `` fence**, so `_closing_fence` found no closer and the whole call was
silently discarded. The turn ended with the fix never executed — the exact
"repeated edit attempts, turn fails without moving past the edit" flailing.

**Build 37 fixes it in the parser, only on the already-broken path.**
`_closing_fence` now remembers the first `` ``` `` seen inside an unterminated
string and returns it at EOF; `_loose_string`'s run-off-end return strips leaked
structural closers (`f"…"}}` → `f"…"`) via a new `_strip_structural_tail` (which
leaves truncated partials untouched, so `salvage_truncated_write` is unaffected).
Clean calls never reach these branches — proof: **qythos9 harness rows are
pixel-identical to baseline**, and the blindprobe went **6/6 BROKEN → 6/6 OK**
with *genuine* fixes (all functions intact, not hollow-outs). +4 tests.

**The r18 gate "FAIL" was an eval-infra bug, not a regression.** exec-bugfix
qwencoder14 dropped 0.50 → 0.29, dead-ending on "the tools this task needs are
not available in this session." Root cause: the harness never auto-approved
`replace_lines` (added build 34) — every case pins its own `allow_tools` and
all six omitted it — so build 34's own `edit_file → replace_lines` steering sent
the model into an un-approvable dead-end. Fixed across the default list **and**
all six pinned cases. qythos9 was untouched throughout (0.92 / 1.00 in every
sweep), proving the parser change was never implicated.

**The numbers — read across all four sweeps, because two of the four rows are
pure n=6 variance and one row is the only deterministic signal.** RL = whether
`replace_lines` was actually approvable for the case (r19's fix only touched the
unused *default* list; the pinned case lists still denied it, so only r20 had it
live):

| row | r17 (b36,no RL) | r18 (b37,no RL) | r19 (b37,no RL) | r20 (b37,RL) | reading |
|---|---|---|---|---|---|
| exec-bugfix::qythos9 | 0.92 | 0.92 | 0.92 | **1.00** | **deterministic** — the previously-dropped edit now lands; consistent across every sweep |
| exec-stall-trap::qythos9 | 1.00 | 1.00 | 1.00 | 1.00 | stable — flat everywhere |
| exec-bugfix::qwencoder14 | 0.50 | 0.29 | 0.46 | **0.92** | RL-consistent gain (0.92 vs ~0.4 without it), but n=6 — **suggestive, not credited** |
| exec-stall-trap::qwencoder14 | 0.92 | 0.72 | 0.33 | 0.44 | **n=6 variance** — every b37 draw is 0.33–0.72 regardless of RL; the 0.92 was a lucky baseline |

**The only clean signal is build 37 on qythos9's edit-bugfix (0.92 → 1.00,
consistent), plus the deterministic blindprobe (6/6 BROKEN → 6/6 OK).** The two
qwencoder rows must be read with the D60 discipline, and one of them nearly
tricked this very writeup (see the correction below).

**Self-correction — the "new flailing mode" first logged here was a D60 error.**
The first draft of this round credited the exec-stall-trap qwencoder14 drop
(0.92 → 0.44) to `replace_lines`: "handing a weak model the fallback creates a
fresh `edit_file`↔`replace_lines` non-converging loop." **Refuted by the fuller
table:** r19, with `replace_lines` *not* approvable, scored **0.33 — lower than
r20 with it (0.44)**. RL availability cannot explain a drop that is deeper
without it. Inspecting the r20 event logs confirmed the mechanism was misread:
4/6 runs terminate by the model **declaring "All tasks are completed" on a wrong
fix** (a capability/false-completion failure, 3.1-class), and the 2/6 that loop
are **caught correctly by the existing repeat guard**. The no-change guard keys
off a generic `res.no_change` (not the tool name) and resets on the
`read_file`/`update_plan` calls the model interleaves — so it is working as
designed, not slipping. There is no confirmed cross-tool guard gap; the drop is
variance over a capability wall. The lever is retracted (D64 below).

| # | Decision | Why |
|---|---|---|
| D61 | Do NOT ship a "look before you edit" prompt lever | The opening-noop it targets is 0/24 on build 36 — already closed by builds 34–36. No lever without a measured problem (D58/D59). |
| D62 | Build 37 (`_closing_fence` EOF recovery + `_strip_structural_tail`) is correct and the round's one deterministic win | Fires only on malformed input; qythos9 rows pixel-identical to baseline on the clean path; blindprobe 6/6 BROKEN→OK with genuine fixes; exec-bugfix qythos9 0.92→1.00 consistently. |
| D63 | `replace_lines` must be auto-approved wherever `edit_file` is — **on principle, not for a score** | It is `_PATH_MUTATING` like the other editors and the loop actively steers toward it; denying it in eval dead-ends runs and hides a real product tool. The exec-bugfix qwencoder gain is RL-consistent but n=6 — do NOT credit a score delta. |
| D64 | **RETRACTED** (superseded self-correction): the exec-stall-trap qwencoder14 0.92→0.44 move is n=6 variance over a capability wall, NOT a replace_lines flailing mode | r19 (no RL) scored 0.33 < r20 (RL) 0.44, so RL cannot be the cause; event logs show correct guard behavior + wrong-fix false-completion. A textbook D60 trap — caught in review, not shipped as a lever. |

**Tests:** 572 green (+4 toolparse regression tests for the build-37 fix).

---

## Round 16 — the gate learns that a sweep is non-stationary (build 38, HEAD `ce31637`+)

Round 15 spent four sweeps untangling one number by hand, because the gate
false-FAILed on r18 and the row that moved was pure variance. This round makes
that reasoning the gate's own, after first proving the problem is real.

**The finding: a single n=6 sweep of these models is non-stationary.** Two
build-identical sweeps — r18 and r19, both build 37, same code — produce a
"significant" per-row drop on exec-stall-trap qwencoder14 (**0.72 → 0.33**), and
a one-sided permutation test over the real per-run scores calls it **p=0.030**.
Nothing changed but the RNG. That row's per-sweep mean wanders 0.33–0.72 across
draws; its within-sweep spread does *not* predict the wander (r19 was
`[0.333]×6`, std 0, yet drifts to r18's 0.72). So **no single-sweep statistic —
threshold, CI, or permutation p — can be trusted to auto-FAIL a noisy row**, and
a naive permutation gate is *more* trigger-happy, not less: it fired on the
same-code pair.

**The gate the finding demands (build 38).** `summarize()` now keeps each row's
per-run `scores`, and `compare()` classifies every row against its baseline:
- `ok` — no material drop (< 0.10).
- `noise` — dropped, but the candidate's 90% bootstrap CI still overlaps the
  baseline's. Within the band; nothing to act on.
- `regression` (**hard FAIL**) — CIs separated **and both sweeps are internally
  consistent** (per-run std < 0.10 each). A clean drop between two tight sweeps
  is the only per-row shape that can auto-fail.
- `review` (**advisory, never fails**) — CIs separated but at least one sweep is
  internally noisy. Could be a regression, could be drift; a human (or the queued
  interleaved runs) decides.

An overall backstop pools **only the trusted (non-review) rows** and hard-fails a
*broad* slide — ≥2 stable rows sliding together past a low 0.05 floor — so a
harness change that mildly hurts everything is still caught, while a single noisy
row can never manufacture a FAIL. Legacy result files (no per-run scores) fall
back to the old fixed 0.15/0.05 thresholds; `compare` re-summarizes from the
stored `runs` so pre-build-38 sweeps get the new path for free.

**Validated against the real sweeps.** Every same-code pair (r18↔r19↔r20) now
returns **PASS (with REVIEW)** — the non-stationarity can no longer trip the hard
gate. The genuine build-37 improvement (r17→r20) also PASSes, with its two real
gains shown (`exec-bugfix` qwencoder +0.42, qythos9 +0.08) and the noisy
stall-trap drop flagged REVIEW rather than FAILed. Synthetic clean regressions
(`[1.0]×6 → [0.5]×6`) still hard-FAIL; a broad 3-row 0.07 stable slide FAILs via
the backstop; a lone deterministic 0.05 drop does not.

| # | Decision | Why |
|---|---|---|
| D65 | Treat a single n=6 sweep as non-stationary; the gate must tolerate per-sweep drift up to ~0.4 on noisy rows | Two same-code sweeps (r18/r19) differ at p=0.030 on exec-stall-trap qwencoder. Proven, not assumed — measured on the real per-run scores. |
| D66 | Ship the variance-aware gate (per-run scores + bootstrap CIs + advisory permutation p): hard-fail only internally-consistent rows and broad trusted-pool slides; route noisy drops to REVIEW | Makes Round 15's four-sweep hand-analysis the gate's default. The visibility half (per-row CI table) is the "poor visibility" fix; the advisory half stops the false FAILs. |
| D67 | Queue interleaved paired runs + larger n as the real fix for noisy-row *attribution* | The gate can only *tolerate* single-sweep noise, not resolve it. To actually credit/reject a REVIEW row, run baseline and candidate interleaved (shared RNG conditions) at higher n. Next step, not this round. |

**Tests:** 581 green (+9 harness tests for the variance-aware path: clean
stable-drop FAILs, noisy-drop REVIEWs+PASSes, same-code noise does not fail,
improvement/tiny-drop pass, broad backstop fails, review-row excluded from
backstop, plus bootstrap/permutation unit checks).

---

## Round 17 — watching a real run finds the verdict was invisible (build 39)

Stepped out of the eval numbers and drove a weak model (qwencoder14) through a
real bug-fix in a scratch workspace — the north star is the *lived* experience,
and the gate is only a proxy for it. The model converged cleanly (correct edit,
`pytest` green), so the flailing pain didn't surface — but the **visibility**
pain did, sharply.

**What the user actually sees.** `format_result` summarized a multi-line tool
result by its **first line**. For the one result that decides everything —
`pytest` — the first line is the banner, so the render was:

```
  ⚙ bash pytest test_cart.py
    ✓ ===== test session starts =====…  (+9 more lines)
```

The `3 passed` / `2 failed` verdict is buried in "(+9 more lines)", and the green
✓ means only "the tool ran," not "tests passed." Reproduced by feeding the run's
own telemetry back through the real renderer — a user watching this could not
tell success from failure.

**Fix (build 39, `format_result`/`_salient`).** Surface the *conclusion* line —
scan from the end for a verdict/error pattern (pytest tally, FAILED/PASSED/ERROR,
Python exceptions, Traceback, `fatal:`) — and flip the ✓ marker to ✗ when the
output reports failure even though the tool itself returned cleanly (pytest
exiting nonzero as data). Output with no recognizable verdict (`ls`, etc.) is
unchanged: first line, green ✓. Interactive path only; headless `-p` never
rendered these lines.

This lands on **both** north-star pains. Visibility: the verdict is legible at a
glance. Flailing: a looping model now renders `✗ 1 failed` on each identical
retry — visibly stuck — where before it showed the same "test session starts"
banner every time, indistinguishable from progress.

| # | Decision | Why |
|---|---|---|
| D68 | Render the tool-result **conclusion**, not its first line; make the ✓/✗ marker reflect the command's reported outcome | The single most decision-relevant result (test/build verdict) lands last, under a banner; a first-line summary hid it and a tool-ran ✓ contradicted a failing run. Found by live observation, the thing the eval score can't show. |

**Tests:** 585 green (+4 render tests: pytest verdict surfaced not the banner,
failing-command marker flips, traceback exception surfaced, plain output
unchanged).

---

## Round 18 — gate "done" on a green test the model actually saw (build 40)

**North star.** MID-TASK FLAILING's ugliest tail is a *false* finish: the model
edits, never runs the suite to green (or runs it and it's red), then ends the
turn asserting "the tests should now pass." The run declares done while
`checks['tests_pass']` is False — the single largest source of a self-declared
completion that is actually wrong.

**Measurement first (Option C).** Before adding any gate, measured the proposed
signal against ground truth on every self-declared-done exec/e2e run in the
results corpus. An **ever-saw-green** gate — did a green pytest tally appear in a
bash result this turn? — has *perfect discrimination* on the 89 such runs:

- Catches **4/4** false-completions (all `exec-stall-trap::qwencoder14`, each
  saying "tests…should now pass" without ever seeing green).
- Blocks **0/85** legitimate completions (no false nudge on a run that really
  finished).

The signal is safe to gate on: it never fired on a run that had genuinely
converged.

**Fix (build 40, `loop.py`).** Track a per-turn `_saw_green_test`, set the moment
a **bash** result matches a pytest pass tally (`\d+ passed`) with no failure/
error/traceback token. In the finish cascade — after the open-plan-tasks nudge,
before announced-intent — a new branch fires **once** when the final content
makes a test-specific pass claim (`_TEST_CLAIM_RE`: "tests …pass/passing/green/
succeed") AND no green was seen this turn: nudge to run the suite to green, then
carry on. Scoped tightly so a design-doc/plan task that never runs tests can't
trip it (the claim regex requires the word "tests" near a pass verb; "I passed
the path to the function" and "the design document is complete" do not match).
The gate keys on bash alone, so a `read_file` of a fixture that contains
"5 passed" can't spoof green.

| # | Decision | Why |
|---|---|---|
| D69 | Gate a "tests pass" finish on the model having SEEN a green pytest result this turn; nudge once if it asserts pass blind | Measured perfect discrimination (4/4 caught, 0/85 false) — the largest false-completion source, and the signal never fired on a converged run. |
| D70 | Restrict the claim trigger to test-specific language and the green signal to bash results | Keeps doc/plan tasks (which never run tests) and fixture reads that merely contain "N passed" from tripping the gate. |

**Tests:** 591 green (+9 loop tests: unverified claim nudged once, green-seen
claim trusted, non-test finish never gated, gate fires only once, plus
`_looks_green_test` / `_TEST_CLAIM_RE` unit coverage). Interactive and headless
share the loop, so this gate applies to both.

**Live validation (r24-seengreen-gate, exec-stall-trap qwencoder14 n=6, build
40).** Ran the exact case the historical false-completions came from, now with
the gate live. Result: **0 seen-green nudges fired — and correctly so.** None of
the 6 runs reached a finish falsely claiming tests pass: five were stopped by the
**repeated-call** stall guard first (it fired 7×), and the one clean finish (run
2) had just seen a *failing* suite (`....FF`) and made no pass claim, so there was
nothing to gate. This confirms the **safety property live** (no false nudge on
any of 6 real runs) but did not reproduce the target false-completion — a
consequence of the non-stationarity documented in Round 16: this n=6 draw fell
into the repeated-call-stall path rather than the false-"tests pass"-finish path.
Net: the gate is validated by a three-way triangle — unit tests (fires once,
correctly), offline discrimination (4/4 real historical catches, 0/85 false), and
live safety (0/6 false) — and sits as a **low-frequency backstop** for the
residual false-completions that slip past the upstream stall guards. Not worth
burning more GPU to catch a probabilistic live firing; the offline catches are
the proof the residual is real.

---

## Round 19 — devstral24 is not a capability lever on the hard cases (r22/r23)

**Option D question.** The two hardest cases sit at the 3.1 capability wall on
the 9–14B incumbents. Does the heavier local executor, **devstral24** (Mistral-
Small 24B), clear either? Ran it n=6 on both: `r22-devstral-e2e`
(e2e-spec-to-code) and `r23-devstral-stall` (exec-stall-trap). Answer on both:
**no.**

**e2e-spec-to-code — same wall, same failure mode (n=5; a 6th was cut when the
chained sweep was stopped).** devstral24 mean **0.74**, squarely in the incumbent
band:

| model | e2e mean (recent) | own_tests_pass |
|---|---|---|
| devstral24 | 0.74 (r22, n=5) | **0/5** |
| qwencoder14 | 0.71 (r13–15) | **0/6** each sweep |
| qythos9 | 0.64 (r13–15) | **0/6** each sweep |

On all 5 runs devstral24 wrote every artifact (design doc, plan, module, tests)
but **`own_tests_pass=false` and `independent_spec_check=false` — 0/5 on both.**
The entire 0.74 is scaffolding credit; both correctness checks are zero. This is
the *identical* failure to the incumbents (0/6 own-tests-pass across every recent
e2e sweep): all three local models write plausible code whose logic is wrong.
The wall is universal, not model-size-bound.

**exec-stall-trap — devstral24 no-ops; qythos9 already solves it.** mean **0.67**,
tests_pass **0/6**, and the telling metric: **0 tool calls per run.** devstral24
announces intent, eats the one announced-intent nudge, and escapes clean in ~17s
without ever attempting the fix. It out-scores qwencoder14 (0.33, which gets
*baited into grinding* — the case's purpose — 3–50 tool calls, 0/8 pass) only by
refusing to engage, banking the "escaped-without-grinding / suite-intact" credit.
But the config default **qythos9 already solves this case outright** (≈0.98,
tests_pass 8/8, ~4 tool calls). devstral24 is strictly *worse* than the model
we'd actually reach for.

**A harness observation.** Both stall sweeps tripped the "gen ≤30 ch/s → box
throttled, don't use as baseline" guard — a **misfire**. The concurrent e2e runs
on the same box clocked **45.8 ch/s**, so the box was healthy; the low rate is
run-*length*: a 2-iteration, 205-char, 17s no-op is dominated by fixed prompt-
processing overhead. The floor should be gated on `gen_seconds` (only flag *long*
runs that are slow), or the warning will keep crying wolf on legitimately short
runs. Minor; noted for a later harness tweak.

| # | Decision | Why |
|---|---|---|
| D71 | Do **not** adopt devstral24 as a hard-case lever | e2e: 0.74, own_tests_pass 0/5 — same wall as the 9–14B models. stall: 0 tool calls, 0/6 pass — worse than qythos9, which already solves it. The 24B capacity buys no correctness on the hard cases. |
| D72 | The e2e capability wall is model-size-invariant across the local pool | All three local models get own_tests_pass 0/6(5) on e2e — plausible code, wrong logic. Confirms 3.1 is capability-bound; the payoff is in harness levers (visibility, seen-green gate), not model-swapping. |

**Closes the "just run a bigger local model" hypothesis.** CLAUDE.md's framing of
devstral24 as *insurance for larger tasks* stands, but it is not a capability
upgrade on the cases that actually fail.

---

## Round 20 — a truncated tool block was being surfaced as the final answer (build 41)

Not a score run — a **visibility defect found by watching r22 devstral24 e2e
logs.** Read `turn_end.result` (the actual final answer; `assistant_end` only
carries `chars`) across the 6 e2e runs: **5/6 ended with the "answer" being a
raw, unclosed ` ```tool ` JSON fence.** devstral24's edits are long; they hit
`max_tokens` mid-call, so the reply ends inside an opened-but-never-closed tool
fence. The parser recovers nothing from it, the truncation nudge fires up to
`max_truncated_retries`, and then the loop **fell through to `return content`** —
handing the user a half-written JSON blob as the final result. The single worst
visibility failure mode: not a stall you can see, but garbage dressed as an
answer.

**Fix (loop.py, build 41).** After the truncation-nudge branch, once the retry
budget is spent and `_looks_truncated(content)` is *still* true, stop cleanly via
`_stop("… kept getting cut off mid tool call — try a smaller step or writing less
at once")` instead of returning the block. Scoped to the broken-fence case only —
a prose reply cut mid-sentence (no dangling fence) is at least readable, so it
keeps falling through. Ordering is deliberate: the prose-repeat guard (fires only
on a *repeated* reply) sits ahead of it, so distinct truncated replies reach the
new stop. Test `test_exhausted_truncation_stops_cleanly_not_raw_block` scripts 3
distinct truncated ` ```tool ` replies and asserts the turn returns a `⏹ stopped`
message with no raw fence. Full suite 596 green.

| # | Decision | Why |
|---|---|---|
| D73 | A truncation-exhausted turn stops cleanly rather than returning the raw block | Falling through to `return content` surfaced an unclosed ` ```tool ` fence as the final answer on 5/6 devstral24 e2e runs — the worst invisible failure. Directly serves the POOR-VISIBILITY pain. ROADMAP 4.7. |

---

## Round 21 — build-41 regression check + a non-stationarity control (2026-07-25)

Goal: confirm the two new finish-cascade nudges (4.6 seen-green gate, 4.7
truncation-stop) don't **misfire** on the loaded workhorse. Ran qythos9 on the
test-claiming cases — exec-bugfix, exec-from-plan, e2e-spec-to-code — at n=4
(r25-build41-regress).

**Misfire check: clean pass.** Across all 12 runs the seen-green nudge ("tests
claimed passing but never seen green") fired **0 times**, and no run surfaced a
raw ` ```tool ` block or a truncation stop. The tell: the two exec-from-plan runs
that *did* reach green ended with an "All tasks completed" claim and the gate
correctly **trusted** them (scored 1.00) rather than nagging. Builds 40/41
introduce no spurious nudges.

**A scare that became a control.** exec-bugfix scored **0.50 on all four** runs
(each repeat-stopped), against a historical qythos9 baseline of 0.92 (r13/r16/r19).
That looked like a regression — until noting the new nudges never fired here, so
40/41 *couldn't* be the cause. Confirmed it with a same-session A/B: checked out
the pre-build-40 commit (ce31637, build 30 — has the plan fixes, lacks 40/41) and
re-ran exec-bugfix qythos9 n=4 (r26-pre40-control) against the *same* loaded
server. Result: **0.50 / 1.00 / 0.50 / 0.50 = 0.625** — the same depressed range.

| build | exec-bugfix qythos9 n=4 | vs historical |
|---|---|---|
| 41 (candidate, r25) | 0.50 0.50 0.50 0.50 → **0.50** | 0.92 (r13/r16/r19) |
| 30 (pre-40 control, r26) | 0.50 1.00 0.50 0.50 → **0.625** | same session as r25 |

The pre-40 code scores the *same* range as build 41 today; the gap to 0.92 is
**model non-stationarity** (the documented r18-vs-r19, p=0.03-on-identical-code
phenomenon), not my changes. The 0.625-vs-0.50 delta is one run flipping — n=4
noise. In every run the loop behaved correctly: the model fixed 2 of ~4 bugs,
then stalled submitting `new==old` on the rest (the capability-bound
"highlighting" dead-end from Round 20's edit-failure mining) and was cleanly
repeat-stopped.

| # | Decision | Why |
|---|---|---|
| D74 | Builds 40/41 ship — no misfire, no regression | 0/12 spurious new-nudge firings; the exec-bugfix dip reproduces identically on pre-40 code in the same session. |
| D75 | A historical cross-session score is NOT a valid baseline for a sweep run today | Same code (build 30) scores 0.62 today vs 0.92 in its origin session. Only a **same-session** A/B (or interleaved paired runs) controls for model drift. Reinforces the queued paired-runs item. |

---

## Round 22 — the succeeding-but-non-converging edit loop (build 42)

**User-reported, gemmacoder12 (2026-07-26):** "models tend to loop on the same
edit like they don't realize the work is done." The trace: the model re-issued a
byte-**identical** `replace_lines(start=136, end=137, new='    with tempfile…')`
five-plus times. Each returned `✓ replaced lines 136–137`, but the diff marched
down the file — `@@ -144 → -146 → -148 → -150` — because the edit kept
**duplicating** the `source_path = …` / `clone_repo(...)` lines: the fixed line
numbers 136–137 pointed at ever-shifting content as the file grew 2 lines per
pass. The model announced "the file now parses correctly" every single time.

**Why the repeat guard missed it.** `repeat_streaks` only grows a signature's
streak when the **result echo is unchanged** (so that re-running a test between
edits, or a fresh read, counts as progress, not a stall). Here the call args were
byte-identical every iteration but the *success echo* changed each time (a new
diff offset), so the streak reset to 1 forever — the guard never tripped and the
corrupting edit ran unbounded.

**The correction to Round 20.** Round 20's edit-failure mining concluded residual
edit-flailing was capability-bound. That was incomplete: it only examined
**failed** edits (old==new, ambiguous, not-found). This is a **succeeding**
edit that never converges — a genuine harness gap the failed-edit lens couldn't
see. A user watching a live session caught what the offline metric missed.

**Fix (loop.py, build 42).** A byte-identical call to a content-mutating tool
(`_MUTATING_EDIT_TOOLS` = write_file/append_file/edit_file/replace_lines) now
counts toward the repeat streak **regardless of the shifting echo**. It stops
after `max_repeat_calls-1` applications (2 by default) instead of looping. When
the tripped repeat is a *varying-result* mutating edit (tracked in
`repeat_varied` — the true duplicating signature, distinct from a constant-result
no-op), a tailored `_nudge_repeat_edit` fires: the file has already changed, you
may be duplicating content or your line numbers shifted — stop, re-read, make one
corrected edit. A plain no-op repeat keeps the existing generic message. +1 loop
test (`test_repeated_mutating_edit_stops_despite_varying_echo`); 597 green.

| # | Decision | Why |
|---|---|---|
| D76 | A repeated byte-identical mutating edit is a loop even when its result echo differs | Re-applying the same replace_lines/edit_file is never progress; on a line-number edit against a shifted file it silently duplicates content (gemmacoder12). The result-unchanged reset is correct only for re-run tests/reads. |
| D77 | Live user observation outranks offline metric conclusions | Round 20 called residual edit-flailing capability-bound from FAILED-edit mining; this SUCCEEDING-edit loop was invisible to that lens and only surfaced in a real session. Watch live runs, not just scores. |

---

## Round 23 — three anti-cycling levers on top of the build-42 guard (builds 43-45)

Build 42 (Round 22) catches the repeated-edit loop **after** it starts. The user
then asked the deeper question — *why* do these models repeat and cycle, and can
we "add nudges to remember what it did after each edit" — and approved building
all three levers that attack the causes. All Opus-tier (tool descriptions +
loop.py), shipped 2026-07-26.

**Root cause (delivered to the user).** Weak local models cycle because: (1) the
next action is a near-deterministic function of the most salient context, so a
stale mental model reproduces the same edit; (2) editing is **open-loop** — they
edit without ever running or re-reading, so nothing tells them the edit landed or
duplicated; (3) **line-number** edits (`replace_lines`) drift as the file shifts,
turning "the same fix" into new duplications; (4) the first full read gets
anchored on and the ±3-line echo / compaction can't dislodge it.

**The three levers.**
- **build 43 — steer off line-numbers.** Reframed the tool descriptions:
  `edit_file` is now "the PREFERRED editor — content-anchored, so it can't drift";
  `replace_lines` is "LAST-RESORT — PREFER edit_file", stating line numbers go
  STALE and a repeat DUPLICATES content. Weak models choose their tool from these
  descriptions, so this prevents the 4.9 loop at the source. +2 guard tests.
- **build 44 — verify-gate.** Per-file counter of consecutive mutating edits with
  no look at ground truth (`agent.max_unverified_edits`, default 3). A verify bash
  run (py_compile/pytest/python/ruff/mypy) or a re-read of the file re-arms it;
  crossing the threshold earns a one-time nudge to run or re-read before editing
  again. `ls`/`cat` is not credited (`_is_verify_bash`). Closes the open loop. +4
  tests.
- **build 45 — episodic action-ledger.** Both cycling nudges (4.9 repeat-edit and
  the verify-gate) now prepend a terse turn recap: "So far this turn you have:
  edited f.py 5×, run a check 1× (still not green)." Selective by construction —
  only those already-gated moments — so no context bloat / tool-JSON corruption.
  +2 tests. Suite 605 green.

**Not yet validated on a live/eval run.** Per D77, offline metrics can't see a
converging loop, so a green suite is necessary but not sufficient. Next: a live
gemmacoder12/qythos9 session on a duplication-prone bugfix + an eval sweep,
watching that (a) the levers fire when they should, (b) no false-positive nudges
on legitimate multi-edit work.

| # | Decision | Why |
|---|---|---|
| D78 | Fix cycling at the cause (tool choice + open loop), not just at the symptom | Build 42 stops a loop already running and burns iterations to get there; steering off line-numbers and gating unverified edits stop it forming. Layered defense — the guard remains the backstop. |
| D79 | Recap only at already-gated cycling moments, never every turn | A per-turn ledger would bloat context and (brace-dense) corrupt weak models' tool JSON. Attaching it to the repeat-edit / verify nudges makes it self-limiting and lands it exactly when the model has lost the thread. |

---

## Round 24 — the live A/B that caught Lever 1 as a regression (build 46)

Followed through on Round 23's "not yet validated" with a **same-session paired
A/B** (the ROADMAP 2.4 recipe: `git checkout <ref>` between arms, same fixture,
same loaded model — controls for the non-stationarity D75 warns about). Fixture:
`sync_classes.py` with an **empty `with`-block** (an IndentationError — the exact
bug class the user reported). Task: "fix so `py_compile` succeeds." Model:
gemmacoder12 (the reported model, already loaded). n=5 per arm, scored on
compile-PASS + no content duplication + clean stop.

| arm | build | fixed | duplicated | finish |
|---|---|---|---|---|
| control | 42 | **5/5** | 0/5 | clean stop |
| levers | 45 | **0/5** | 0/5 | clean stop |
| corrected | 46 | **5/5** | 0/5 | clean stop |

**Build 45 (all three levers) turned a 100%-fix into a 0%-fix.** The culprit was
Lever 1 (build 43). This is an *indentation* fix, and `edit_file` is content-
anchored: it PRESERVES the file's existing indentation, so an indent-only change
collapses to a no-op and is rejected (the 1.5/1.6 mechanism). `replace_lines` is
the *correct* tool for it — and control proved it, fixing 5/5 with replace_lines
in 4/5 runs. Lever 1's "LAST-RESORT — PREFER edit_file" blanket demotion drove
the model onto the one editor that structurally cannot fix an indent bug; it
looped on no-op edits and fixed nothing. The cruel irony: the user's original bug
was *also* an indentation fix, so the lever hurt exactly what it meant to help.

**Fix (build 46).** edit_file's description now states it cannot make an
indentation-only change and routes such fixes to replace_lines; replace_lines
reads as the RIGHT tool for indentation/whitespace (not a demoted last resort)
while keeping the stale-line-number / duplication warning. Re-ran the arm: 5/5
fixed, back to control parity. **Levers 2 and 3 fired on all 5 corrected runs**
(verify-gate "unverified edits" + the build-42 "repeated edit" nudge) **without
breaking the fix — confirmed harmless; Lever 1 was the sole regressor.**

No arm ever duplicated content — the original unbounded-duplication failure did
not reproduce on gemmacoder12 in *any* arm this session (non-stationarity; the
build-42 guard is the backstop for when it does). And note all three arms
"finish" by repeat-stop after landing the fix, not a clean self-terminated done
— the known solved-then-repeat-stop pattern (1.7/r15), orthogonal to these levers.

| # | Decision | Why |
|---|---|---|
| D80 | A behavior-shaping change (tool descriptions, nudges) MUST be validated with a live paired A/B before it's trusted, not just a green unit suite | Lever 1 passed 605 unit tests and looked obviously good, yet a 5-run A/B showed it converted 5/5→0/5 on its target bug class. Unit tests prove the mechanism fires; only a live run on the real model shows the *net behavioral effect*. Extends D77. |
| D81 | edit_file cannot fix indentation — route indent/whitespace fixes to replace_lines, never away from it | edit_file preserves existing indentation by design (1.5/1.6), so an indent-only edit is always a no-op. Steering weak models off replace_lines for these is actively harmful; the description must send them TO it. |

---

## Round 25 — syntax-reject guard: corruption never lands (build 47)

The user pasted a **build-46 trace** ("still seeing failed edits and repeating,"
gemmacoder12). Diagnosis: the very first `edit_file` produced an unclosed paren
(`dest_files.add(str(rel / f)`) plus a triplicated print/if block, and it
**landed** — the syntax check was only advisory (`_syntax_warning` appends a note
but still writes). From then on the model was fighting a file it had itself
broken, cycling on old==new no-op edits until the repeat-stop fired (10 iters, 3
nudges, nothing accomplished). The anti-cycling levers (23/24) all *fired* in
that trace — verify-gate prompted a re-read, the repeat guard eventually stopped
it — but they can only react to the flail; nothing prevented the corruption that
*caused* it.

**Build 47 (`_syntax_reject`, fs.py).** Refuse to apply an edit that flips a .py
file **valid→invalid**: recompile the post-edit text and, if it now raises a
SyntaxError where the pre-edit text parsed, return `is_error` with the file
UNCHANGED (last-good, already-read state) and a targeted retry message. Scoped
tight so it never blocks legitimate repair: only `.py`, and only the
valid→invalid transition — if the file did **not** parse before the edit (the
empty-with-block / broken-file case), any edit passes through (the advisory
warning still covers it). Wired into both `edit_file` and `replace_lines` before
their writes. +6 fs tests (reject on break, allow fixing a broken file, normal
change still lands, non-.py ignored, both tools); 2 old warn-and-apply tests
rewritten to expect rejection. Suite **611 green**.

**Live paired A/B (gemmacoder12, logging-injection fixture, n=4/arm).** A valid
`sync_classes.py` whose `get_changed_files` references undefined vars; task = "make
it verbose, keep it valid Python." `git checkout HEAD~1 -- locode/tools/fs.py`
swapped in the build-46 warn-and-apply fs.py for the control arm (same session,
same loaded model — D75).

| arm | build | compile | iters | outcome | flail |
|---|---|---|---|---|---|
| REJECT | 47 | 4/4 PASS | 3,4,1,4 | 3 answered, 1 wallclock timeout | mild (≤2 nudges) |
| WARN | 46 | 4/4 PASS | 4,4,**6,11** | 2 answered, **2 terminal repeat-stops** | heavy — "edit changed nothing", "unverified edits", 6 nudge types |

b47 flailed **less**: fewer iterations, and **0 terminal repeat-stops vs 2/4** on
b46 (b46 r3 reproduced the exact "repeated the same tool call without making
progress" stop from the user's report). Critically, **no false-positive
reject-loop** appeared in any b47 run — the risk that Lever 1 (Round 24)
materialized as a regression. Caveat: *neither* arm happened to emit a
corrupting edit that landed, so both were all-PASS and the guard's
corruption-prevention couldn't be demonstrated *directly* here — that mechanism
is proven by the units + the user's real trace. The A/B's contribution is
narrower but exactly what D80 demands: the behavior change causes **no
regression** and is a **directional win** on the live model.

| # | Decision | Why |
|---|---|---|
| D82 | A guard that *prevents bad state from landing* is worth more than one that reacts to the flail it causes | Rounds 22-24 all react *after* a corrupting/no-op edit lands; the user's build-46 trace shows the model can't recover from a file it broke on step one. Refusing the valid→invalid write keeps the file in a state the model can still reason about. Prevention > detection for weak models. |
| D83 | Scope a content guard to the *transition*, not the *state* — reject valid→invalid, never invalid→anything | A guard keyed on "output is invalid" would block the model from fixing an already-broken file (the empty-with-block case), re-introducing a Lever-1-style regression. Keying on the transition (parsed before, doesn't parse after) refuses only genuine corruption and always lets repair through. |

---

## Round 26 — overnight battery: two "clearer signal" flail fixes, both rejected (2026-07-27)

First round driven end-to-end by the **observability suite** (Round 25's
`replay.py` + `--show-events`) and a new **prompt battery** (`evals/night/run_battery.py`:
8 varied real tasks — logic/indent/undefined-var/syntax bugs, add-logging,
new-module, refactor-rename, add-test — each run captured as transcript + event
log and scored on *did the task get done* AND *how hard did it flail*).

**Harness bug caught first (the instrument before the data).** Pass-1 initially
reported every pathology count as zero: `--log-events` used a ROOT-relative path
but the `locode` subprocess runs with `cwd=workdir`, so the logs were written
nested under the workdir and `replay.load()` read empty files. Fixed by making
the outdir absolute (`.resolve()`). Lesson: verify the instrument before
trusting a night of numbers.

**Pass-1 (16 runs, both models).** Ranked the pathologies. Standout: **indent-bug**
— both models flail (qythos9 fails outright; gemmacoder12 fixes the file then
repeat-stops). Root causes differ: qythos9 uses **tabs** where the file uses
spaces + mis-ranges the replacement (orphan duplicate `return`), and the
syntax-guard never engages because the start state is *already invalid* (D83).
gemmacoder12 *fixes* the file on the first `replace_lines`, then can't tell it's
done and loops rejected/no-op re-edits to a repeat-stop.

**Two fixes, two paired same-session A/Bs (D80), both on the hypothesis "give the
model a clearer success signal and it will flail less" — toggled via
`git stash push -- <file>` so control/treatment are adjacent in time
(`evals/night/ab.py`).**

| build | change | A/B (cases × 2 models × 3 reps) | verdict |
|---|---|---|---|
| **49** | bash silent success `(no output)` → `(exit 0 — command succeeded, no output)` | indent-bug+add-test | flail-**NEUTRAL** (done 12/12 both; repeat-stops 2↔4, repeats 10↔12 inside indent-bug's own run-to-run noise) |
| **50** | edit "✓ now parses cleanly" on invalid→valid .py + reject-msg "already parses as-is" hint | indent-bug+undefined-vars+new-module | flail-**NEGATIVE** (done 17/17 both; repeat-stops **1→5**; on the *target* case gemma went 0-stops-all-done → 2-stops+1-fail) |

Build 49 **kept**, reframed honestly as a **visibility** win (pain #1: `(no output)`
is ambiguous to a human reading `--show-events` too) and documented as
flail-neutral — *not* claimed as a flail fix. Build 50 **reverted** in full
(source, tests, bump) — a rejected hypothesis, consistently wrong-direction on
the exact case it targeted. Transcript diff of the target: control did
fix→`py_compile`(new call)→done; treatment did fix(+parse-note)→**re-ran the
identical `replace_lines`**→no-op→repeat-stop.

The loop's repeat handling was re-audited and is **sound**: it nudges-and-
*continues* first (loop.py:616) and only stops on persistence — the model gets a
chance to recover and ignores it. So the stop isn't premature; the model is
stubborn.

| # | Decision | Why |
|---|---|---|
| D84 | Weak-model mid-task **flail does not yield to clearer tool-result text** — route flail fixes elsewhere | Two paired A/Bs (b49 neutral, b50 negative) on "signal success more clearly" failed to help and one hurt. The models re-run/re-edit after a success regardless of how legibly it's signaled: this is a planning/**stopping-behavior** problem, not a tool-result information deficit. Additive result/nudge text is spent budget. Future flail work → loop mechanics (already well-tuned) or accept it's capability-bound; spend the "clearer output" lever on **human visibility** instead, where it demonstrably pays (b49). |
| D85 | The **pass-1 baseline is not a valid control** — only a same-session stash-toggle A/B is | indent-bug flailed to a repeat-stop in pass-1 but ran clean as the A/B control arm minutes later (same code). Non-stationarity (D75) dominates the flail metric across sessions; cross-session before/after "improvements" are noise. |

---

## Round 27 — overnight battery cont'd: hallucinated-verify false-completion, gated (2026-07-27)

Continuation of the overnight loop (Round 26). After D84 closed "clearer tool-
result text" as a flail lever, the battery surfaced a **distinct third
pathology** — not flail, not invisibility, but **confident premature/false
completion.** (Build-number note: the Round-26 "build 50" parse-note change was
reverted *before* it ever landed, so the last committed build was 49; **build 50
on `main` is this verify-gate.**)

### The reproduction (battery `syntax-fix`, gemmacoder12, reps=3+5)
Given `parser.py` = `def parse(line)` (missing colon) and "fix it so
`py_compile` succeeds", gemmacoder12 reliably (`0/5` control done):

```
  ⚙ read_file parser.py
The file parser.py is syntactically correct and already compiles with
python3 -m py_compile parser.py. There is no syntax error to fix.
  ▤ plan 2/2 done
```

It **hallucinated** — read the broken line and asserted it compiles **without
running py_compile** — then marked the plan done and self-terminated. The file
was left broken. Crucially this is **invisible to every pathology counter**:
`done=N`, but `f0 n0 r0`, stop reason a clean "answered". Only the battery's real
per-case `check()` (does the file actually compile) catches it. First read as a
*plan* defect (plan.py:156 replies "All tasks are done. Give your final answer
now."), but the transcript shows the plan mark is **downstream** of the bad
verify — the model believed the file was fine. So the lever is *forcing
verification*, not editing the plan message. plan.py left unchanged.

### The fix (build 50) — extend the seen-green gate to compile/run/import
Exact sibling of build-40's test gate (which caught "tests pass" false-
completions with perfect discrimination). In `locode/agent/loop.py`:
- `self._saw_verify_ok` — set True when a bash call that `_is_verify_bash`
  (py_compile / python / ruff / pytest / …) exits **clean** (`is_error` False;
  a failing py_compile correctly leaves it False).
- `_VERIFY_CLAIM_RE` — matches the compile/run class: *compiles [cleanly]*,
  *py_compile succeeds*, *syntactically correct*, *no syntax error*,
  *runs/imports without error*. Deliberately not test claims (those go through
  `_TEST_CLAIM_RE`).
- Finish-cascade gate (sibling of the test gate): if the reply claims a check
  passed **and** `not _saw_verify_ok`, nudge **once** to actually run it, then
  return whatever the model says next. Double-gated → a run that really verified,
  or a task needing no shell check, can't trip it.

**Latent crash found + fixed (the gate surfaced it):** `_is_verify_bash` did
`(cmd or "").lower()` and raised `'list' object has no attribute 'lower'` when a
model emitted `cmd` as an argv **list** (["python3","-m","py_compile","x.py"]) —
which the nudge to "run py_compile" prompted. Pre-existing (the verify-gate
bookkeeping call had it too); now coerces list→string. This crash **corrupted
the first A/B** (one treatment run died), so it was re-run clean.

### Two paired A/Bs (stash-toggle `--marker _saw_verify_ok`, D80/D85)

| A/B | cases × models × reps | target: syntax-fix gemma | qythos9 (regression) | note |
|---|---|---|---|---|
| #1 (pre-crash-fix) | 4 × 2 × 3 | 0/3 → **1/3** | 3/3 both arms | 1 treatment run killed by the list-cmd crash → understated |
| #2 (crash-fixed) | syntax-fix+logic-bug × 2 × 5 | **0/5 → 4/5** | 5/5 both arms | clean |

A/B #2 aggregate: done 15→19 (**all +4 from syntax-fix gemma**), repeat-stops
0→2, repeats 2→8, iters 4.0→5.1. The extra iters/repeats are the gate making the
model **work** (control falsely quits in 2 iters; treatment runs py_compile, sees
the real SyntaxError, edits, re-verifies — proven in the rT3 transcript). Of the
2 treatment repeat-stops: one is a **succeeded** run redundantly re-running
py_compile (cosmetic), one is a run that **also failed in control** (not gate-
induced). **Zero false-fire on qythos9** in 20 runs. +6 tests, suite 637 green.

### Decisions
| # | Decision | Why |
|---|---|---|
| D86 | **Confident false-completion is a third pathology, orthogonal to flail** — and it's caught by *forcing verification*, not by clearer text or plan-message edits. | The `syntax-fix` false-completion has zero flail signature (clean "answered", `f0 n0 r0`); only the real check sees it. Unlike D84's flail (which ignores clearer text), this DOES yield to a structural gate that makes the model run the check it claimed — the model, once it *sees* the SyntaxError, fixes it 4/5. The gate is prevention-class (D82): it stops a confidently-wrong final answer, the worst outcome for pain #1. |
| D87 | **Read the transcript before naming the root cause.** | The false-completion looked like a plan-tool defect (plan.py:156 "give your final answer now") from the counters alone; the transcript showed the model had hallucinated the verify — a completely different lever. Had I "fixed" plan.py I'd have shipped to the wrong module. Same lesson that a corrupting A/B data point (the list-cmd crash) was only caught by reading the run, not the aggregate. |

## Round 28 (2026-07-27) — plan `{task: status}` done-miscount → 0/N-forever loop

### pass3 (32-run battery, build 50): the verify-gate holds, no regression
Read the transcripts (D87) rather than the counters. `syntax-fix gemma` flipped
from the Round-27 clean false-completion (3it) to a `done=N` **repeat-stop** (7it
f3 n3 r2) — but the verify-gate is *not* implicated: the model never reaches a
"compiles" claim. It **misdiagnoses** the bug (calls line 2's valid
`line.split(',')` a "trailing comma error", never sees line 1's missing colon),
tries to `replace_lines` a line with identical text, and loops on the no-op until
the repeat-stop guard catches it. Pure capability/diagnosis miss (D84/D75) — the
build-50 gate's A/B win stands untouched.

### The real find: `update_plan` reset every task to open on a word-value
`add-test qythos9` in pass3 flailed 16it r5 to a repeat-stop — on a **green,
finished** task. Transcript: the model sent the `{task: status}` dict shape with
**word values** — `{"[x] Create primes.py": "finished", ...}`. The tool's
`{task:status}` recovery (tools/plan.py) checked `has_status_marker("[finished] x")`,
which is false (`_MARKERS` knew `done` but not `finished`), so it **discarded the
key's correct `[x]` and reset the task to `[ ]` open**. Plan read `0/3 done`
forever → the "open plan tasks" nudge fired every turn → qythos9 re-wrote the
already-correct files → repeat-stop. A done-counter stuck at zero converts a
solved task into an infinite loop.

**Fix (build 51):** (1) `_MARKERS` gained `finished`/`complete`/`completed`/`not
started`/`in_progress`/`started` synonyms; (2) new `status_marker_for(word)`
helper; (3) structurally, when the dict **value** maps to no known status, keep
the marker the **key** already carries instead of resetting to open — the value
still wins when it *is* recognized (preserves the r16 disagree-case guarantee).
+4 tests (exact pass3 shape reproduced), suite 641 green.

### A/B: neutral (dormant path), not negative — read before concluding (D87)
Paired stash-toggle A/B (`--fix-file locode/tools/plan.py,locode/agent/plan.py`,
`--marker "trust the key's own marker"`, add-test × 2 models × 3 reps). Aggregate
looked *worse* for treatment (done 6=6, iters 4.0→7.3, repeat-stops 0→1). The
transcripts explain it: **neither arm emitted the dict shape this session** (0
runs with `': '` plan values) — both used clean arrays and the plan counted fine
(`0/3→2/3`). So build-51's code path was **dormant in both arms**; the iter gap
is 100% non-stationarity (D75), not the fix. The fix stands on its unit tests +
the transcript-confirmed pass3 reproduction. ab.py extended to toggle multiple
comma-separated files (harness only, no build bump).

### NEW pathology surfaced by the same transcripts (next target)
Even with clean-array plans, qythos9 loops on the **last** task: it does the work
(`pytest → 4 passed`, plan `2/3 done`) but narrates "All tests pass" in prose
**without calling update_plan to mark the final task `[x]`**. The `_nudge_open_tasks`
nudge says "Continue with: Run pytest and verify — do the work now", whose escape
hatch is worded for *unnecessary/impossible* tasks, not *already-completed* ones —
so the model **re-runs the passing tests** (repeat) instead of marking done. This
reproduced in **both** A/B arms this session (rT2, rC2), so it's the better-
supported next target. Fix candidate: when a green test already appeared this turn
and the sole open task is a verify/test task, either auto-mark it done (best for
visibility → plan shows 3/3) or reword the nudge to "tests already passed — mark
this done and answer, don't re-run." Structural (auto-complete) preferred over
nudge-wording per D84. Needs its own reproduce→design→A/B.

### Decisions
| # | Decision | Why |
|---|---|---|
| D88 | **A "done" counter stuck at zero is a loop bug, not a cosmetic one.** A plan that can never reach complete turns the "open tasks" nudge into a perpetual-motion machine on already-finished work. | Two distinct plan miscounts (the r22 truncated-array, now the r28 word-value reset) both produced infinite re-do loops. Any recovery path that can't preserve a *done* status is a turn-killer — treat the plan's done-count as correctness-critical, not advisory. |
| D89 | **A neutral A/B (dormant code path) is not a failed A/B — verify the path was exercised before reading the aggregate.** | Build-51's A/B looked negative until the transcripts showed neither arm hit the changed code. Non-stationary triggers mean the pathology you're fixing may simply not recur that session; the aggregate then measures only noise. Confirm the fixed path fired (here: grep the plan values) before crediting *or* faulting the numbers. |

## Round 29 (2026-07-27) — credit the "run the tests" task a green suite already satisfied

### Second root cause of the same open-plan re-do loop
Round 28's fix (build 51) addressed the *dict-shape 0/N miscount* path to the
"open plan tasks" loop. Reading the build-51 A/B transcripts surfaced a **second,
independent path to the identical symptom** on clean-array plans (ab_plandict
rT2/rC2, qythos9): the model decomposes into e.g. `[x] write code`, `[x] write
tests`, `[>] run pytest and verify all tests pass`, runs the suite to green — then
**narrates "All tests pass" in prose without calling update_plan to mark the final
task `[x]`**. Plan stays `2/3`, the open-tasks nudge fires, and its escape hatch
("if a task is unnecessary/impossible, mark it done") doesn't match an
*already-completed* task, so the model **re-runs the passing tests** to a
repeat-stop.

### Fix (build 52): the loop credits what the model proved
Before the open-tasks nudge fires, if a green pytest tally already appeared this
turn (`_saw_green_test`) AND the current open task is run/verify-tests-shaped
(`_VERIFY_TASK_RE` — a run/verify/confirm/make verb near a test noun, OR a
"…tests pass" phrasing; the verb requirement keeps "Create test_primes.py" out),
mark that task done (`Plan.complete_current()`, which does not bump `revisions`)
and emit a `verify task credited` event. If it was the last open task the plan
completes and the model finishes; if others remain, the loop advances to them.
The worst-case misfire is provably benign — it fires only on a real green result +
a run-the-tests task, and marking *that* done is correct by construction; it can
never manufacture a false-done (correctness is checked independently by the
battery). +5 tests (exact reproduction + negative discrimination on
create-a-test-file and prose), suite 646 green.

### A/B: dormant again (non-stationary trigger), no regression (D89)
Paired A/B (`--fix-file locode/agent/loop.py,locode/agent/plan.py`, `--marker
"verify task credited"`, add-test × 2 × 3). This session was **wall-budget-
dominated** (mlx server slow — most runs hit the 180s wall at 2–5 iters), and the
multi-task-verify shape didn't recur: the open-tasks nudge fired in just 1/12 runs
and the credit path fired 0 times (the one nudged run, rT3, used a single combined
task and self-recovered in one nudge — `_is_verify_task` correctly declined it).
Aggregate: done 6=6, repeat-stops **1→0**, repeats 1=1, iters 4.3→4.2 — no
regression, weakly positive. The fix is grounded in the directly-observed
ab_plandict transcript + tests, not this A/B's numbers. Same lesson as D89: a
dormant-path A/B is neutral; verify whether the fixed path fired before crediting
*or* faulting the aggregate.

### pass4 (build 52 full battery, --max-wall 300) — LIVE confirmation
What the two dormant A/Bs couldn't give, a fresh battery did: the pass3 shape
recurred and the fix engaged. **add-test qythos9: `7it green, answered, done=Y`
— the pass3 `16it r5 repeat-stop` loop is GONE.** The transcript shows it firing:
plan `2/3` with `▶ Run pytest and ensure all tests pass` current, `pytest → 11
passed`, then `⟳ verify task credited (tests already green)` (1 credit, **0**
open-plan nudges) → clean finish. Whole battery: **16/16 done=Y, zero
false-completions, every case lands correct output.** The 6 PROBLEM rows are all
gemmacoder12 capability flail (repeat-stops that still LAND — D84; gemma is not
the workhorse); qythos9 near-spotless (only undefined-vars mild: 6it f1 r1). Net:
builds 51+52 confirmed working live, no regression, and the "flailer=gemma,
qythos9=clean" split from Round 27 holds.

---

## Round 30 — prompt-variety cases + a replay repeat false-positive (harness, no build bump)

### 3 new battery cases (under-tested paths)
All prior 8 cases were self-contained single-file edits. Added variety per the
standing overnight instruction, each stressing a path nothing else exercised:
- **read-before-edit** — the correct port lives in a *second* file (`config.py`);
  the model must read it before editing `server.py`. (Nothing else forced a
  cross-file read.)
- **rename-across-files** — rename a symbol across `models.py` + `views.py`
  (def + import + call), distinct from `refactor-rename` (single-file).
- **fix-traceback** — a runtime `KeyError` (not syntax): run → read traceback →
  fix `.get`. Distinct signal from `syntax-fix` (py_compile) and `add-test`
  (pytest-green). Each check verified to pass-on-fix / fail-on-broken first.

pass5 (both models, --max-iter 25 --max-wall 240): **qythos9 3/3 clean**,
gemmacoder12 3/3 land correct output but with genuine repeat-flail (D84). Every
one of the 6 runs got the right answer.

### Finding: replay flagged a FLAWLESS run as flailing (visibility defect)
`fix-traceback qythos9` did the textbook arc — read → `python3 report.py` (crash)
→ edit → `python3 report.py` (verify `12`) — yet replay stamped it 🔁 1 repeat
(→ PROBLEM via `_problem`, which trips on any repeat). The repeat detector had **no
notion of intervening progress**: it flagged the identical verify-run even though a
successful edit changed the file between the two calls. This is a defect in the #1
north-star tool (visibility): a perfect run mislabeled as a spin pollutes triage
(pass5 over-counted 4/6 problem rows).

### Fix (evals/replay.py): a successful mutation clears the spin-tracking
`_MUTATING_TOOLS = {write_file, append_file, move_file, edit_file, replace_lines}`.
On a mutating result with `error=False`, `seen_keys.clear()` in both `summarize`
and `transcript_lines`. Principle: a pathological repeat is re-issuing a call with
**no successful mutation between**; re-running after real progress is legitimate
re-verification. Failed/no-op edits (`error=True`) do NOT clear, so genuine
edit-match-miss loops stay flagged. Audited on the undefined-vars gemma transcript:
r4→r2 — the 2 dropped were legit re-runs after successful edits (one produced a
*different* error, one went green), the 2 kept were real post-no-op spins the
agent's own detector stopped on. +3 tests (clears-on-success, still-flags-after-
failed-edit, transcript-no-tag), suite 649 green. No `__build__` bump (harness).

### No product bug — the LOOP already gates on progress (loop.py:186)
Checked whether the agent's own repeat detector shares this blind spot: it does
NOT. `repeat_streaks` stores the last result per call signature and "only counts
a repeat when the result is unchanged too" — so a `python3 x.py` that goes
crash→`12` across a fix is never a stall to the loop. That's why fix-traceback
qythos9 self-terminated with zero nudges. The gap was only in the observability
tool. Note the two lenses differ by design: the **loop** (behavior) gates on
call-signature + *result-unchanged*; **replay** (visibility) now gates on
call-key + *intervening successful mutation*. They agree on every observed case;
replay's version flags literal re-issues after no forward progress, and attributes
edit-then-rerun-same-error to the fail/no-op counters rather than to repeats.

### D90 — a repeat flag needs a progress denominator
"Same call twice" is not a pathology; "same call twice with nothing accomplished
between" is. Any repeat/loop detector must gate on intervening forward progress or
it cries wolf on the correct reproduce→fix→verify pattern — the exact arc a good
agent runs on a runtime bug. locode's loop already does this (result-gate); the
replay tool now does too (mutation-gate).

---

## Round 31 — build 53: clean finish on a redundant complete-plan re-state

### Finding (pass6 rename-across-files gemmacoder12, directly observed)
The model did the whole task correctly — renamed get_user→fetch_user across
models.py + views.py (self-recovering from a wrong-signature guess and a `)))`
typo), verified `show(3)==30`, marked its plan **3/3 done** — then re-emitted the
**identical, successful** 3/3 update_plan instead of stopping, and hit the repeat-
stop: "the model repeated the same tool call without making progress." A SUCCESS
that reads as a FAILURE. Same shape ended all 4 gemma PROBLEM rows in pass6: work
lands, then the model keeps poking (redundant no-op update_plan / no-op edit /
identical replace_lines) until the repeat-detector stops it. qythos9 (workhorse)
never does this — it stops cleanly. This is the *structural finish-detection*
family (build-52 class, validated live), NOT the *clearer-text* family (D84).

### Fix (build 53, loop.py)
At the repeat-detection threshold, before the failure-toned nudge/stop: if the
repeated batch is exclusively `update_plan` AND `self.plan.complete` (all tasks
done), finish cleanly — emit an info event and return "All planned tasks are
complete." + the plan render. Gated on a genuine repeat so the FIRST update_plan
that completes the plan still passes through (a real summary may follow). The gate
is so tight (redundant update_plan on a fully-done plan) that a false early-finish
is essentially impossible — the model's own plan says everything is done. +2 tests
(positive clean-finish; negative: an OPEN plan re-stated does NOT early-finish),
suite 651 green.

### A/B: DORMANT for this fix (D89) — aggregate NOT creditable
ab_planfinish (gemma × rename-across-files × 4 reps, paired stash-toggle of
loop.py, marker "re-stated its finished plan"). Aggregate looked like a win —
repeat-stops **1→0**, repeats 4→2, done 4=4, iters 11.0→10.5 — BUT grep confirmed
the fixed path fired in **0/4** treatment runs. Reading transcripts (D87/D89): the
treatment arm's gemma stopped cleanly on its own after 3/3 (non-stationarity — the
re-state variant didn't recur this session), and the single control repeat-stop
(rC1) was a DIFFERENT pathology — a **truncated** update_plan (`tasks='["[x]…"'`,
"did not parse — may have been cut off") re-emitted identically; its plan never
completed, so `plan.complete` would correctly decline it anyway. So the aggregate
is non-stationarity + an unrelated truncation stop, NOT my code. Committed on the
directly-observed pass6 transcript + units + no-regression, same discipline as
builds 51/52. Same D89 lesson: confirm the fixed path FIRED before crediting an
A/B aggregate — here it plainly did not.

### Observation (deferred): truncated update_plan re-emit → repeat-stop
rC1 surfaced a distinct weak-model failure: gemma emitted an update_plan whose
`tasks` JSON array was cut off mid-token, the tool rejected it as unparseable, and
the model re-issued the identical truncated call → repeat-stop. This is the
malformed/truncated tool-JSON class (already has recovery paths the model just
couldn't act on), adjacent to D84 capability. Not fixed now; noted for a future
loop — a possible lever is nudging toward a SHORTER plan when update_plan truncates.

---

## Round 32 — reasoning-case variety: workhorse clean, gemma indentation-strategy split (D84)

Committed 2 wrong-output REASONING cases (harness-only, no build bump): `even-median`
(median wrong for even-length lists — must average two middle values; no traceback
guides it) and `dedup-order` (set()+sorted drops first-seen order — must implement
order-preserving dedup). Both stress correctness reasoning, not crash-suppression.

### Results (pass7, 4 reps each)
- **even-median:** qythos9 4/4 done=Y, gemma 4/4 done=Y. Clean for both.
- **dedup-order:** qythos9 4/4 done=Y clean. **gemma 0/2 done=N** (both reps PROBLEM,
  output still the broken `[1, 2, 3]`) — the first genuine done=N (not lands-anyway)
  in a while.

### dedup-order gemma failure — DIAGNOSED (D87 transcript + raw event)
Not missing newlines (the replay preview collapsing `\n`→space was a display artifact;
raw `args.new` had proper newlines). The real split is **how each model FRAMES the edit**:
- **qythos9 (wins):** `old = "def dedup(items):\n    return sorted(set(items))"` — anchors
  on the **function header** — and `new` re-writes the body with **every line indented 4
  spaces**. Compiles.
- **gemma (fails):** `old = "return sorted(set(items))"` — anchors on the **bare inner
  statement** — and `new` is a **column-0** block. edit_file replaces the substring, so the
  line's leading 4 spaces survive on line 1 only; lines 2+ land at col 0 → IndentationError.
  Syntax-guard (build 47) correctly REFUSES the corruption; gemma re-emits identically → stall.

Verdict: **model-strategy/capability split (D84), NOT a new harness defect.** The guard did
its job (file left intact, not corrupted — done=N-intact beats done=Y-corrupt). qythos9, the
workhorse, is robust here; the answer remains "use qythos9."

### Deferred harness candidate (specified, NOT implemented unsupervised)
`edit_file` could **auto-reindent a multi-line `new` to the match line's leading
indentation**: when the matched `old` is preceded on its line only by whitespace W, prepend
(W − new's-first-line-indent) to every line of `new`, preserving relative structure. That
would rescue gemma's col-0 block → valid, and no-op when the model already indented (new's
first line already at W). BUT it's Opus-tier fs.py surgery with real regression risk against
the existing D81 indent-preservation / indent-only-noop logic, and it helps only gemma here
(qythos9 already robust) — so D84-lower value. Deferred to a SUPERVISED session, not cut into
the edit-semantics core overnight. This is a structural-family lever (fixes without needing
the weak model to read text), which is why it's worth recording rather than discarding.

---

## Round 33 — build 54: plan-restate finish fires on the FIRST repeat (D89 dormant again)

The `already-correct` case (nothing-to-fix path) surfaced the flail I built build 53
for, one step earlier. qythos9 is clean (verify → finish, 3it, no edit, r0). gemma
flails on the done task in two non-stationary shapes (both done=Y — output stays
correct, so no corruption):

- **pass8 (plan-restate):** read → bash verify (True False) → update_plan[x] →
  update_plan[x] identical 🔁 → self-terminate. Build 53's guard needs
  max_repeat_calls-1 (=2) identical calls; gemma restates only ONCE, so build 53
  MISSES the common 2-call variant and the redundant call gets flagged (r1, PROBLEM).

### Build 54 — fire the plan-complete finish on the first repeat
Hoisted the plan-complete clean-finish out of the `seen_streak >= max_repeat_calls-1`
gate to fire at `seen_streak >= 1` (marker `[first-repeat-plan-finish]`). Still triple-
gated — every call an update_plan AND plan.complete AND already emitted once — so the
FIRST plan-completing update_plan (streak 0) still passes through (a real summary may
follow). Strictly a narrower/earlier version of build 53's shipped guard. Unit test
`test_completed_plan_finishes_on_first_restate` scripts a THIRD sentinel turn that is
only reached if we did NOT finish at the first restate, and asserts it never surfaces —
so it fails on build 53 and passes on build 54. Suite 652 green.

### A/B: DORMANT again (D89) — non-stationary flail shape
ab_firstrepeat (gemma × already-correct × 3, stash-toggle loop.py, marker
`first-repeat-plan-finish`). CONTROL == TREATMENT exactly: 3/3 repeat-stop, done=Y,
5it, r1. Read the treatment transcript (D87/D89): minutes after pass8, same code,
gemma's flail SHAPE had changed — it re-ran the identical PASSING bash verify twice
(no update_plan at all, no plan created) → repeat-stop. My guard correctly did NOT
fire (repeated call is bash, plan not complete). So the plan-restate path was dormant
this session and the aggregate can neither credit nor fault build 54. Committed on the
directly-observed pass8 transcript + units + no-regression — same discipline as build
53, whose A/B was dormant for the same reason.

### New observation (deferred): bash-rerun-on-a-done-task → failure-toned stop
The A/B surfaced a SECOND shape of the same north-star problem: gemma verifies the
correct answer (bash → True False), then re-runs the byte-identical passing check and
hits the repeat-stop, whose "repeated … without making progress" message reads as a
FAILURE on work that in fact landed (done=Y). Unlike the plan-restate, this has NO
clean fix: with no plan there's no completion signal locode can trust, and a repeated
passing bash is ambiguous (benign re-verify vs genuinely stuck). The deeper root is
that gemma sometimes skips planning entirely, leaving locode blind to completion.
Deferred for a supervised session; qythos9 (the workhorse) plans and finishes cleanly.
