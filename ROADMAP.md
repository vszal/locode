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

*(pre-roadmap history is in `evals/LOG.md`, Rounds 1–12.)*
