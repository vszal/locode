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
