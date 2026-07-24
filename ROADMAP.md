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
- `[ ]` **1.3 The one-line-off edit loop** — "repeated the same tool call"
  dominated r12 nudges (42 of 107): the model re-issues an `edit_file` whose
  `old` lands one line off, forever. The next real quality target after writes.
- `[ ]` **1.4 No-op / stalled `edit_file` on a bugfix** — *discovered
  2026-07-24, user-observed.* On a real syntax-error fix the model issued
  `edit_file` with `old` == `new` ("changes nothing"), then read + grepped in
  circles without ever landing a corrected edit. Two gaps: the identical-edit
  rejection gives the model nothing actionable, and the flail isn't caught as a
  stall (no repeated *call* signature — each probe differs). Related to 1.3 but
  a distinct symptom (no-op edit + investigate-forever, not one-line-off).

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
- `[ ]` **3.1 e2e-spec-to-code** — weakest case on both models, unmoved 5+
  rounds. Diagnose the dominant failure mode before touching it.

---

### Done
- **1.1 Runaway intra-generation repetition** (build 20, 2026-07-24) — sampling
  knobs + streaming cycle-abort. +14 tests.

*(pre-roadmap history is in `evals/LOG.md`, Rounds 1–12.)*
