# locode roadmap

Living plan. We work top-down within a milestone, and **insert newly discovered
priorities in place** rather than deferring them to the end. Every code item
lands with tests + a `__build__` bump; every quality claim is backed by a graded
sweep logged in `evals/LOG.md`.

Status: `[ ]` todo · `[~] in progress` · `[x] done` · `[!] needs a decision`

---

## Milestone 1 — Generation robustness
*The model must not silently lose work or emit garbage. Highest priority: these
are user-visible and waste whole generations.*

- `[x]` **1.1 Runaway intra-generation repetition** — *discovered 2026-07-24,
  user-observed; shipped build 20.* A model falls into a degenerate cycle and
  streams one giant reply of near-repeating text (`…megahypermega…universal…`)
  until it burns the full `max_tokens` or the wallclock. Nothing caught it:
  every stuck-detector works *between* replies, and the client sent no
  anti-repetition params.
  - `[x]` **1.1a Sampling cure** — `frequency_penalty` / `repetition_penalty` /
    `stop` now plumbed config → client, sent only at non-neutral values.
    Off by default (opt-in per model) so code generation isn't penalised.
  - `[x]` **1.1b Streaming cycle-abort** — `model/repetition.py` fingerprints
    the token stream, derives the loop period from the tail's nearest earlier
    occurrence, and the client cuts the generation off (`finish_reason=
    "repetition"`); the loop discards the garbage and nudges, bounded by
    `max_repetition_aborts`. Catches short-phrase AND ~330-char sentence-level
    loops. *Caveat: thresholds are conservative but unvalidated against a full
    sweep — confirm no false-positive aborts on real cases before trusting it.*
- `[ ]` **1.2 Large-file EDIT truncation** — salvage today covers
  `write_file`/`append_file` only; a truncated `edit_file` of a big span still
  loses everything (the e2e/qwencoder14 wallclock death at ~20.4k chars).
  Salvaging a half-formed `new` is unsafe. Try **steering big edits toward small
  `old` snippets** before building a bespoke tool.
- `[~]` **1.3 The one-line-off edit loop** — "repeated the same tool call"
  dominated r12 nudges (42 of 107). **Diagnosis (2026-07-24, r12 event logs):
  33% of all edit_file calls fail.** Breakdown of the 74 failures and whether
  the run ever lands a correct edit on that file afterward:
  no-op old==new 38 (23 unrecovered), ambiguous 16 (0 unrecovered — always
  recovers), not-found 12 (3 unrecovered), empty-old 8. So the recoverable-help
  levers (ambiguous/not-found) have little conversion headroom; the dead-end is
  the no-op (see 1.4). Ambiguous message now lists match locations anyway — it
  saves wasted iterations (each cut truncation risk) even though it always
  eventually recovers.
- `[x]` **1.4 No-op `edit_file` (old==new) — the real edit dead-end** —
  *discovered 2026-07-24, user-observed; shipped build 21; validated by
  r13-edithelp vs r12-salvage (same two cases, exec-bugfix + e2e-spec-to-code).*
  Build 21 attacks it two ways: **prevention** (edit_file description now states
  `new` must DIFFER from `old`, seen at planning time) and a **sharper,
  actionable no-op message** (re-read the failure and correct `new`, or the bug
  is elsewhere — stop editing this line). **Measured result on qwencoder14 (the
  model that owned the problem): the fix worked.** Per-edit failure rate 41% →
  20%; no-op fails 29 → 8 (25% → 8% of its edits); **unrecovered no-op dead-ends
  20 → 1** — the target metric essentially eliminated. *Caveat: task scores
  barely moved (exec-bugfix qwencoder14 0.25 → 0.29) — landing edits is not the
  same as computing correct fixes; the capability wall is real (see 3.1). And
  qythos9 looks slightly worse (edit fail 24% → 32%, unrecovered no-ops 3 → 9,
  score 1.00 → 0.92 / e2e 0.76 → 0.60), but n=6 (was 8) and it is concentrated
  in the wallclock-death e2e case — plausibly noise, not confirmed. Worth a
  re-check if qythos9 edit reliability shows up again.*
- `[x]` **1.5 Silent false-success no-op edits** — *found 2026-07-24 while
  trying gemmacoder12; user-reported "old==new, same as every model".* Distinct
  from 1.4's exact `old==new` (already caught): the **whitespace-tolerant** (and
  fuzzy) match tier strips `new`'s leading indent and preserves the file's
  original, so an **indent-only "fix" produces a byte-identical file** — yet
  `run()` reported it as `edited (1 replacement, whitespace-tolerant)`. Every
  model that tries to fix indentation via edit_file loops forever thinking it
  won. Fix (build 27): `try_edit` now returns a `noop` status when
  `updated == text`, and `edit_file` surfaces it as an error explaining the
  indent-preservation rule and pointing to write_file. +2 fs tests (533 green).
- `[x]` **1.6 Model blind to its own successful edits** — *the 1.5 sibling;
  seen in the gemmacoder12 fizzbuzz run where a SUCCESSFUL edit was misdiagnosed
  as a no-op and the model then re-targeted already-removed text (not-found
  loop → stop).* Root cause: the success message was just
  `edited (1 replacement)` — the model never saw the file's new state, so it
  built the next `old` from a stale mental model. Fix (build 28): `edit_file`
  now echoes the **changed region back, line-numbered exactly like read_file**
  (`_edit_snippet`, ±3 context lines, capped at 24 lines). The tolerant tier
  already strips copied line-number prefixes, so echoing them back is safe.
  +2 fs tests (535 green). *Sweep-validated in r15-editecho (see below).*
- `[x]` **1.5/1.6 sweep validation (r15-editecho vs r13-edithelp)** — same 2
  edit-heavy cases × qwencoder14+qythos9 × n=6, build 28. **Mechanisms confirmed
  live:** the build-28 echo fired on 100% of successful edits (108/108); the
  build-27 `noop` status caught 14 silent indent-only no-ops on qwencoder14 (0 on
  qythos9, which doesn't do them). **Scores: 3 of 4 cells improved** — the target
  cell most: exec-bugfix qwencoder14 **0.29→0.50, clean-finish 1/6→3/6**;
  e2e-spec-to-code +0.07 (qwen) / +0.13 (qythos). **1 cell regressed:**
  exec-bugfix qythos9 0.92→0.75, clean 5/6→0/6 — but *every* run repeat-stopped
  and **3 scored 1.00 (solved) then got killed post-solve**, via two pre-existing
  loop pathologies (not the echo/noop mechanisms): see 1.7. Attribution: the
  regression is variance surfacing old fragilities at n=6, not the 27/28
  mechanisms, which fired correctly.
- `[x]` **1.7 update_plan double-wrap kills solved runs** — *found 2026-07-25 in
  the r15 qythos9 exec-bugfix regression.* The model, having already made pytest
  green, tried to mark its plan done but sent the whole call shape nested inside
  the argument: `{"tasks": {"tasks": [...]}}` (dict) or its truncated string half
  `{"tasks": "[ ] run tests"`. The dict form was **hard-rejected** (model resent
  it → stall); the string form **fell through the newline split and was adopted
  as one bogus task**, poisoning the completion gate. Either way an
  already-solved run stall-died. Fix (build 29): `update_plan` unwraps a
  single-key `{"tasks": X}` wrapper, parses `{`-prefixed JSON strings and pulls
  out their inner `tasks`, and rejects an unrecoverable JSON-object string with
  the real array shape instead of false-accepting it. +4 plan tests (539 green).
  **Validated (r16-planfix, build 29, exec-bugfix × 2 models × n=6):** qythos9
  fully recovered to the r13 baseline — **0.75→0.92, clean-finish 0/6→5/6**, same
  score vector as r13; the double-wrap recovery fired live in 4 runs (`0/1 → 1/1
  done` → clean finish). qwencoder14 held at 0.46 (>> r13's 0.29; its 5/6
  repeat-stops are the known capability wall). Regression CLOSED.
- `[x]` **1.7b update_plan task→status dict** — *r16 surfaced a second shape:*
  the model sent `tasks` as a dict mapping marked task text to a status word —
  `{"[ ] Run tests": "done", "[>] Fix wrap": "in progress"}` — where the key
  marker and the value disagree. The old code hard-rejected it. Fix (build 30):
  when every key looks like a task line, rebuild each task with the **value's**
  status winning (via `strip_status_marker`), so a task the model calls "done"
  can actually complete instead of staying open and jamming the completion gate.
  Gated on all-keys-marked so an ordinary object isn't mistaken for a plan.
  +2 plan tests (541 green).

## Milestone 2 — Eval-harness trustworthiness
*The gate must catch real regressions without crying wolf on noise.*

- `[ ]` **2.1 Variance/n-aware regression gate** — the fixed `0.15` per-row
  threshold sits below the noise floor at low n. **Finding (2026-07-24):** a
  naive Welch/2·se test does **not** silence the r12 false positives — the
  baseline was n=3 with *zero* within-sample variance (3/3 identical), so its
  own samples understate its true uncertainty. A real fix must (a) treat tiny-n
  rows as high-uncertainty (Wilson interval / a minimum-variance floor, not the
  observed stdev), and (b) compare *intervals*, not point means. Also stop
  comparing an n=3 baseline to an n=8 candidate as if like-for-like.
- `[ ]` **2.2 Infra-kill scored as model failure** — the checker's 180s pytest
  timeout scores an infrastructure kill as 0.00, indistinguishable from a model
  failure (same class as fixed in c00e8a4).
- `[ ]` **2.3 Gate/compare ergonomics** — `compare` takes two positional
  results.json paths; document the threshold and exit codes where a user looks.

## Milestone 4 — Interactive usability
*User feedback 2026-07-24: "still very cumbersome to use locode with local
models." The pains that bite in a live session, ranked by the user: **poor
visibility** (can't tell what it's doing / whether it's stuck / whether the run
accomplished anything) and **mid-task flailing**. These compound — you can't see
the flailing to know when to step in. Eval scores are a proxy; this milestone is
the lived experience.*

- `[x]` **4.1 Live plan checklist (build 23).** The model already maintains a
  task list (`update_plan`), but the REPL collapsed it into one truncated
  generic tool line. Now each `update_plan` renders the live plan as a checklist
  — done tasks dimmed + struck, the current task arrowed and bold, todos dim —
  so a turn's progress (and where it's stuck) is visible at a glance.
  `render.format_plan(loop.plan)`.
- `[x]` **4.2 End-of-turn summary + louder nudges (build 23).** A turn that did
  real work now ends with a `↳ N iterations · M tool calls · K files changed ·
  J nudges` trailer, so a long/flaily run is legible — effort vs. what actually
  changed on disk answers "did it accomplish anything?". Nudges (the flailing
  signal) went from whisper-dim `…` to a visible yellow `⟳` — they read as
  warnings now, not reassurance. `render.format_turn_summary`.
- `[x]` **4.3 Out-of-box model default.** Flipped the built-in default from
  qwen14 to **qythos9**, the reliable editor (84% edit success vs 58%; no no-op
  dead-ends). Slower 9B that can hit the generation cap on large writes, but
  editing reliability dominates interactive use. Landed in config.py,
  scaffold.py, config.toml.example, test_config.py (build 26). User decision.
- `[x]` **4.5 Tool-result verdict is legible (build 39).** Found by *watching* a
  weak model fix a real bug (not from a score): `format_result` summarized a
  multi-line result by its first line, so a `pytest` result rendered as
  "✓ ===== test session starts =====  (+9 more lines)" — the `3 passed`/`2 failed`
  verdict buried, the green ✓ meaning only "the tool ran." Now it surfaces the
  conclusion line (verdict/error patterns, scanned from the end) and flips ✓→✗
  when the output reports failure even on a clean tool exit. Helps both pains:
  the verdict is legible at a glance (visibility), and a looping model now shows
  `✗ 1 failed` each retry — visibly stuck — instead of an identical banner
  (flailing). `render._salient`. LOG Round 17, D68.
- `[x]` **4.6 Gate a "tests pass" finish on a green the model saw (build 40).**
  The worst flailing tail is a *false* finish: the model asserts "the tests
  should now pass" having never run the suite to green — the largest source of a
  run declaring done while `tests_pass` is False. Measured the signal first: an
  ever-saw-green gate has perfect discrimination on 89 self-declared-done runs
  (catches 4/4 false-completions, blocks 0/85 legitimate ones). Now `loop.py`
  tracks a per-turn `_saw_green_test` (bash result with a pytest pass tally, no
  failure token) and, in the finish cascade, nudges once when the final content
  makes a test-specific pass claim without having seen green. Scoped so
  doc/plan tasks and fixture reads containing "N passed" can't trip it. LOG
  Round 18, D69/D70.
- `[x]` **4.7 Stop cleanly instead of surfacing a half-written tool block
  (build 41).** Found by *watching* r22 devstral24 e2e runs: `turn_end.result`
  showed **5/6** ended with the "final answer" being a raw, unclosed ` ```tool `
  JSON fence. devstral24's long edits hit `max_tokens` mid-call; the truncation
  nudge fires up to `max_truncated_retries`, then the loop fell through to
  `return content` and handed the user the half-written block — the worst kind of
  invisible failure (a garbage blob masquerading as the answer). Now, once the
  retry budget is spent and the reply *still* ends inside an open ` ```tool `
  fence, `loop.py` stops with a legible "kept getting cut off mid tool call — try
  a smaller step" message. Scoped to the broken-fence case only; a prose reply
  cut mid-sentence stays readable and still falls through. Pure visibility lever.
  LOG Round 20.
- `[~]` **4.4 Convergence / clean-finish.** clean_finish is low suite-wide —
  sessions rarely end on a clean "done", they flail and stall. The deepest, and
  hardest, usability lever. **Per-case flailing map (r12-salvage, all 6 cases ×
  2 models × n=8, cross-referenced with r15/r16):**
  - `design-doc` 16/16 clean, `plan-doc` 15/16 — doc stages are at ceiling.
  - `exec-bugfix` — **update_plan malformation was the dominant harness-fixable
    stall** (11/31 update_plan calls errored in r12). Fixed by 1.7/1.7b; r16
    confirmed the fix eliminates the "solved-but-churns-past-the-win" pattern:
    every run that reached pytest-green now clean-finishes (r15 2/4 churned →
    r16 0/5). Residual = capability wall (wrong-fix iteration; old==new on an
    already-correct line).
  - `exec-from-plan` 11/16 clean (best hard case). Residual stall = the model
    finishes the real work but leaves a **non-actionable meta-task** open
    ("[ ] Read PLAN.md and understand…" — the exact vagueness the tool
    description warns against), then re-sends the *identical* plan. The
    anti-revision nudge fires and the repeat guard kills it correctly — it is
    already handled; a new lever would fire only 1–2 iters sooner at real
    false-positive risk. Upstream plan-quality issue, not a loop bug.
  - `e2e-spec-to-code` 0/16 — the 3.1 capability wall (wrong logic / broken
    syntax). Harness levers exhausted.
  - `exec-stall-trap` 7/16 — designed to bait stalling; mixed.
  **Assessment: the harness-fixable convergence levers are now captured (1.5/1.6
  edit-path, 1.7/1.7b update_plan shapes). The remaining non-convergence is
  capability-bound** (3.1) or upstream plan quality — pushing the loop guards
  harder trades false-positives against legitimate work. Revisit if a stronger
  base model changes the capability picture.
  - **Update 2026-07-25 (build 37, `r20-replacelines-live`): one deterministic
    convergence win; no new harness lever.** The default model (qythos9) was
    failing a trivial raw-error bugfix **6/6** because its correct `edit_file`
    call, emitted as single-quoted JSON with a dropped closing `'`, left a
    trailing `}}` whose unterminated string swallowed the closing tool fence —
    `_closing_fence` dropped the whole call and the turn ended with the fix
    unexecuted. Build 37's parser recovery (`_closing_fence` EOF-in-string +
    `_strip_structural_tail`) took the blindprobe **6/6 BROKEN → 6/6 OK** and
    lifted exec-bugfix qythos9 0.92 → **1.00** (consistent across every sweep —
    a real, deterministic fix). Separately, the eval had never auto-approved
    `replace_lines` (build 34's `edit_file` fallback); fixed on principle across
    the default list + all six pinned case allowlists (a real product tool the
    eval was wrongly denying), though its exec-bugfix qwencoder14 gain is
    n=6-suggestive, not credited.
    **A first-draft "new harness-fixable alternation stall" claim here was
    RETRACTED as a D60 error:** the exec-stall-trap qwencoder14 move (0.92→0.44)
    was NOT caused by `replace_lines` — r19 *without* it scored 0.33, lower than
    r20 *with* it (0.44), so RL cannot be the cause. Event logs show the loop
    guards behaving correctly (repeat guard catches the loopers; the generic
    `res.no_change` guard resets on interleaved reads, as designed); the drop is
    n=6 variance over the 3.1 capability wall (wrong fix + premature "all tasks
    completed"). **No new lever — the 4.4 assessment above stands unchanged.**
  - **Update 2026-07-25 (build 38): the gate now encodes the n=6 non-stationarity
    that the round-15 retraction had to reason through by hand.** Proven finding:
    two *build-identical* sweeps (r18/r19) differ at p=0.030 on the exact
    exec-stall-trap qwencoder row above — a single n=6 sweep is non-stationary, so
    no single-sweep statistic can auto-FAIL a noisy row. `compare` now keeps
    per-run scores and classifies each row `ok`/`noise`/`review`/`regression`,
    hard-failing only rows that are internally consistent in *both* sweeps (or a
    broad slide across the trusted pool) and routing per-sweep drift to an
    advisory **REVIEW**. Validated: every same-code pair now PASSes; the real
    build-37 gains still show; the noisy stall-trap drop is REVIEWed, not FAILed.
    **The real fix for noisy-row *attribution* (crediting vs rejecting a REVIEW
    row) is interleaved paired runs at higher n — queued, see Milestone 4 next
    steps.** (LOG Round 16, D65–D67.)

## Milestone 3 — Weakest-case quality
- `[x]` **3.1 e2e-spec-to-code** — weakest case on both models, unmoved 5+
  rounds. **Diagnosed 2026-07-24 (r13 event logs + per-check tally).** The
  ceiling is entirely in **stage 3 (code)**: the doc stages mostly pass, but
  `own_tests_pass` = **0/12** and `independent_spec_check` = **0/12** across
  every run and both models, and `clean_finish` = **0/12** — every run dies on a
  stall. It is a genuine **model-capability wall**, two distinct flavours:
  - **qwencoder14 — wrong logic.** Writes parseable code but never implements
    type-coercion/precedence. Assertions show `{'a': '3'} == {'a': 3}` (env value
    not coerced to the default's type) and `{'a': 1} == {'a': 2}` (precedence not
    applied). It iterates `edit → pytest` ~5× with identical failures, then the
    loop correctly kills it as no-progress. Nothing the harness can do — it can't
    write the coercion for the model.
  - **qythos9 — broken syntax / hallucinated stdlib.** 3/6 runs die on
    `SyntaxError` (`invalid syntax`, `unexpected character after line
    continuation`); one used `tomllib.dumps` (does not exist). The file will not
    even import, so pytest shows an opaque *collection* traceback
    (`<frozen importlib>`), and it edit-stalls trying to repair it.
  - `[x]` **3.1a Inline Python syntax feedback (build 22).** The one
    harness-actionable lever: `write_file`/`append_file`/`edit_file` now
    `compile()` any `.py` result and append a one-line `SyntaxError at line N:
    <msg>` warning to the *successful* result (advisory, never an error — a
    half-built file may not parse yet). Turns qythos9's frozen-importlib death
    into a legible, located signal one call after the mistake. +6 tests.
    **Measured (r14-syntax vs r13, e2e, n=6): the mechanism works but does not
    lift the score.** qythos9 runs that reached pytest with a SyntaxError
    dropped **5/6 → 0/6** (the inline warning fired in 3 runs; the model fixed
    the syntax before running tests every time) — a real robustness win, worth
    keeping. But `own_tests_pass`/`independent_spec_check` stayed **0/12**:
    removing the syntax roadblock just exposed that the code is *also* logically
    wrong. The wall moved from "won't parse" to "parses but wrong." The apparent
    overall gain (0.61 → 0.69) is doc-stage variance (`plan_has_tasks` swung
    0/6→6/6 for qwencoder14, the opposite for qythos9), NOT the lever — do not
    credit it (see 2.1).
- **3.1 conclusion: capability-bound; harness levers exhausted.** own_tests_pass
  and independent_spec_check are 0/12 across both r13 and r14. The doc stages are
  near-maxed and stage 3 is model reasoning — qwencoder14 can't compute
  coercion/precedence, qythos9's code doesn't pass even once it parses. Further
  e2e gains need a stronger executor model, not more harness code. Keep 3.1a on
  its own general merit; stop spending harness effort chasing this case's score.
  - **Update 2026-07-25 (build 40, r22/r23): a bigger LOCAL model does NOT clear
    the wall.** Tested the "stronger executor" claim directly with devstral24
    (Mistral-Small 24B) n=6 on both hard cases. e2e: mean 0.74 (right in the
    incumbent band), and `own_tests_pass` = **0/5**, `independent_spec_check` =
    **0/5** — the identical failure to qwencoder14/qythos9 (both 0/6 on every
    recent e2e sweep). The wall is **model-size-invariant across the local
    pool**: all three write plausible code with wrong logic. exec-stall-trap:
    devstral24 no-ops (0 tool calls, 0/6 pass) and is strictly worse than
    qythos9, which already solves that case (≈0.98, 8/8). Closes the "just run a
    bigger local model" hypothesis — the payoff is in harness levers
    (visibility 4.5, seen-green gate 4.6), not model-swapping. LOG Round 19,
    D71/D72.

---

### Done
- **1.1 Runaway intra-generation repetition** (build 20, 2026-07-24) — sampling
  knobs + streaming cycle-abort. +14 tests.
- **1.4 No-op `edit_file` prevention + guidance** (build 21, 2026-07-24) —
  validated: qwencoder14 unrecovered no-op dead-ends 20 → 1, edit fail rate
  41% → 20%. Task scores unmoved (capability wall → 3.1).
- **3.1a Inline `.py` SyntaxError feedback** (build 22, 2026-07-24) — validated:
  qythos9 pytest-SyntaxError deaths 5/6 → 0/6. Kept on general merit; did NOT
  lift the e2e score (own_tests_pass 0/12 both rounds — capability-bound).
- **4.1 + 4.2 Interactive visibility** (build 23, 2026-07-24) — live plan
  checklist, end-of-turn summary, louder nudges. +9 tests. Directly targets the
  user's "poor visibility" + "mid-task flailing" feedback.

*(pre-roadmap history is in `evals/LOG.md`, Rounds 1–12.)*
