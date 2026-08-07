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
  - `[x]` **1.1c Paragraph-scale loops** — *user-observed 2026-08-02 on qythos9;
    build 72.* The conservative thresholds had a hole big enough to miss a live
    loop: a **932-char analysis block** ("Based on the error and the context…
    Let me look at the exact code") re-emitted verbatim for 241.9s until the
    user hit Esc. Two independent blockers, both of which had to move: the
    period exceeded `MAX_UNIT` (700) so it was rejected before repeat-counting
    ran, and the 2000-char `WINDOW` held only **2.15** reps of it against
    `MIN_REPS = 4` — arithmetically unreachable even with the cap lifted. Now
    `WINDOW = 8000`, `MAX_UNIT = 2000`, and `reps_required()` eases long units
    (≥400 chars) to 3 reps, since 1200+ chars of byte-identical text is already
    conclusive and waiting for a 4th burns another ~1 KB. The real block is
    pinned as a fixture, plus an invariant test that the window can always hold
    `MIN_REPS` units at `MAX_UNIT`. +6 tests; all prior negatives still pass.
- `[x]` **1.2 Large-file EDIT truncation — the steering worked; do NOT build the
  bespoke tool.** Salvage still covers `write_file`/`append_file` only, and a
  truncated `edit_file` of a big span would still lose everything — but that span
  no longer occurs. The recommended lever (steer big edits toward small `old`
  snippets) is already in `EditFile.description`: *"Keep `old` to the SMALLEST
  unique snippet … make several small edit_file calls instead of one giant one."*
  **Measured 2026-08-02 across every recorded battery run: 525 `edit_file` calls,
  `old` median 33 chars, p90 152, MAX 344, and zero calls above 1500.** Args are
  logged verbatim (`loop.py` `phase: "run"` passes `call.args` unmodified), so
  this is a real distribution, not a logging artifact. A bespoke
  salvage-a-half-formed-`new` tool would be dead code against this pool.
  Caveat: this measures the local models on battery-sized files; a genuine
  large-file refactor could still reach the limit, so reopen on evidence rather
  than on principle.
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
- `[x]` **1.7c update_plan word-value reset (build 51, 2026-07-27)** — 1.7b let the
  *value* win, but only when it was a single-char/known marker. r27 qythos9 sent
  **word** values — `{"[x] Create primes.py": "finished"}` — and "finished" wasn't
  in `_MARKERS`, so the recovery discarded the key's `[x]` and reset every task to
  open → plan stuck `0/N` → open-tasks nudge drove a green, finished task to a
  repeat-stop. Fix: status synonyms + `status_marker_for()`, and fall back to the
  KEY's marker when the value is unintelligible. See 4.14. +4 tests (641 green).

## Milestone 2 — Eval-harness trustworthiness
*The gate must catch real regressions without crying wolf on noise.*

- `[x]` **2.1 Variance/n-aware regression gate** — *build 74.* Parts (a) and (b)
  of this item were already shipped by the bootstrap-CI/permutation work: the
  gate compares *intervals* rather than point means and refuses to hard-fail a
  row whose sweeps aren't internally consistent. The **degenerate-sample hole
  named in the original finding was still open**, and measured open: a baseline
  of `[1.0]×3` produced a CI of exactly `(1.0, 1.0)` and hard-FAILed an
  `[0.8]×8` candidate — the r12 false positive, reproduced verbatim.
  - Every gate interval is now widened by a floor standard error of
    `_GATE_MIN_SE/n` (`_score_ci`), so k-for-k identical runs read as *weak
    evidence* rather than certainty. The floor **shrinks with n**, which is what
    lets it coexist with the tuned n=6 behaviour — 0.167 wide at n=3 (swallows
    r12's 0.20 drop), 0.083 at n=6 (a genuine 1.00→0.50 slide still separates
    and FAILs). *A flat Wilson interval was tried first and rejected: at n=6 it
    puts `[1.0]×6` at [0.69,1.0] against `[0.5]×6` at [0.22,0.78], which overlap
    — it would have silenced the regressions the gate exists to catch.*
  - `_GATE_MIN_N = 4` and `_GATE_MAX_N_RATIO = 2.0` close the third clause: too
    few runs, or an n=3-vs-n=8 mismatch, can now only REVIEW, never FAIL.
  - `_bootstrap_ci` stays pure — its zero-width answer is the correct
    *empirical* one; the floor belongs to the gate, not the statistic. +7 tests.
- `[x]` **2.2 Infra-kill scored as model failure** — *build 73.* A run that
  produced no verdict (checker raised — including `ctx.bash`'s 180s pytest
  timeout — no `check.py`, or a turn that died on a transport error) is now
  recorded as **ungraded** via `RunResult.invalid` and excluded from every
  score, instead of landing as a 0.0 that is indistinguishable from the model
  failing the case. Rows and sweeps report `n_invalid`/`invalid_rate`; absent
  stats render as `-`, never `0.00`. The gate goes INCONCLUSIVE over
  `_MAX_INVALID_RATE` (20%) or on any row where nothing could be graded — which
  also removes the inversion where an ungraded row read as a catastrophic drop.
  A **harness timeout is deliberately still scored**: the model really did grind
  past the limit, and that is agent behaviour.
  *Measured impact on history:* only 3 of 28 saved sweeps contained ungraded
  runs, the largest correction being r8-append 0.752 → **0.793** (+0.041). Past
  conclusions survive — but +0.041 is the size of `_GATE_OVERALL_FLOOR`, so the
  bug was one bad run away from mattering. +15 tests.
- `[x]` **2.3 Gate/compare ergonomics** — *build 74.* `evals/README.md` had
  documented only the legacy fixed thresholds ("drops more than 0.15… overall
  over 0.05"), which the variance-aware gate replaced — it described a gate that
  no longer existed. Replaced with the real contract: argument order, the three
  **exit codes** (with the note that `2` = INCONCLUSIVE means *re-run*, not
  revert — it aborts under `set -e` like a failure but is not one), the full
  hard-FAIL table keyed to the actual constant names, the pooled backstop, why
  the p-value is advisory, the refusal conditions, graded-vs-ungraded, and why
  baselines are session-bound. Constants in the doc are checked against the
  module rather than transcribed.
- `[x]` **2.4 Same-session paired A/B for regression checks (build 75,
  2026-08-02)** — shipped as `evals/ab.py`. The design tension below was settled
  in favour of **git worktrees**: no checkout of the live tree, so an interrupted
  run cannot leave the repo on a detached HEAD, and the candidate arm can be the
  *dirty working tree* — which is the question you actually have ("does the thing
  I just wrote help?") and which the checkout recipe could not express at all.
  - **`PYTHONPATH` cannot select the tree.** Verified empirically: the editable
    install registers `__editable___locode_0_1_0_finder` on `sys.meta_path`, and
    meta-path finders run *before* `sys.path`, so a decoy tree at build 999 was
    ignored and `import locode` still resolved to the main checkout. Hence
    `evals/_agent_launcher.py`, which drops the editable finder, puts the
    requested root first, and then **asserts** the import came from there.
  - **Three refusals.** Identical arms (a zero delta from the same code reads as
    "no effect" — the most believable wrong answer available); fewer than 6
    **informative** pairs (a sign-flip test's p floor is 2/2ⁿ, so at n=5 *no*
    outcome could reach alpha — the experiment's answer is fixed in advance, and
    a *tie* is not informative because flipping the sign of zero changes nothing,
    so ties must not be counted toward n; caught by rendering a report with one
    tie in it and noticing W5/L0/T1 at p=0.0625 was being announced as "no
    difference"); and any run that logged **no events**, now `invalid` rather than
    scored 0.0, because an agent that never started leaves an untouched workdir
    that a checker confidently grades zero while every process metric reads clean
    (`clean_finish=True`, zero iterations). That last one is the failure mode an
    A/B must never mistake for a result.
  - Interleaving flips arm order every repeat, so cache warmth and thermal drift
    land on both arms instead of on whichever ran second. A pair is dropped whole
    when either arm is ungraded — substituting 0.0 for an infra-killed baseline
    run would manufacture a large fake win.
  - Also fixed here: `--version` printed bare `__version__`, identical for every
    build, so the obvious way to check which arm you were running could not tell
    them apart. It now prints `__full_version__`.
  - 31 tests in `tests/test_ab.py` (878 total, green). Original entry follows.
- ~~`[!]`~~ **2.4 (original entry)** — *evidence
  hardened 2026-07-25 (Round 21, D75).* A historical score from another session
  is **not a valid baseline** for a sweep run today: build 30 scored 0.62 on
  exec-bugfix qythos9 in the r26 session vs 0.92 in its own r13/r16/r19 sessions —
  pure model non-stationarity, same code. So a single sweep vs a saved baseline
  cannot distinguish a real regression from session drift (exactly the two sweeps
  Round 21 spent proving a "0.92→0.50 regression" was noise). The fix is to run
  **candidate and baseline interleaved, run-by-run, against the same loaded
  server**, so drift cancels in the paired delta. **Interim manual recipe (proven
  in Round 21):** commit the candidate, `git checkout <pre-change-ref>`, run the
  case n≥4 with one `--label`, `git checkout main`, run the same case+n with
  another `--label`, compare the two same-session means — never a historical
  results.json. **Design tension (why this is `[!]` not `[ ]`):** automating it
  means the harness git-checkouts between runs (fragile if interrupted, and the
  working tree must be clean), *or* it shells two worktrees, *or* runs pin a seed
  (Metal non-determinism makes seed-pinning only partial). Needs a decision on
  which before building. **Recipe paid off again in Round 24 (2026-07-26):** the
  manual 3-arm A/B (build 42 / 45 / 46, n=5 each) caught Lever 1 turning a 5/5
  fix into 0/5 — invisible to the 605-green unit suite. Each arm was ~10-12 min of
  gemmacoder12 wallclock; the sequential-checkout recipe worked cleanly. That
  cost/value ratio is the argument for automating this (D80).

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
- `[x]` **4.15 The compaction ratchet + the guards that fought it (builds
  58-61, 2026-08-01).** User: qythos9 "still falls into repeat loops, stops
  before plans are finished ... basically not usable" — then supplied a real
  failing session (`~/Code/skills`, `sync_gke_compute_classes.py`). The eval
  said 31/31 clean. Both were true: **every battery case finished far below the
  75,000-char auto-compact threshold**, so the entire context-management path
  had zero coverage, and builds 55-57 were validated in a regime real sessions
  leave within two tool calls.
  - **The ratchet (b58, `agent/compact.py`).** Fidelity inversion: a short
    confident assistant claim survived compaction verbatim while the tool
    evidence it rested on collapsed to a one-liner, and repeated copies
    accumulated. Measured on the reported shape: 44,591 → 1,865 chars with six
    identical copies of a wrong conclusion, none of its evidence.
    `_dedupe_stale_claims` now runs over the kept region *and* the recent
    window (confining it to the pre-window region left the fresh copies exactly
    where they hurt most), collapsing ≥120-char identical prose replies to one
    annotated copy: "this exact reply was sent N times and never advanced the
    task — do not send it again." Never touches a reply carrying a tool call
    (that would break call/result pairing) or a short ack. Counts merge across
    passes; the marker is held aside during truncation so a later pass can't
    silently un-annotate a still-stale claim. The collapsed-tool-output summary
    no longer says "already used earlier" — it says re-read, don't assume your
    earlier conclusion holds.
  - **Repeat guard vs compaction (b59, `agent/loop.py`).** The two contradicted
    each other: compaction tells the model to re-read, then `seen_streak`
    killed the identical re-read as a loop. `_forgive_rereads` clears
    read-only (`read_file`/`ls`/`glob`/`grep`) streaks and re-arms the nudge on
    every auto-compact. Live A/B: before = repeat-stopped at 9 iterations with
    the question unanswered; after = 14 iterations, 3 compactions, converged,
    correct answer. **Bounded in b62** after the new eval case caught the
    obvious follow-on defect: forgiving unconditionally *disarms* the repeat
    guard precisely when compaction is frequent, because every firing wipes the
    streaks and a real read loop never accumulates one. Measured at a 70k
    budget before the bound: 11 compactions, 24 repeats, 30 iterations, no
    answer. Each signature now gets `_MAX_FORGIVEN_REREADS = 2`; after that the
    guard sees it again.
  - **Reads rendered as failures (b60, `render._salient`).** Verdict-sniffing
    was applied to `read_file` output, so reading any file containing the word
    "error" rendered a red ✗ headlined by that line. `_DATA_TOOLS` now skip the
    sniff; `bash` still gets it.
  - **Coverage (b61 + `evals/`).** `LOCODE_MAX_HISTORY_CHARS` lets one headless
    turn be put in the compaction regime on purpose. `replay.py` now counts and
    shows compactions and forgiven re-reads — they were previously invisible in
    every replay, which is a large part of why the ratchet survived so many
    sweeps. New `evals/night/real_battery.py` case `long-context-find`: six
    ~15k handler modules where the target is identified by *behaviour* (one
    handler reports its neighbour's name in `handled_by`) so no grep can
    shortcut the reading. qythos9 passes it — reads all six, localizes
    correctly, lands the edit on the far side of a compaction.
- `[x]` **4.16 Consecutive-error guard (build 63).** Surfaced by 4.15's case:
  after compaction dropped the file contents, qythos9 invented `notes/golf.py`
  … `notes/tango.py` and burned **nine consecutive iterations** on files that
  never existed. Nothing stopped it. The repeat guard couldn't (each path is
  genuinely a new call); `max_error_stall` couldn't either, because it keys on
  *byte-identical error output* and "no such file: …/golf.py" ≠ "…/hotel.py".
  The fix is content-independent: `_run_calls` now also reports whether **every
  call that ran** errored, and `max_consecutive_errors` (default 4) batches of
  that nudges once — say plainly that everything failed, stop guessing paths,
  `ls`/`glob` and work from names that came back — then ends the turn. Any
  single success anywhere in a batch clears the streak, so a model that is
  failing *and* getting somewhere is untouched. Denied and unknown-tool calls
  are excluded (they never reached a tool; denials have their own counter).
- `[x]` **4.17 Compaction is visible to the model (build 64).** The last half of
  the ratchet, and the one that actually reproduces the user's report. Auto-
  compaction was announced to the *user* and hidden from the *model*: evidence
  vanished from under it with no signal, so it read the gap as forgetfulness and
  re-read — and a re-read costs the same space again, so it compacts again.
  Measured (eval `long-context-find`, six modules totalling 88k chars against a
  70k budget): read alpha…echo, compact, then alpha/bravo/alpha/charlie/alpha/
  charlie/delta/delta, 8 repeats, turn stopped, question unanswered. No loop
  guard can fix that — the corpus genuinely does not fit, so the model *must*
  work file-by-file and record findings, and it cannot know to do that unless
  it is told. Two changes: (a) the loop appends a bounded `Context notice`
  (`_MAX_COMPACT_NOTICES = 2` — the third is boilerplate, and it costs the very
  budget it warns about) saying older tool output was dropped, that re-reading
  evicts something else, and that *replies survive compaction while tool output
  does not*, so state each conclusion in plain text before moving on; (b) the
  shrunk-result summary no longer *opens* with "Re-read or re-run if you need
  it" — in the only regime where compaction fires, that sentence is an
  invitation into the loop. It keeps the anti-ratchet half ("don't trust an
  earlier conclusion") and makes the re-read a deliberate choice.
- `[x]` **4.18 No-information guard (build 65).** From a second live report, and
  a failure mode *none* of the guards above could see: every one of them keys on
  something going **wrong**. Transcript: asked to diagnose why a sync script
  detects no differences, qythos9 read `SOURCE_PATH = "skills/cloud/…"` out of
  the script and went looking for that path in git — `ls-remote <url> <path>`,
  `ls-tree -r HEAD <path>`, the same with `2>&1`, the same again. Six
  consecutive **exit-0, empty** results, four byte-identical, until the repeat
  guard ended the turn with nothing diagnosed. The emptiness *was* the answer
  (no such prefix in that repo) and the harness had no way to say so.
  `max_error_stall` needs errors; `max_consecutive_errors` needs failures; the
  repeat guard needs identical calls and only caught the tail. Two changes:
  (a) `_run_calls` reports a fifth flag — every call that ran **succeeded and
  returned no information** (matched against exact sentinels: the bash rc-0
  sentinel, `(no matches)`, `(empty directory)`, `(empty file)`; whole results
  only, so a grep that finds the literal text "(no matches)" still counts as a
  hit) — and `max_noinfo_calls` (default 3, lower than the error counter because
  a deterministic query repeats its silence perfectly) nudges once toward
  questioning the *assumption* behind the query, then ends the turn;
  (b) `shell.py`'s rc-0-no-output sentinel no longer says only "command
  succeeded". That wording was written for a passing `py_compile`, where silence
  really is success — but for a query it is misleading, and the model reads it
  as "worked but told me nothing" and re-runs. It now carries both readings and
  rules out the unchanged re-run explicitly.
- `[x]` **4.19 The open-tasks nudge stops fighting the repeat guard (build 66).**
  Found while validating 4.18, and worth more than the guard that surfaced it.
  Two guards, each correct alone, combining into a manufactured failure: the
  model edited the script, ran `python3 sync.py`, got the right output — **fix
  landed, verified** — but its own plan still had "test the fix by running the
  script again" open. The open-tasks nudge fired and told it to finish. The only
  action that closes that task is re-running the script. It complied, and the
  repeat guard ended the turn with *"repeated the same tool call without making
  progress."* **Two of three runs were reported as failures on a task that had
  already succeeded** — precisely the "stops before plans are finished" in the
  original user report, with the harness inventing the failure. Same shape as
  4.14 (compaction vs the repeat guard) from the other direction, and the same
  remedy: `_forgive_nudged_verifies` clears the repeat streak for the call we
  just demanded. Scoped harder than the compaction case — read-only tools plus
  bash only when `_is_verify_bash` says it checks rather than mutates — and
  bounded at ONE forgiveness per signature, because nothing was actually lost
  here, so a second identical re-run after we've excused one is a real loop.
  A/B on the same fixture, 3 reps: repeat-stops **3/3 -> 0/3**, every run now
  self-terminating, and the 2/3 that were already correct are finally reported
  as such. The principle, worth keeping: *a nudge that demands work must not
  leave the model in a state where the only compliant action is punished.*
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
- `[x]` **4.9 Repeated mutating edit that "succeeds" but never converges
  (build 42).** *User-reported 2026-07-26, gemmacoder12:* the model re-issued a
  byte-**identical** `replace_lines(start=136, end=137, new=…)` five-plus times,
  each "✓ replaced" with a diff that marched down the file (`@@ -144 → -146 →
  -148…`) — the edit kept **duplicating** the `clone_repo(...)` lines because the
  fixed line numbers pointed at ever-shifting content, and the model declared
  "now parses correctly" every time. The repeat guard missed it: it only grows
  the streak when the **result is unchanged**, and this edit's success echo
  changed every call, so the streak reset to 1 forever and the corrupting edit
  ran without bound. Fix: a byte-identical call to a content-mutating tool
  (`_MUTATING_EDIT_TOOLS` = write/append/edit_file/replace_lines) now counts
  toward the repeat streak **regardless of the shifting echo** — re-applying the
  same edit is never progress. When the trip is a *varying-result* edit (the true
  duplicating signature, tracked in `repeat_varied`), a tailored nudge fires:
  "the file has already changed, you may be DUPLICATING content / your line
  numbers have SHIFTED — STOP, re-read the file, make one corrected edit." A
  plain no-op repeat keeps the existing generic message. Both pains: it converges
  (stops after `max_repeat_calls-1` applications instead of looping) and the stop
  is legible. +1 loop test (597 green). LOG Round 22. *This corrects the Round 20
  claim that residual edit-flailing was all capability-bound — I had only mined
  FAILED edits; this is a SUCCEEDING-but-non-converging loop, a real harness gap.*
- `[x]` **4.13 Overnight prompt-battery round — two flail fixes rejected, then a
  distinct false-completion pathology found + fixed (2026-07-27).** Driven by the
  4.12 observability suite + a new varied-task battery
  (`evals/night/run_battery.py`, 8 cases). Full method + tables in LOG Rounds
  26–27. Outcomes:
  - **build 49 (kept):** bash silent success `(no output)` → `(exit 0 — command
    succeeded, no output)`. Paired A/B (stash-toggle, both models × 3 reps):
    flail-**neutral**. Kept as a **visibility** win (pain #1 — `(no output)` is
    ambiguous to a human reading `--show-events` too), explicitly **not** claimed
    as a flail fix.
  - **build 50 (reverted):** edit "✓ now parses cleanly" on invalid→valid .py +
    a reject-message "already parses as-is" hint. Paired A/B: flail-**negative**
    (repeat-stops 1→5; the *target* case gemma went all-done → 2 stops + 1 fail).
    Rejected per D80. → **D84: weak-model flail does not yield to clearer
    tool-result text** — it's a stopping-behavior problem; spend the clarity lever
    on human visibility, not the model. **D85: the pass-1 baseline is not a valid
    control** — only a same-session stash-toggle A/B is (non-stationarity, D75).
  - **build 50 (shipped) — hallucinated-verify false-completion gate (Round 27).**
    The `syntax-fix` false-completion first read as a *plan* defect (the model
    `update_plan`-marked "fix" `[x]` done without editing, and plan.py:156 replied
    *"All tasks are done. Give your final answer now."*). Reading the transcript
    corrected the diagnosis: the model had **hallucinated** — it read
    `def parse(line)` (missing colon) and asserted *"the file is syntactically
    correct and already compiles"* **without ever running py_compile**, so the
    plan mark was downstream of a bad verify, not the cause. This is a **distinct
    third pathology** — confident *premature/false completion*, invisible to every
    flail counter (zero repeats/fails/no-ops, a clean "answered" stop); only the
    battery's real `check()` catches it. Fix = extend build-40's seen-green (test)
    gate to the **compile/run/import** class (`locode/agent/loop.py`): a
    `_saw_verify_ok` flag (set when an `_is_verify_bash` command exits clean), a
    `_VERIFY_CLAIM_RE` (compiles / py_compile succeeds / syntactically correct /
    no syntax error / runs-without-error), and a finish-cascade nudge that fires
    **once** when the reply claims a check passed but none ran clean this turn.
    Double-gated (claim AND `not _saw_verify_ok`) → a real verify or a no-shell
    task can't trip it. Also **fixed a latent crash** the gate surfaced:
    `_is_verify_bash` did `(cmd or "").lower()` and died with `'list' object has
    no attribute 'lower'` when a model emitted `cmd` as an argv list — now
    coerced. Paired A/B (stash-toggle, crash-free re-run, syntax-fix+logic-bug ×
    2 models × 5 reps): **target case gemmacoder12 syntax-fix 0/5 → 4/5 done**;
    qythos9 **5/5 both arms** (zero false-fire); logic-bug unaffected. Extra iters
    (4.0→5.1) are the gate making the model *work* instead of falsely quitting in
    2 iterations; the 2 treatment repeat-stops are one benign post-fix re-verify +
    one run that also failed in control (not gate-induced). +6 tests, suite 637.
    Left plan.py:156 **unchanged** — it was not the lever; the plan mark honestly
    reflected the model's (wrong) belief, which the verify gate now corrects at
    source. LOG Round 27.
- `[x]` **4.14 The "open plan tasks" re-do loop — two root causes (2026-07-27).**
  Same symptom both times: qythos9 flails to a **repeat-stop re-doing a GREEN,
  finished `add-test` task** because the plan never reads complete, so the
  open-tasks nudge fires forever. Found by reading pass3 + ab_plandict transcripts
  (D87). Full method in LOG Rounds 28–29.
  - **build 51 (9249bdf):** `update_plan` `{task: status}` recovery reset every
    task to OPEN when the dict *value* was an unrecognized status word — qythos9
    sent `{"[x] Create primes.py": "finished"}`, "finished" wasn't in `_MARKERS`,
    so the key's `[x]` was discarded and the plan stuck at `0/N` forever. Fix:
    finished/complete/completed/not-started/in_progress/started synonyms; new
    `status_marker_for()`; when the value is unintelligible, KEEP the key's own
    marker. Extends 1.7b. `ab.py` now toggles comma-separated multi-file changes.
  - **build 52 (23e440d):** on CLEAN-array plans the model runs the suite green
    then narrates "All tests pass" **without** marking its `[>] run pytest and
    verify` task done → plan stuck `2/3` → nudge → re-runs the passing tests. Fix:
    before the open-tasks nudge, if a green pytest result already appeared this
    turn AND the current task is run/verify-tests-shaped (`_VERIFY_TASK_RE`,
    verb-gated so "Create test_primes.py" is excluded), `Plan.complete_current()`
    credits it. Misfire is benign by construction (real green + a run-tests task).
  - Both A/Bs were **dormant** (D89: non-stationary trigger no-showed; the build-52
    session was wall-budget-heavy) but showed **no regression**; the fixes rest on
    the directly-observed transcripts + tests (641 / 646 green). **D88: a plan
    done-counter stuck at zero is a loop bug, not cosmetic. D89: a dormant-path A/B
    is neutral — confirm the fixed path fired before crediting/faulting the numbers.**
- `[x]` **4.12 Session observability: see a run as the user sees it on screen
  (2026-07-26).** Prompted by the user: "I'm unclear what you can *see* of locode
  as a CLI tool ... I'm seeing a lot of repeats and failed tool calls and I'm
  concerned you aren't noticing." Root cause: headless `locode -p` streams only
  the model's prose to stdout; every tool call/result/nudge went solely to the
  `--log-events` JSONL, which the eval sweeps scraped into "compile=PASS
  finish=STOPPED" — discarding the turn-by-turn detail where repeats and failed
  edits live. Two complementary pieces, both reusing `locode/ui/render.py` (the
  REPL's own formatters) so there's a single source of on-screen truth:
  - **`evals/replay.py` + `evals/watch.sh` (eval harness, no build bump).**
    replay feeds a recorded event log back through render.py, reconstructing the
    on-screen transcript with pathology flags (🔁 repeat call, ∅ no-op, ✗ failed
    edit, 🛡 build-47 syntax-guard save, ✓ green test) and a loud VERDICT header
    (iters/wall/tools, fails/no-ops/repeats, nudges, stop reason). watch.sh runs
    a task headless and replays it in one command. +12 tests. Verified on the
    build-46 flail log: surfaces all 5 failed edits, 3 no-ops, the repeat, and
    the root cause (model re-edited `sync()` while never touching the broken
    `get_changed_files`).
  - **`--show-events` on headless `-p` (build 48).** Renders the on-screen
    transcript — prose interleaved with clean ⚙/✓✗/⟳ lines and edit diffs — to
    stdout in one capturable stream, so a captured run reads like the interactive
    one (and the repeating prose the user flagged is finally visible). New
    `locode/ui/headless.py` (HeadlessView) reuses render.py + StreamSink (```tool
    fence suppressed → clean tool line). Flag-gated: default `-p` unchanged, so
    the harness's stdout parsing is untouched. +7 tests. Suite 630 green.
- `[x]` **4.11 Refuse edits that turn parseable Python into a SyntaxError
  (build 47, 2026-07-26, live-A/B validated).** Follows the user's build-46
  trace ("still seeing failed edits and repeating"): the first `edit_file` added
  an unclosed paren + a triplicated block, and because the syntax check was only
  *advisory* (`_syntax_warning`), the corrupted file **landed** — after which the
  weak model spent the whole turn fighting a broken file it couldn't dig out of.
  New `_syntax_reject` (fs.py) refuses to apply an edit that flips a .py file
  valid→invalid, returning it to the last-good state the model already read, with
  a targeted retry message. Scoped tight so it never blocks real work: only .py,
  only the valid→invalid transition — an already-broken file is presumed under
  repair and any edit passes (the advisory warning still covers that). Wired into
  both `edit_file` and `replace_lines` before their writes. +6 fs tests, 2 old
  warn-and-apply tests rewritten to expect rejection; suite 611 green.
  **Live A/B (LOG Round 25, gemmacoder12, logging-injection fixture, n=4/arm):**
  b47 flailed *less* — 0 terminal repeat-stops vs 2/4 on b46 warn-and-apply, and
  fewer iters/nudges — with **no false-positive reject-loop** (the Lever-1 risk,
  cleared). Neither arm reproduced actual corruption on this fixture, so the
  guard's corruption-prevention is proven by the units + the user's real trace;
  the A/B's contribution is confirming no regression and a directional win.
- `[x]` **4.10 Three anti-cycling levers on top of 4.9 (builds 43-46,
  2026-07-26, live-A/B validated).** 4.9 catches the loop *after* it starts;
  these attack the causes. User asked "why do these models repeat and cycle — can
  we add nudges to remember what it did after each edit," then approved all three.
  **A same-session paired A/B on gemmacoder12 (LOG Round 24) then caught Lever 1
  as a regression and the fix restored parity — see the build-46 note.**
  - **Steer off line-numbers → corrected to *route indent fixes to* replace_lines
    (build 43, fixed build 46).** Weak models pick their editor from the tool
    descriptions. The build-43 version demoted `replace_lines` ("LAST-RESORT —
    PREFER edit_file") — which **backfired**: an indentation fix (the user's own
    bug class) *needs* replace_lines because edit_file preserves indentation and
    no-ops an indent-only change (1.5/1.6). The A/B measured it: control 5/5 fixed
    vs build-45 **0/5**. Build 46 inverts it: edit_file's description says it
    cannot do an indentation-only change and points those fixes at replace_lines;
    replace_lines reads as the RIGHT tool for indentation/whitespace (keeping the
    stale-numbers/duplication warning). Re-ran → **5/5**, back to parity.
  - **Verify-gate (build 44).** The deeper fault is *open-loop editing*: edit,
    never run or re-read, edit again, never learn if it worked. New per-file gate
    (`agent.max_unverified_edits`, default 3) counts consecutive mutating edits
    with no look at ground truth; a verify bash run (py_compile/pytest/python/…)
    or a re-read re-arms it, then a one-time nudge tells the model to look before
    editing again. A poke-around `ls`/`cat` is not credited. +4 tests.
  - **Episodic action-ledger (build 45).** When a cycling nudge fires (4.9 repeat-
    edit or the verify-gate), it now prepends a terse turn recap — "So far this
    turn you have: edited f.py 5×, run a check 1× (still not green)." Selective by
    construction (only those already-gated moments), so no context bloat / JSON
    corruption. +2 tests. Suite 605 green. **Levers 2+3 fired on all 5 corrected
    (build-46) A/B runs without breaking the fix — confirmed harmless; Lever 1
    was the sole regressor (D80/D81).**
- `[x]` **4.8 Turn-ending legibility is data-confirmed closed (2026-07-25).**
  Categorized all 147 recent turn-endings by what the *user actually sees*: 46%
  prose answer, 36% clean-stop with a legible reason, 15% short claim ("All tests
  pass."), and **only 3.4% (5 runs) a raw tool/json garbage block — all of them
  pre-build-41 logs, i.e. exactly the case 4.7 now converts to a clean stop.**
  With 4.5 (legible verdict), 4.6 (seen-green gate), and 4.7 (truncation-stop)
  shipped, the identifiable *turn-ending* visibility defects are covered. What
  remains in the non-clean endings is **capability-bound** — plausible-but-wrong
  code, the `new==old` "highlighting" dead-end (Round 20 edit-failure mining) —
  which no harness lever fixes; Option D (r19) already showed a 24B local model
  doesn't clear those walls either. Further visibility gains, if any, are in
  *during-turn* (real-time progress) not turn-endings. LOG Round 21.
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
  - **Update 2026-07-28 (builds 55/56): two NEW harness levers land, and lever #2
    is a decisive same-session A/B win — the "edit-path levers exhausted" framing
    was incomplete.** From the R38 frequency-backed edit-failure map, two of three
    approved levers shipped. **#1 already-applied short-circuit (build 55):** when
    `old` produced no real change but `new` is already present and differs in
    content from `old`, edit_file/replace_lines now answer with a NON-error
    no_change ("already applied — don't revert, move on") instead of the
    is_error=True that made a model revert its own working fix (pass11/12 revert
    loop); safety nets (nochange streak, repeat streak) intact. **#2
    deletion-steering (build 56):** reframed the tool descriptions so edit_file
    (content-anchored `new=""`) is the PREFERRED deletion path and replace_lines
    steers deletion away from stale line numbers — kept surgical so replace_lines
    stays the indentation tool (b45 lesson). **A/B (gemma, remove-block, 5+5,
    D75/D85 stash-toggle, ONLY the description text toggled): treatment 5/5 clean
    vs control 0/5 Traceback.** Number-anchored multi-delete renumbers under
    itself → corruption; content anchors are shift-immune. So `remove-block`'s
    ~80% gemma failure was tool-choice (harness-fixable), NOT the 3.1 capability
    wall — refines the 4.4 assessment for the deletion sub-case. Lever #3
    (completion-gate, ~29% plan-only false-completion) remains unbuilt/unapproved.
    (LOG Round 40.)
  - **Update 2026-07-28 (build 57): lever #1 extended to DELETIONS; remove-block
    now 11/11.** A build-56 regression sweep replay showed lever #2's steering
    working (gemma deletes via edit_file new="") but a residual corruption: after
    a correct+verified delete, gemma RE-submits it, `old` is gone → not_found
    ERROR (lever #1's len>=3 guard skipped empty `new`) → the error suggested
    replace_lines → line-number re-delete on shifted lines over-deleted the return
    (out='None'). Fix: a not_found deletion (empty `new`, `old` truly absent) is
    "already done" — non-error, and explicitly steers OFF line numbers. Validated
    gemma 6/6 clean with ZERO flail; combined 11/11 on a former ~80%-fail case.
    Also fixed an eval-harness bug the sweep exposed (append_file missing from the
    battery allowlist → false qythos9 Traceback). (LOG Round 41.)
  - **Update 2026-07-28 (post-build-57, no code change): stability probe →
    lever-#3 evidence.** A reps=3 probe on the 4 "benign flail" cases confirmed
    append-func/deep-nest/remove-block flails ALWAYS end correct (repeat-guard
    noise, safe). But it reclassified **diff-report gemma from a reps=1 lucky
    done=Y to a real 2/3 failure** — and rep r2 is a **pure false-completion:
    1 iteration, ZERO tool calls, self-terminated "answered", silent wrong output
    (`'\n'`)** on a realistic ~50-line multi-function file. Current stop-nets can
    NEVER catch this shape: with no mutating action, no repeat/noop/error streak
    accrues. This is the exact lever-#3 (completion-gate on zero mutating actions)
    target — logged as accumulating justification. **Lever #3 remains
    unbuilt/unapproved; needs explicit user OK before building.** Also reworded
    the insert-const case (harness-only) to remove a "comma-space" literal-string
    trap that produced a false-negative qythos9 signal. (LOG Rounds 42–43.)
  - **Update 2026-08-02 (build 80, BUILT → MEASURED → REVERTED): rewording the
    silent-rc0 message does NOT buy a clean finish. Do not retry this lever.**
    Hypothesis: on syntax-fix the model fixes the file, runs `py_compile`, gets
    `_EMPTY_OK` ("ran fine but printed nothing… if it was a query, nothing
    matched"), reads that as *no confirmation*, re-runs the identical command,
    and dies to the repeat guard — so a verify-specific message ("the check
    PASSED… this is confirmed: do not re-run it… say what you changed and
    finish") should convert those into clean finishes. Built it as a second
    sentinel selected by a token predicate, with the loop's `_is_verify_bash`
    delegating to the same shell-tool function (single source of truth).
    **Paired A/B (`verifyok-msg`, base 68bb85a vs live, gemmacoder12_4bit,
    syntax-fix, r=10): clean-finish base 1/10 → cand 0/10, mean iterations 6.9 →
    7.0, all 20 runs scored 1.00 (10 ties, paired test structurally
    INCONCLUSIVE — the effect isn't in the score).** The new text *was* served
    (confirmed verbatim in the candidate event logs) and the model re-ran the
    identical `python3 -m py_compile parser.py` anyway. Wording is not the
    binding constraint; this model re-verifies reflexively regardless of how
    unambiguous the result is. Reverted rather than shipped: zero measured
    benefit, and the predicate matched bare `python`/`python3`, which would have
    told a model that a *buggy* `python3 changes.py` (rc 0, no output — verified
    by hand on diff-report's seed) had PASSED and it should finish. That is a
    false-completion generator aimed at the exact case built to catch false
    completions.
    **What the run did expose, and the recommended next lever:** these turns are
    *successes mis-reported as failures*. The file is fixed, the compile is
    green, and locode ends the turn with "the model repeated the same tool call
    without making progress." The fix is in the repeat guard, not the message:
    when it fires on a turn that has **mutated** and holds a **green verify**
    (`_saw_verify_ok`), end the turn as done with a truthful reason instead of
    as a flail. Unbuilt — it changes turn-ending semantics, so it needs its own
    A/B and an explicit OK.
  - **Update 2026-08-02 (build 81, SHIPPED): the recommended lever above, built
    and measured — clean-finish 0/10 → 10/10 on syntax-fix.** The repeat guard
    treated every repeat as a dead end; one shape isn't. When the repeat is a
    VERIFY re-run, an edit actually LANDED, and the LATEST verify is green, the
    turn ended as done instead of as a flail. **Paired A/B (`doneverify`, base
    68bb85a vs live, gemmacoder12_4bit, r=10): base 0/10 clean, cand 10/10,
    mean iterations 7.0 on both, score 1.00 on both arms — the work was always
    correct, only the reporting was wrong.** Transcript-verified rather than
    taken from the metric: read → `py_compile` RED → edit lands → `py_compile`
    green → identical re-run → repeat nudge → the new exit. That first red
    verify is why the flag is latest-wins; `_saw_verify_ok` is sticky and would
    have been the wrong signal. Two new flags, both needed because neither
    existing one can fall back to False: `_landed_edit` (a mutating edit
    SUCCEEDED, vs edit_tally which counts attempts including not_found
    failures) and `_last_verify_ok`. +4 tests, three of them the negatives —
    a repeated broken edit, a green check with no landed edit, and a verify
    that has started failing all still repeat-stop.
    *Scope note: this fixes locode misreporting a success, not the model's
    reflex to re-verify. Iterations are unchanged; the model still burns the
    same three checks. The remaining win would be getting it to stop after the
    first green one, which build 80 showed is not reachable by wording.*
  - **Update 2026-08-02 (build 82): directory-named change requests.** See the
    commit — `_asks_for_a_change` now also anchors on a NAMED directory,
    closing 1 of the 4 gate escapees with zero regressions across the 37-prompt
    sweep. Rejected on measurement: slash-paths (only corpus match was
    `8080/api.`) and the no-anchor variant (34/37, incl. `already-correct`).
    **Still open: 3 escapees remain**, all briefs that name no target at all by
    design (`locate-symptom`, `add-json-flag`, `keep-tests-green` — "the prompt
    names a symptom, not a file"). Closing those means dropping the anchor,
    which the sweep shows is not safe. Also unfixed: `_CHANGE_VERB_RE` has no
    "copy".
  - **Update 2026-08-02 (build 83, full-battery regression check): clean-finish
    42% → 71% suite-wide, zero false completions.** Build 81 changed turn-ending
    semantics on the evidence of ONE case, so the point of this sweep was the
    downside, not more upside. Paired A/B over all 8 cases (`b83-regression`,
    base 68bb85a vs live, gemmacoder12_4bit, r=3, 48 runs): **no score
    regression** — W4/L3/T17, mean delta +0.019, sign-flip p=0.67, i.e. the arms
    are indistinguishable on score, which is the expected and desired result for
    a reporting fix. **Clean-finish 10/24 → 17/24.** The new exit fired on 5
    runs (3 syntax-fix, 2 exec-bugfix) and **every one scored 1.00** — both
    those cases scored 1.000 across all reps on both arms, so no firing landed
    on a run that had not actually succeeded. The three-condition gate holds
    outside the case it was built on.
    **Residual, mined from the same 48 runs: all 7 remaining non-clean candidate
    endings are repeat-stops on EDITS, not verify re-runs**, so build 81
    correctly stayed out of them. Three are `e2e-spec-to-code` (the 3.1
    capability wall), two `exec-stall-trap`, one `exec-from-plan`. The seventh,
    `diff-report` r1, is a **second instance of the same success-reported-as-
    failure class**: edit lands → `python3 changes.py` prints the correct
    `added/modified/removed` → the model re-submits the identical edit → the
    tool answers "This edit is ALREADY DONE" → repeat-stop, on a run that scored
    1.00.
    **Deliberately NOT extended to cover it.** The tempting generalization is to
    let an "already done" edit repeat satisfy condition 1 alongside a verify
    re-run. The blocker is condition 3: on syntax-fix the green verify is
    `py_compile`, which proves the actual goal, whereas here it is a bare
    `python3 changes.py`, whose exit 0 proves only that the script did not
    raise — it says nothing about the output being right. That is precisely the
    weak-evidence trap build 80 was reverted for, and `diff-report` is the case
    whose documented pathology IS false completion (a zero-tool-call self-
    terminate with silent wrong output, Rounds 42–43). Shipping a new completion
    path into that case on the evidence of one run would repeat the build-80
    mistake. Closing it needs a predicate that separates "a checker passed" from
    "a script exited", plus its own A/B.
    Also examined and left alone: `_CHANGE_VERB_RE` has no "copy". No prompt in
    the 37-case corpus exercises it, so adding it would be an unmeasured
    widening — and a missing verb fails in the SAFE direction (the gate stays
    quiet, i.e. today's behaviour), while a bad addition fires on questions.

## Milestone 5 — Competitive teardown (2026-08-06)

Source teardowns of Aider, Cline/Roo, OpenHands and SWE-agent, read from source
rather than docs. Two standing rules for everything in this section: a finding
only lands with a measurement **on our corpus**, and nothing here is credited
until it survives an A/B. Two candidates were already declined on measurement
(5.7) — that ratio is expected, not a failure.

### The measurement that reframed the milestone

Before the teardowns, the corpus was mined for what actually kills our turns.
**Byte-identical `old == new` edits are the single largest failure mode we
have**, and they match exactly what the user reports seeing live:

- 309 of 1842 `edit_file` calls (16.8%); 137 of 651 runs hit at least one.
- 108 of those 137 resent it **after** being told not to; 94 resent a
  byte-for-byte identical call (same path, same text).
- Clean finish **52% → 18%**. Repeat-stop deaths **40% → 69%**. This one
  pathology causes **94 of the 260 repeat-stop deaths in the corpus (36%)**.
- The rejection already names `replace_lines`, which structurally cannot no-op.
  Across all 309 no-op edits the model took that advice **once**; only 10 of the
  137 runs ever called it. The most common next action, **79 times**, is another
  no-op edit.
- **Not our parser.** Clean JSON, single-quoted, raw-newline, trailing-junk and
  tab-vs-space payloads all keep `old` and `new` distinct.

- `[x]` **5.1 Redact a rejected no-op call from history (build 87).** The
  mechanism behind the numbers above: a rejected call stays in history verbatim
  as `[assistant: the call][user: the error]`, so by the third attempt the model
  is reading three worked examples of the call we are asking it to stop making.
  Build 80 already settled that rewording the rejection does not move this
  (clean-finish 1/10 → 0/10), so the lever is mechanical — delete the example
  instead of arguing with it. **SWE-agent reached the same design
  independently:** its `max_requeries` loop puts a rejected action into a
  temporary history that is never persisted to the real trajectory. A one-line
  marker replaces the fence rather than deleting it, because an assistant turn
  that falls silent before a "Tool results:" message is incoherent history and
  native tool-callers answer it by narrating an intent and then stopping.
  Behind `agent.redact_noop_calls`. +7 tests.

  **A/B `b87-noop-redact` graded** (exec-bugfix + exec-stall-trap × qwencoder14,
  r=5). Score `+0.0001, p=1.0` — INCONCLUSIVE, and correctly so: 7 of 10 pairs
  tied, which is the expected reading for a change that alters how a turn ends
  rather than what it produces. On turn-endings:

  | case | clean finish | repeat-stops | "repeated call" nudges |
  |---|---|---|---|
  | exec-bugfix | 0/5 → 0/5 | 5 → 5 | 7 → 5 |
  | exec-stall-trap | 2/5 → **4/5** | 3 → **1** | 6 → **1** |

  The split was predicted before the run: `exec-bugfix` is 82% the 5.8 fault,
  which this lever does not touch, so flat there was the expected outcome rather
  than a refutation. `exec-stall-trap` moved on all three measures. At n=5 the
  clean-finish jump is two runs — suggestive, not established; the nudge counts
  (6 → 1) are the larger event sample and agree. **Kept on**, to be re-measured
  at higher n once 5.8a's effect is separated out.

### Confirmed feature gaps

- `[x]` **5.2 Project-instructions file (build 89).** `locode/context.py` walks
  the git root down to cwd, nearest file last so the most specific wins, and
  renders into `build_system_prompt`'s `extra` seam — which had existed unused
  since it was written. Read once at construction: the system prompt is stable
  and first, so the prompt cache reuses it for the session at one prefill.
  Defaults, all reversible under `[context]`: `AGENTS.md` and `LOCODE.md`, with
  a hard 8000-char budget spent in file order (a local model has ~32K tokens;
  an unbounded file would spend it before the conversation starts, and `0`
  disables). **`CLAUDE.md` deliberately excluded** — it is another tool's file
  and silently absorbing another vendor's instructions is a surprise, not a
  feature; one config line adds it. Outside a git repo only cwd is consulted so
  a stray `~/AGENTS.md` cannot leak in, and root detection tests `.git`
  *existence* rather than `is_dir` because a worktree's `.git` is a file and the
  eval harness runs agents in worktrees. +18 tests.

  Caught in review and worth remembering: `Config._merge_toml` names every
  section explicitly, so the new `[context]` block was silently ignored — the
  defaults read back correctly while no override took. Pinned by a test, plus
  one asserting `config.toml.example` has not drifted from the dataclass.
  No A/B: inert on the eval corpus, whose workspaces are `mkdtemp` dirs with no
  instruction files. Its value shows on real repos.
- `[ ]` **5.3 Undefined-name linting on edited Python.** We `compile()`, which
  catches syntax only. **Both** Aider and SWE-agent additionally run flake8
  restricted to fatal codes — Aider `E9,F821,F823,F831,F406,F407,F7xx`,
  SWE-agent `F821,F822,F831,E111,E112,E113,E999,E902` — deliberately excluding
  style noise so the loop never nags about PEP8. F821 (undefined name) targets
  our documented 3.1 wall precisely: code that *parses but is wrong*. Needs a
  decision on the flake8 dependency (stdlib-first policy); a `compile()`-plus-
  `symtable` approximation may cover most of it without a new dep.
- `[ ]` **5.4 No checkpoint / undo.** Cline keeps a shadow git repo and can
  restore workspace files, task state, or both. locode has no way to revert a
  botched agent run. Ranked below 5.2/5.3 because git already covers the
  careful user, but it is the most-requested safety net in this class of tool.
- `[!]` **5.5 Architect/editor split.** Aider runs one model to reason in prose,
  then a **second, cheaper** model to convert that prose into edits — decoupling
  "can reason about the change" from "can emit exact-match diff syntax". That
  split is exactly where our local models fail, and locode already has the
  multi-alias machinery to do it. Needs a decision: it doubles model residency,
  and the memory guard (build 69) says two resident models do not fit in 24 GB.
  Possible as sequential load/unload; costs a model swap per turn.
- `[ ]` **5.6 Nudge budget is unbounded and unshared.** Aider caps *all*
  reflection at `max_reflections = 3` across edit-apply, lint and test failures
  combined, and skips lint/test feedback entirely when the edit itself failed.
  OpenHands nudges once at threshold−1, **de-dupes the nudge per error id**, and
  hard-stops one repeat later. locode has ~10 independent nudge counters that
  can compound — the user's dead session burned **5 nudges in 12 iterations**.
  Worth a shared ceiling and per-cause de-duplication.

### Declined on measurement (do not retry without new evidence)

- `[x]` **5.7a Steering models to whole-function rewrites.** Aider measured a
  **30–50% increase in editing errors** when they disabled "high level diff"
  prompting, which is a strong result — but it **does not transfer**. On our
  corpus the no-op edits have *longer* `old` blocks (median 77 chars) than the
  successful ones (median 49). Their pathology was GPT-4-turbo *laziness*
  (omitting code); ours is a copy attractor. Different failure, opposite fix.
- `[x]` **5.7b Switching edit format to unified diff.** Aider's famous udiff
  result is a narrow historical fix for `gpt-4-turbo`, auto-selected only on
  that model-name match; every modern model in their settings table routes back
  to SEARCH/REPLACE. No reason to change our format.
- Also noted: Aider **deleted** their fuzzy matcher six days after shipping it
  (commit `00512e3d1`, "no fuzy matching, stronger prompt for whitespace") and
  it has been dead code for three years. We keep a fuzzy tier at 0.8 — worth an
  audit that it is genuinely human-gated on every path, but their retreat was
  from an *ungated* matcher, so this is not yet a reason to remove ours.

### 5.8 The repeat guard kills the debugging loop (the real "stalls are the norm")

Chasing the residual after 5.1 turned up a far bigger fault than the no-op edit,
and it is **ours, not the model's**. Measured over the same corpus (656 event
logs), asking not "which runs contain a repeat" but "which call was being
repeated at the moment the run died":

| repeated call at death | runs | share |
|---|---|---|
| `bash` | 212 | **80%** |
| `edit_file` | 22 | 8% |
| `update_plan` | 15 | 6% |
| `read_file` | 11 | 4% |
| `write_file` / `replace_lines` | 4 | 2% |

The dominant killer is not a broken edit going round again — it is the model
**re-running its test command**. And of the 578 consecutive identical `bash`
pairs, **475 (82%) had a mutating call in between** and 71% returned *different*
output. That is not a flail; that is edit → test → edit → test, the correct
debugging loop.

The bug is in the streak rule at `loop.py:958-964`. A repeat resets only when
`result_sig` changes, so two rounds whose test output happens to be
byte-identical — fix one of two bugs, or fix something masked by an earlier
failure — climb to a streak of 2 (`max_repeat_calls - 1`) and the run is nudged,
then stopped, *with real edits sitting between the two calls*. Scoring the
terminal pair of every repeat-stop death:

- **140 of 265 (53%) had a mutating edit land between the last two identical
  calls** — the model was re-verifying a change we told it to make, and we
  killed it for "repeating the same tool call without making progress".
- Per case: `exec-bugfix` **56/68 (82%)**, `e2e-spec-to-code` **50/73 (68%)**,
  `exec-stall-trap` 24/47 (51%). `syntax-fix` 4/55 — its deaths are genuine.
- The other 125 (47%) had nothing in between and are real no-progress loops.

This is the mechanical explanation for "repeating and stalling out is the norm",
and for the reported transcript that produced a *correct* prose diagnosis and
died at 12 iterations with "1 file changed".

**Scale.** Stop reasons across all 662 logged runs: 51% self-terminate cleanly,
**41% die on the repeat guard**, and *every other cause is ≤1%*. Combining that
with the 53% false-positive rate, roughly **22% of all runs — better than one in
five — are killed by this bug.** Note the second line of that table:
`edits kept hitting the same error` fired **8 times (1%)**. The net that keys on
the right signal almost never gets to run, because the call-identity guard trips
first: it stops at two identical results and the error net needs three.

Reproduced as a unit test before touching anything
(`test_retesting_after_a_real_edit_is_not_a_repeat`): four *distinct* edits, all
landing, each followed by the same test command — dead after `bash` had run
**twice**, with `# fix 1` through `# fix 4` sitting in the file.

- `[x]` **5.8a Reset the repeat streak when the workspace moved (build 88).** A repeat is
  only a repeat if nothing changed between the two calls. Track a monotonic
  count of edits that actually **landed** (`loop.py:1212` already distinguishes
  landed from attempted — `_landed_edit` is the right signal but is sticky, so
  it needs a counter), record the count each signature last ran at, and reset
  the streak when it has advanced. Restrict the reset to batches that are not
  themselves mutating, or the existing `repeated_edit` exception — which
  deliberately counts a repeated edit even when its echo shifts — would reset
  itself and be defeated.

  **Why this does not remove the safety net.** Three independent stops survive,
  and the first is better-targeted than the guard being relaxed:
  `max_error_stall = 3` keys on the **error text**, so an edit-then-retest loop
  whose failure never changes still dies after three identical failures, while
  a failure that *does* change is real progress and correctly resets;
  `max_consecutive_errors` catches nothing-succeeding; `max_iterations = 50` and
  the wallclock bound the rest. The outcome is the right signal for "stuck"; the
  call identity never was.

  Note the interaction to preserve: the done-on-repeated-verify finish
  (`loop.py:876`) requires the check to have been "re-run unchanged", which by
  definition means no edit landed in between — so the reset does not apply and
  that exit is untouched.

  **Independently corroborated by Cline.** Its loop detector
  (`sdk/packages/core/src/runtime/safety/loop-detection.ts`,
  `softThreshold: 3, hardThreshold: 5`) keys on the same signature we do — tool
  name plus sorted-JSON args — but counts identical **consecutive** calls. An
  intervening edit breaks their streak *by construction*; ours is cumulative
  across the turn and survives any amount of real work in between. Same
  conclusion, reached from their design and from our corpus separately. Cline
  also keeps loop detection and the mistake limit as independent knobs (6 vs.
  soft-3/hard-5) where Roo couples them (`ToolRepetitionDetector` is constructed
  with `consecutiveMistakeLimit`); locode already has them separate
  (`max_repeat_calls` vs `max_error_stall`), which this argues we should keep.

  **Shipped** as `agent.repeat_resets_on_landed_edit` (default on; off restores
  the old behaviour for an A/B). `_landed_edits` counts edits that actually
  landed, `sig_mut_mark` records the count each signature last ran at, and the
  streak resets when it has advanced. 944 passed; the three tests are
  `test_retesting_after_a_real_edit_is_not_a_repeat` (the reproduction),
  `test_retesting_with_nothing_in_between_still_stops` and
  `test_a_repeated_identical_edit_still_counts_as_a_repeat` (the two guards that
  must survive).

  **Graded 2026-08-06 — `b88-verify-after-change`** (exec-bugfix +
  e2e-spec-to-code × qwencoder14, r=5, the 82% and 68% cases).

  *Score: INCONCLUSIVE, and my prediction was wrong.* I expected the score to
  move here unlike 5.1, on the reasoning that a turn which gets to finish
  changes the artifact and not just the ending. It didn't: `+0.0200`, p=1.0,
  **9 of 10 pairs tied**, 1 informative. Longer survival is evidently not
  sufficient for a better artifact — the model uses the extra turns without
  converting them. Worth remembering the next time a mechanical fix tempts a
  score prediction.

  *Turn endings: the fix works, and works by the predicted mechanism.*

  | | base | cand |
  |---|---|---|
  | **exec-bugfix** clean-finish | 0/5 | **2/5** |
  | exec-bugfix repeat-stops | 5 | 3 |
  | exec-bugfix iters / mutations | 9.6 / 5.6 | 12.4 / 7.4 |
  | **e2e** clean-finish | 0/5 | 0/5 |
  | e2e repeat-stops | 5 | **2** |
  | e2e `error unchanged` stops | 0 | **3** |
  | e2e iters / mutations | 25.4 / 16.4 | 22.8 / 17.2 |

  exec-bugfix is the headline: it was **flat at 0/5 under build 87** because
  82% of its deaths were this fault, and it moves the moment the fault is
  fixed. Runs now last longer and edit more, which is what "stopped killing
  them mid-debug" should look like.

  e2e's clean-finish does not move, but its **stop reasons transfer exactly as
  designed**: repeat-stops 5→2 and the honest error-text net picks up 3. That
  net fired **8 times in 662 runs** before this change because the call-identity
  guard was stopping at two and stealing every case. Those runs still fail —
  but they now fail for a real reason instead of a false one, which is the
  precondition for diagnosing them. e2e also spent *fewer* iterations (25.4→
  22.8) for *more* mutations, and its nudges cleared out entirely: `unverified
  edits` 4→0, `repeated edit` 3→0, `edit changed nothing` 2→0.

  *Caveat, stated plainly:* n=5 per arm, so exec-bugfix's 0/5→2/5 is two runs.
  Directionally consistent with the diagnosis and with the stop-reason transfer
  (which is the stronger evidence, being a mechanism and not a count), but not
  established. The ~22%-of-all-runs scale claim in 5.8 still deserves a
  higher-n confirmation before it is treated as banked.

### 5.9 Cline / Roo teardown

Read from each repo's current `main` (Cline is actively developed and mid-rewrite
into `apps/` + `sdk/packages/`; Roo Code, its fork, was **archived read-only
2026-05-15**, so its absence of any post-May feature is not a design choice).

**Fuzzy matching is now a three-way retreat, and locode is the outlier.**
- Cline's shipped default editor (`sdk/.../executors/editor.ts`) does a plain
  exact substring replace with **no fuzzy tier at all**, erroring unless there
  is exactly one match. Its Levenshtein matcher (`apply-patch-parser.ts`, ≥0.66)
  is `enableApplyPatch: false` in **both** the act and plan presets.
- Roo's `MultiSearchReplaceDiffStrategy` keeps a fuzzy path but defaults
  `fuzzyThreshold = 1.0` — **exact only** — with a buffered ±40-line middle-out
  search around the declared start line.
- Aider deleted its fuzzy matcher six days after shipping it (5.7b).

So all three ship exact-by-default while locode runs a 0.8 fuzzy tier. Ours is
human-gated, which is the material difference from Aider's ungated one — but
this now deserves the audit named in 5.7b, and the question is no longer
"should we remove it" but "is the gate airtight on every path, including
headless, where nobody can decline".

- `[x]` **5.9a Show the file on a failed match (build 90).** Roo's diff failure returns the
  ±40-line window of *actual current content* plus the best fuzzy candidate it
  found, and tells the model to re-read. locode's `_not_found_help` explains the
  failure but does not hand back the surrounding text, so the model must spend a
  `read_file` round-trip — or guess again, which is how no-op edits start. This
  is the highest-value item from this teardown after 5.8a, and it composes with
  the "say what already worked" note below.

  **Shipped (build 90).** `_not_found_help` was worse than the entry assumed:
  it showed a **3-line** sliver (`idx-1 … idx+2`), and only when a fuzzy match
  on `old`'s **first line alone** cleared 0.4 — so when the model was most lost
  it got no file content at all. Three changes:
  1. Extracted `_best_block`, the ungated version of the block scan already
     inside `_fuzzy_span`, and pointed the failure message at it. Matching now
     scores `old` as a **block** instead of by its first line, which matters
     whenever that line is generic (`return None`, a bare brace) — pinned by
     `test_best_block_scores_the_whole_block_not_just_the_first_line`, where
     first-line keying anchors on the wrong one of two identical lines.
  2. Widened the window to ±12 lines (`_HELP_WINDOW`), capped at 60
     (`_HELP_MAX_LINES`) so a large `old` can't flood the reply. Kept
     **verbatim and unnumbered** on purpose: the same message tells the model
     not to put line-number prefixes in `old`, so the block has to be
     copy-ready. Roo's ±40 was not adopted — ours is a copy target, theirs a
     reference, and 60 lines of context is already ~600 tokens.
  3. New third branch found while testing: when **nothing** scores above zero,
     `_best_block` returns None and the old code silently showed nothing. That
     is a distinct failure — wrong file, or one already changed — so it now
     says so and tells the model to `read_file`, rather than handing back a
     region that would only mislead.

  Also corrected the confidence wording: below 0.5 it says "No close match" and
  labels the region as merely the most similar, instead of asserting a match it
  doesn't have. 967 passed.

  **Graded — `b90-editwindow`** (base `52f0ed5`, r=5 × qwencoder14 on the three
  cases that actually generate not-found edits: exec-bugfix, e2e-spec-to-code,
  exec-stall-trap). Score INCONCLUSIVE again (−0.0223, p=0.5, 13/15 tied).

  The raw turn-ending table looked *mixed* — exec-bugfix clean-finish 1/5 → 4/5
  but exec-stall-trap 3/5 → 1/5 — and reading it at face value would have been
  wrong in both directions. **What settles it is exposure: did the changed code
  path actually fire in each run?**

  Matching the *triggering condition* (`"not found in"`, which both arms emit)
  rather than the new wording gives the apples-to-apples table:

  | case | exposed base / cand | clean-finish (all) | **clean among EXPOSED** |
  |---|---|---|---|
  | exec-bugfix | 5/5 · 5/5 | 1/5 → 4/5 | **1/5 → 4/5** |
  | exec-stall-trap | 1/5 · 1/5 | 3/5 → 1/5 | **1/1 → 1/1** |
  | e2e-spec-to-code | 1/5 · 0/5 | 0/5 → 0/5 | 0/1 → n/a |

  **exec-stall-trap's "regression" vanishes under exposure: on the runs that
  actually reached the changed code it is 1/1 on both arms, identical.** The
  entire 3/5 → 1/5 swing is among runs that never hit a not-found edit — two of
  its four candidate repeat-stops never called `edit_file` at all, and its
  r1/r2/r5 trajectories are near-identical across arms, forking on a coin-flip
  around iteration 3. The change could not have caused what it never touched.

  exec-bugfix is the real result: **fully exposed on both arms**, so the
  1/5 → 4/5 is measured on a like-for-like subset, and it carries the predicted
  mechanism in its edit counts — 3→2, 5→1, 4→2, 4→2, i.e. **fewer retries
  because the model finally gets copyable text**. That is the causal story, not
  just a better outcome number. e2e is a near-null control (1 exposed run
  total across 10).

  ### ⚠️ RETRACTED (build 92) — the above reads the wrong metric

  **Everything above this line credits a win that did not happen.** Chasing
  why exec-bugfix's score stayed at 0.500 while "clean finishes" went 1/5 → 4/5
  turned up the reason: **`clean finish` was defined as "no `stopped` event",
  which counts the model GIVING UP as success.** A representative
  "clean-finishing" candidate run ends with:

  > *"I cannot make progress based on the information provided. The tool calls
  > have consistently failed, and I do not have enough context to determine the
  > correct course of action. Please provide more details or clarify the issue."*

  Zero landed edits, tests still red. Re-graded on **DONE** (self-terminated
  **and** at least one edit actually landed), the real b90 result is:

  | exec-bugfix (5/5 exposed both arms) | base | cand |
  |---|---|---|
  | DONE | **1/5** | **0/5** |
  | gave up | 0 | **4** |
  | mean landed edits | 0.4 | **0.0** |
  | mean iterations | 13.0 | 7.8 |

  The wide window **caused surrender**. The baseline kept working the problem
  for 13 iterations; the candidate quit after 7 having changed nothing. The
  "fewer edits = more efficient" reading was exactly backwards — it was fewer
  edits because it stopped trying. The variant log rules out my wrong-file
  branch: all five runs saw the plain window message. Best guess is that 60
  lines of code inside an *error* reads to a 14B model as a listing to discuss
  rather than a target to copy from.

  **b88 re-graded on DONE survives**: exec-bugfix 0/5 → 2/5 with **zero**
  surrenders in either arm. That result stands.

  `[x]` **Acted on (build 92).** `_HELP_WINDOW` back to 1 (the prior 3-line
  behaviour). **Kept**, because neither was implicated and both are better
  targeting at any width: block-level location via `_best_block`, and the
  wrong-file branch. The width stays a parameter so re-testing a smaller value
  is a one-line edit — the hypothesis is still live, just unproven at 12.

  ### Two lessons, both about instruments

  1. **`clean finish` was measuring the wrong thing, and had been all along.**
     Fixed in `armstats.py` (build 92): `DONE` requires a landed edit, and
     `gave-up` is now its own column. Any earlier conclusion in this file that
     leans on clean-finish counts is suspect and should be re-derived — the
     b88 and b90 numbers above have been.
  2. **Exposure-filtering (build 91) is still right and still necessary** —
     it correctly killed the phantom exec-stall-trap regression. But it is not
     sufficient: it tells you *which runs* count, not whether the *metric*
     means what you think. I got the right subset and then read a broken number
     over it.

  *Standing rule, earned twice now:* before crediting any A/B win, open one
  winning run and read what the model actually did. Both retractions this
  session came from a summary statistic that looked good.
- `[ ]` **5.9b Checkpoint / undo.** Both have one and locode has none. Note the
  two designs differ and Cline's own docs describe Roo's: Cline actually uses
  `git stash create` inside the real repo, parked at a private
  `refs/cline/checkpoints/...` ref, once per turn, with a rollback-protected
  restore transaction. Roo uses a genuinely separate shadow `.git` with
  `core.worktree` pointed at the workspace, and its restore is a bare
  `clean -fd` + `reset --hard` with no safety stash. If we build this, Cline's
  in-repo-stash-plus-private-ref is the safer of the two and needs no second
  repo to keep in sync.
- `[ ]` **5.9c Grace retry on a no-tool-use reply.** Roo increments its mistake
  counter only on the **second** consecutive turn that produces no tool call
  (`Task.ts:3489`) — the first is a silent retry. Same family of fix as 5.8a:
  do not spend a strike on a single stumble.

Not adopting: Roo's 5-mode system (`architect`/`code`/`ask`/`debug`/
`orchestrator`, tool-group + file-regex bundles) — materially richer than a
plan/act binary but a large permission surface, and locode has no mode concept
to hang it on. Roo's tree-sitter "folded file context" (dedup repeated reads
down to signatures during condensation) is interesting for our context budget
but is a whole subsystem; parked.

Worth stealing cheaply: Cline's plan-mode `command-guard.ts` keeps a
`BLOCKED_COMMANDS` set (rm, mv, dd, chmod, truncate, …) that rejects mutating
shell commands *before* approval rather than trusting the prompt — the same
instinct as SWE-agent's interactive-command blocklist below.

### 5.10 Reading `CLAUDE.md` as a first-class option

Requested 2026-08-06, with the stated worry: *"I worry the local LLM may choke
on Claude specific instructions."* Build 89 deliberately shipped
`instruction_files = ["AGENTS.md", "LOCODE.md"]` and left `CLAUDE.md` out, on
the grounds that silently absorbing another tool's instruction file is the
wrong default. Nothing needs building to *enable* it — adding `"CLAUDE.md"` to
`context.instruction_files` works today, and the opt-in half of
`test_claude_md_is_not_read_by_default` already pins that path. So this item is
about the two things around it: whether the worry is real, and what breaks when
you do turn it on.

**The worry is mostly not borne out — measured, 2026-08-06.** Scanned all six
`*/CLAUDE.md` under `~/Code` for content a local model cannot act on:

| Pattern | Hits across 6 repo `CLAUDE.md` |
|---|---|
| Claude Code tool names (`TodoWrite`, `WebFetch`, `Task`, …) | **0** |
| subagent / delegation | 2 (both in locode's own) |
| slash commands | 1 |
| MCP / hooks | **0** |
| model routing (Opus/Haiku/Sonnet) | 10 (9 in locode's own) |

Repo-level `CLAUDE.md` is overwhelmingly ordinary project prose — build
commands, conventions, architecture. **Zero** Claude-Code tool names in the
whole corpus, which was the specific hazard worth fearing: naming a nonexistent
tool in the system prompt is the classic way to induce hallucinated tool calls,
and our parser is deliberately tolerant enough to try to honor one. The
Claude-specific material that does exist concentrates in the *global*
`~/.claude/CLAUDE.md` (delegation tiers, model routing, the Resend email
workflow) — which locode never reads, because `find_instruction_files` walks
repo-root → cwd and stops. And the single most Claude-flavored file in the
sample is **locode's own `AGENTS.md`, which we already load by default**, with
no observed harm. The concern is legitimate in principle but the corpus does
not show the failure mode.

**What the scan did surface is a real defect, and it is ours.** In **4 of the 6**
repos that have both files, `CLAUDE.md` is a **symlink to `AGENTS.md`**
(gke-custom-compute-class-examples, locode, skills, vsz_zzzz). `context.py`
dedupes nothing — no `realpath`, no `samefile`, no inode check — so adding
`CLAUDE.md` to the list today injects **the identical text twice** and burns
double the 8000-char budget on it. That is a bug regardless of this feature,
since a user can hit it with any two aliased names.

- `[ ]` **5.10a Dedupe instruction files by resolved identity.** Resolve each
  candidate and skip one already loaded. Prerequisite for 5.10b; worth doing
  even if we never enable `CLAUDE.md`.
- `[ ]` **5.10b Make `CLAUDE.md` a documented option, not a list edit.**
  Ship it commented-out in `config.toml.example` with the tradeoff stated in
  place, so it is discoverable without reading `context.py`. Leave the default
  off — the argument for that was never the choke risk, it is that another
  vendor's file should be opted into, and the measurement above doesn't change
  it.
- `[ ]` **5.10c Only then consider defaulting it on**, and only behind the
  standing Milestone 5 rule: a finding lands with a measurement **on our
  corpus** and nothing is credited until it survives an A/B. Note the honest
  obstacle — eval workspaces are `mkdtemp` dirs with no instruction files in
  them, so 5.2 shipped without an A/B and this one cannot get a real number
  until the harness can seed a repo-shaped fixture. That fixture is the actual
  blocker here, not the config line.
- `[ ]` **5.10d Size, not vocabulary, is the likelier failure.** The largest
  `CLAUDE.md` in the sample is 11,397 chars against an 8,000-char budget, so it
  truncates mid-document. Truncating instruction prose at a byte offset can cut
  a sentence in half; if we start ingesting more files, prefer dropping a whole
  file to slicing one, and say which in the marker.

### 5.11 Read before edit — the root cause under 5.8/5.9 (build 93)

`[x]` **5.11a Refuse a content-anchored edit to a file the model has never
read.** Shipped build 93, commit `0748436`. **A/B `b93-readfirst` launched
2026-08-06 against base `5cc7ef2`** (qwencoder14, exec-bugfix +
exec-stall-trap, r=6); result recorded in 5.11c below.

*Scope note:* the sweep was started with `e2e-spec-to-code` included and
restarted without it. That case predominantly **creates** files with
`write_file`, which the gate marks seen and never blocks, so it is the least
exposed case in the corpus — and the slowest, meaning ten long runs would have
elapsed before any signal on the question actually being asked. Run it later
as a regression check (does the gate hurt a case it shouldn't touch?), not as
evidence for the change.

**Why this and not another nudge.** Found while retracting build 90, by doing
the thing the retraction taught: opening the runs and reading them. Across all
**10** exec-bugfix runs of *both* b90 arms, the model landed **at most one**
successful edit and fixed **none** of the three seeded bugs — every run ends
"3 failed". One candidate run went `bash pytest`, `bash pytest`, `edit_file`,
`edit_file` with **no `read_file` at all**, reconstructing `word_wrap` from the
pytest traceback. `old` matched nothing because it had never been copied from
anything.

That reframes builds 87–91. The better failure message (b89), the wider help
window (b90, retracted), the laxer repeat guard (b88) are all *downstream* of a
model editing text it has not seen. They compete to describe a miss more
helpfully; none of them stop the miss. The economics are stark: one `read_file`
costs one iteration, and the guess-loop it replaces cost five to seven and
ended in surrender.

**What shipped.** `edit_file` and `replace_lines` refuse a path not seen this
session and name the fix ("call read_file on it FIRST, then copy `old`
verbatim… text you did not copy is why edits fail to match"). `read_file`
marks a file seen — a truncated or windowed read counts, because the gate is a
floor against editing text the model never saw, not a guarantee it saw the
right part. `write_file` counts (it authored the body); `append_file`
deliberately does not (it knows what it added and nothing about the lines
above). Session-scoped, cleared by `_forget_seen()` on `reset_context`,
`/compact`, auto-compact and `set_history`, so the record can never outlive the
read in the model's context. Off via `agent.require_read_before_edit = false`.

`[ ]` **5.11b The description sentence, deliberately NOT bundled.** Telling
`edit_file` "read the file first" in its own description would *prevent* the
wasted iteration rather than punish it, and is probably additive. It is held
back because tool-description wording has produced large measured swings in
this repo before (the `write_file` size sentence, D44/D49/D51), and shipping
both at once would leave the A/B unable to say which half worked. Run it as its
own arm on top of whatever 5.11a scores.

`[ ]` **5.11c Record the b93 result honestly.** Grade with
`armstats b93-readfirst --by-case --exposure "have NOT read"` — reading **DONE
and gave-up, never clean-finish** — and then *open a winning run and read it*
before crediting anything. Note the exposure string matches the new message, so
it selects candidate-arm runs that hit the gate and reports 0/n on the baseline
by construction; it is a "did the change fire" check, not a paired filter. The
paired comparison is the unfiltered per-case DONE table, because the gate is
reachable in every run that edits at all.

**The plausible way this loses.** The gate converts a *silent* failure into an
*error*, and errors feed `max_consecutive_errors` (4) and the error-stall
streak (3). A model that responds to the refusal by re-issuing the same edit
instead of reading burns the same budget faster and stops sooner — the exact
shape of the b90 regression. If DONE drops while gave-up rises, that is the
mechanism, and the fix is 5.11b (prevent the call) rather than reverting.

`[ ]` **5.11d The gate WORKS and reveals the next failure: the model quotes a
function without its docstring.** Read off `exec-bugfix r1 cand` while the
sweep was still running — the whole point of opening a run instead of waiting
for the table.

The gate did its job exactly as designed. The model opened with the same blind
`edit_file` the baseline used; it was refused, it called `read_file`, and it
then quoted the *real* code back. Compare what it sent as `old` against the
file:

```
old (19 lines)                    textkit.py
  def word_wrap(text, width):       8  def word_wrap(text, width):
                                    9      """Wrap `text` on word boundaries …
                                   10
                                   11-15  … five more lines of contract …
                                   16      """
      words = text.split()         17      words = text.split()
      lines = []                   18      lines = []
      current = []                 19      current = []
```

**Every single line of `old` is in the file.** The model copied accurately and
dropped the 8-line docstring between the signature and the body, so the quote
is not *contiguous* and exact match cannot fire. It is not hallucinating here —
it is eliding, the way a person quoting a function in prose would.

Why fuzzy did not rescue it, and why that is correct: the best-scoring region
is the **body alone** starting at line 17 (18 of 19 lines align, ratio
**0.962**), i.e. the winner is shifted off the signature. The runner-up is that
same region shifted one line further, ratio **0.9605** — a gap of **0.0012**
against the 0.05 ambiguity gate, so `_fuzzy_span` refuses. Accepting would have
replaced the body while `new` re-supplied the `def` line, duplicating the
signature. The ambiguity gate earned its keep. (Note for anyone tempted to
loosen it: on a long span the runner-up is *always* the winner shifted by one
line, so the gap is structurally tiny. That is a property of a sliding window,
not evidence the gate is too strict.)

What the model actually received for all of this was `` `old` not found in
…/textkit.py (84 lines) `` plus the closest-region snippet. Correct, and
useless — it does not name the mistake.

Two levers fall out, in priority order:
1. **Detect the elision and say so.** When every line of `old` appears in the
   file *in order* but the matched region is longer, the diagnosis is exact:
   "your `old` skips lines that are really in the file (probably a docstring or
   comment). `old` must be a CONTIGUOUS verbatim block." Cheap to compute
   (`difflib.SequenceMatcher` opcodes on the located region already give the
   inserted lines) and it names the fix.
2. **Ask for a smaller `old`.** The model quoted an entire 19-line function to
   change two of its lines. A minimal `old` never spans a docstring and cannot
   drift. This is a description change and therefore belongs with 5.11b, as its
   own arm.

The other two edits in that run are *not* this failure and should not be
lumped in: `truncate` scored 0.642 with 2 lines genuinely absent, and
`title_case` scored 0.42 — a real hallucination of the body, after reading it.
So the corpus has at least three distinct edit-miss mechanisms and only one is
addressed by lever 1. Quantify the mix across the finished sweep before
building anything.

#### 5.11c result — the gate is unproven, not proven (graded 2026-08-06)

`armstats b93-readfirst --by-case --exposure "have NOT read"`, reading **DONE
and gave-up**, over 6 pairs per case:

| case | arm | DONE | gave-up | mean iters | mean landed edits |
|---|---|---|---|---|---|
| exec-bugfix | base | 1/6 | 0 | 10.5 | 1.5 |
| exec-bugfix | cand | 0/6 | 0 | 10.5 | 1.3 |
| exec-stall-trap | base | 0/6 | 6 | 5.0 | 0.0 |
| exec-stall-trap | cand | 0/6 | 6 | 5.0 | 0.0 |

`ab.py`: 12 pairs, W1/L2/T9, mean delta −0.0417, p=0.75, INCONCLUSIVE.

**`exposed = 0/6`. The gate never fired once in this sweep.** No candidate run
contains "have NOT read": in every run that got as far as editing, the model
called `read_file` first on its own. The exec-stall-trap columns are
byte-identical across arms, which is what a genuine no-op looks like.

So: **neither confirmed nor refuted.** The 1/6 → 0/6 is a single run of noise
on a change that did not execute. Do not cite this sweep as evidence in either
direction. The gate stays in (it is correct-by-construction, costs nothing when
the model already reads, is behind `require_read_before_edit`, and *did* do its
job in the void sweep) but it is **not** credited with an improvement, and
5.11's premise — that blind editing is the root cause under 5.8/5.9 — is
downgraded: on this corpus the model mostly reads first already.

The sweep's real yield is 5.13, which is an order of magnitude larger.

### 5.13 The parser, not the editor, was killing the runs (build 94) ✅

Mining the same 24 runs for *why* turns ended:

```
total "missing a name" nudges : 16
runs that DIED unparseable    : 8 of 24  (4 base + 4 cand, all exec-bugfix)
unnamed fenced-JSON objects, by top-level key set:
    24  ('tasks',)
```

**Two thirds of exec-bugfix runs in both arms died at iteration 4**, after a
single `bash` call, on this — the same 983 bytes emitted three times:

````
```json
{
  "tasks": [
    "[x] Run initial pytest to identify failing tests",
    "[>] Fix the identified bugs in textkit.py",
    "[ ] Re-run pytest to verify all tests pass"
  ]
}
```
````

An `update_plan` call with no `name` field. The parser said "tool object
missing a name", the model — which had no reason to read that as being about a
field it had never emitted — reproduced its output verbatim, and the repeat
guard ended the turn. Nothing to do with edit matching. It dwarfs every effect
measured in 5.11, and it was invisible until the endings were mined, because
`clean-finish` and score deltas both count a dead turn as an ending like any
other.

`tasks` is a key no other tool has. **Shipped in build 94:** a nameless fenced
object resolves to the tool that both accepts every key present and has all of
its required arguments — *if exactly one tool qualifies*. `Registry.signatures()`
supplies the key sets. Deliberate refusals: `{"path"}` (read_file or ls),
`{"path","content"}` (write_file or append_file), any object with a key no tool
declares, and bare JSON in prose outside a tool fence — data far more often
than a call there. A wrong inference *runs the wrong tool*, so unique-or-nothing
is the whole design. The fallback nudge now names the missing field.

Verified against the exact reply that killed those runs: unparseable before,
`update_plan` after. 998 tests pass.

**Kept on the mechanism, not on the A/B.** The `b94-infername` sweep read
`+0.375, p=0.031, ✅ IMPROVED` and that verdict does not survive calibration —
see 5.14. What justifies build 94 is narrower and does not need a sweep: the
reply that ended those turns is in the archive, it parses correctly now and did
not before, and in the one pair where the baseline actually died on it (r4) the
candidate did not. Everything past that is unmeasured.

Open follow-ons:
- **a.** History still records the model's nameless block verbatim, so it keeps
  emitting the malformed shape (which now works). Rendering the *normalized*
  call into history would teach the format instead of accepting it forever.
- **b.** Inference is off in `salvage_truncated_write` and in tier-3 prose
  salvage. Revisit only with evidence from a sweep, not on principle.
- **c.** ✅ done below.

#### 5.13c Mining every ending in the archive (771 runs, 2026-08-07)

Same treatment applied to all `evals/results/*/events/*.jsonl`:

```
310  the model repeated the same tool call without making progress
 12  edits kept hitting the same error without making progress
  8  the model kept emitting unparseable tool calls     <- 5.13, all in b93
  5  budget: max iterations reached
  5  the tools this task needs are not available
  5  the model repeated the same reply without making progress
 ~20 budget: wallclock (mostly the void sweep)
```

**Repeat-stop is 85% of all endings.** "Repeating and stalling out is the norm"
is not an impression, it is the shape of the entire corpus. What is being
repeated at the moment of death:

```
128 bash    95 edit_file    36 read_file    27 update_plan
 16 replace_lines     7 write_file     1 ls
```

Two questions worth asking of that, and the answers point *away* from the
guard, which is why this is written up as a negative result:

**Is it firing on legitimate re-verification?** 113 of the 128 bash cases had a
mutating tool succeed *between* the identical calls — the model edited, then
re-ran `pytest -q`, which is the correct loop and looks identical every time by
construction. But comparing the outputs kills the theory: the dominant shape is
"2 distinct outputs of 3 identical calls" (60 runs) — the output moved once and
then stopped moving. The model edited and the test result did not change. The
guard is firing on a **real** stall, one iteration after the evidence arrives.
Do not loosen it.

**Is it killing finished work?** 17 runs were stopped as "repeating" when the
last test run was fully green — task complete, reported as failure. Real bug,
and **already fixed**: last occurrence is `b83`, and b87/b88/b90/b93/b94 have
zero between them (the build-88 verify-after-change work closed it). Recorded
so nobody re-fixes it; `green-when-killed` is a column worth keeping in any
future mining pass as a regression check.

So the residual 278 are genuine: **edit → still red → same edit again.** The
lever is not the repeat guard and not the parser. It is what the model is told
when an edit lands and the error does not move — currently a bare "error
unchanged across edits" nudge (74 in the archive) that evidently does not
redirect it. That is the next thing to design, and it wants the 5.11d elision
diagnostic under it, since a `old`-not-found miss and a landed-but-useless edit
are different situations that currently read the same to the model.

### 5.14 The A/B harness had no noise floor, and nearly sold a false win ✅

The most important result of 2026-08-07, and it is about the instrument.

`b94-infername` (build 93 vs build 94, exec-bugfix, qwencoder14, 8 pairs) read:

```
exec-bugfix   n=8   base 0.438   cand 0.812   delta +0.375
pairs: 8 (W6/L0/T2) — 6 informative · sign-flip p 0.0312
✅ IMPROVED — the candidate beat the baseline
```

Every gate this repo had was satisfied: enough pairs, alternating arm order,
paired statistic, p under alpha, a clean 6-0. The exposure check is what
started the unravelling — **only 1 of the 6 winning pairs had a baseline run
that actually hit the parser bug.** Reading the r1 pair confirmed it: both arms
issue the same first three calls, then diverge on sampling, and neither ever
emits a nameless tool call.

So: run the same sweep with **identical code in both arms**.

| sweep | arms | delta | pairs |
|---|---|---|---|
| `b94-infername` | build 93 vs 94 | **+0.375** | W6/L0/T2, p=0.031 |
| `b94-AA-noisefloor` | **94 vs 94** | **+0.281** | W5/L0/T3 |
| `b94-AA2` | **94 vs 94** | +0.062 | W2/L2/T4 |

Five independent n=8 samples of the *same build* (the b94 candidate arm plus
both arms of both A/A runs) scored **0.812, 0.438, 0.719, 0.719, 0.781** —
spread **0.375**, sd 0.149. The measured delta is exactly the spread identical
code produces, and build 93's lone sample (0.438) is the minimum of build 94's
own range.

Two things were briefly suspected and are ruled out, recorded so they are not
re-suspected: **arm ordering** (it alternates, and the candidate won from both
positions in A/A #1) and **an environmental handicap on the worktree arm**
(A/A #2 came back W2/L2 symmetric; `_agent_launcher.py` verifies the import
root; nothing in `locode` reads its own source tree at runtime). There is no
bias. There is just a noise floor nobody had ever measured, and A/A #1 drew a
1-in-32 hand from it.

Why the p-value did not protect us: the sign-flip test is correct about the
signs, but the per-run score is coarse (0 / 0.25 / 0.5 / 1.0) and heavy-tailed
on this case, so a handful of pairs flipping together is far likelier than the
nominal 2/2ⁿ suggests. **Only an A/A run knows that.**

**Shipped in `ab.py`:**
- `evals/noise_floor.json`, checked in, keyed by `cases|models|repeat`. Any
  sweep whose arms are byte-identical is auto-detected, reports no verdict
  (it *is* the floor, by construction), and banks its |delta|.
- A verdict of improved/regressed must now clear `floor × (1 + 2/k)` for `k`
  calibration runs — 2× at k=2, 1.33× at k=6, approaching the floor as
  evidence accumulates. The multiplier is a judgement call; that a thin
  calibration must buy a bigger effect is not.
- **An uncalibrated setup cannot report a win at all.** It returns
  inconclusive and names the exact command to run. This is the part that would
  have caught tonight.
- 11 tests in `tests/test_ab.py`, including the literal +0.375-against-0.281
  case. Re-reading `b94-infername` through the gate now yields INCONCLUSIVE.

**Standing rule, added to the methodology list: calibrate before crediting.**
An A/B without an A/A for its setup is not evidence. Every "IMPROVED" verdict
in this ROADMAP predates the floor and should be re-read with that in mind
before being built on — none of them were calibrated.

### 5.15 The syntax guard was sending models to fix text that was correct (build 95) ✅

Mining the 818-run archive for *how the turn ended* — the same method that
found 5.13 — the repeat-stop deaths in the current architecture (b87 onward,
167 runs, 68 repeat-stopped) break down as:

| deaths | shape |
|---|---|
| 16 | `bash` repeated, command errored |
| 14 | `read_file` re-read after edits |
| **12** | **`edit` refused by our own syntax guard, then resent identically** |
| 9 | `edit` — `old` not found |
| 8 | `edit` landed, check still red |
| 6 | `read_file` re-read, never edited |
| 2 | `bash` repeated, tests red |

**Correction to a claim first made here and retracted the same session.** I
initially read that table as showing the no-op edit (`new` identical to `old`)
had been closed by build 87, because it no longer appears as a *terminal*
signature. That was a classification artifact, not a fact: the table keys each
run by the call it was repeating when it died, so a failure that fires
repeatedly mid-run and then hands the kill to some other call vanishes from it.
Counting **occurrences** instead of deaths, over the same 168 b87+ runs:

| events | edit failure |
|---|---|
| **187** | **`old` not found** |
| 67 | no-op — `new` identical to `old` (43 runs, 26%) |
| ~90 | syntax-guard rejection |

More than one `old`-not-found per run. And the no-op is not fading: the three
b94 sweeps hit it in 39 of 48 runs (**81%**), far above b88/b90's ~10%, almost
certainly because b94 runs survive past iteration 4 now and simply get more
chances to reach it. The user's report of seeing four in one live session is
consistent with these numbers, and my "build 87 closed it" was wrong.

What build 87 *did* close is narrower and still true: a no-op edit is no longer
the call a run dies repeating. `bash: SILENT-OK` (re-running `py_compile` after
it succeeded silently) is genuinely absent after b87 — build 88 closed that one,
and it is a regression column, not a task.

**So the ranking of edit-landing work is: `old` not found (dominant), then the
no-op, then the guard's misdiagnosis below.** The guard is what this build
fixes because it is the one where locode is actively lying to the model; the
other two are larger and are next.

**The finding.** Every guard rejection ended with the same sentence: *"Your
`new` text is malformed — most often an unmatched bracket or paren, or a broken
indent."* It is not always true. Locating the supplied span in each archived
rejection: **31 of the 58 knowable cases broke OUTSIDE the supplied text**, and
in every one of those the error was at exactly the first line after it.

`b87-noop-redact/exec-bugfix r4` is the canonical shape. The model issued
`replace_lines 8-28` with a complete, correct, 13-line `word_wrap`. Its text
was valid Python. But the block being replaced ran past line 20, so its tail
was stranded and the candidate file read `unexpected indent` at line 21. We
told the model its function was malformed. It re-read the file, looked at the
function, correctly concluded nothing was wrong with it, and sent the identical
edit again — twice — until the repeat guard ended the turn at 155s.

The model behaved correctly at every step. locode misdiagnosed, and the repeat
guard then punished the model for trusting us.

**Shipped (build 95, commit b04ac6d, 1021 tests).** `_changed_span` recovers the
supplied lines from the common prefix/suffix of before-vs-after, which works for
`edit_file` and `replace_lines` alike without threading their different
arguments into the guard. When the error line falls outside that span the
message now says the error is *not* in the supplied text, gives the line it is
actually on, states that the replaced region ended mid-block, renders the
junction with both ends marked, names the apparent extent of the leftover tail,
and says plainly that resending the same text will not fix it. When the break
genuinely is inside the supplied text, the old message is correct and unchanged.

Measurement pending — `b95-seam`, exec-bugfix, 8 pairs. Per 5.14 this setup has
a noise floor (k=2, floor 0.281), so the score delta must clear 0.563 to claim
anything; the load-bearing read will be `armstats --exposure "would introduce a
SyntaxError"`, which is the triggering condition both arms emit.

### 5.16 Why `old` misses — the dominant edit failure, classified (build 96 target)

187 `old`-not-found events over 168 b87+ runs. Reconstructing the file each
model was looking at from the last `read_file` in the same run and re-testing
its `old` against it:

| events | why the match failed |
|---|---|
| 84 | single-line `old`, absent from the file entirely |
| 47 | the model never read the file in this run — nothing to compare |
| 28 | **every line present, in order, but not contiguous** (elision) |
| 28 | partial / mostly absent multi-line blocks |
| 0 | whitespace-only differences |

**Whitespace is not the problem.** Zero of 187 were leading/trailing, trailing
per line, or interior indent-width mismatches. Any fuzzy-normalization tier
aimed at whitespace would have bought nothing here, which is worth knowing
before building one.

The 28 elisions are the model writing `old` the way a person summarizes code —
every line real and in the right order, with the boring middle dropped. That is
ROADMAP 5.11d lever 1 and it is now sized: 15% of misses.

**Two concrete defects found while reading the current message.** Replaying the
archived b87 elision through today's `_not_found_help` (not b87's — the message
has improved a lot since; it now returns 15 lines of real content where b87
returned 2):

1. **The advice text is jammed onto the last line of the block.** The message
   says "Copy your `old` out of it verbatim:" and then ends
   `…    current = [word] If the target text is hard to reproduce EXACTLY — …`.
   `_TRY_REPLACE_LINES` is concatenated with a leading space and no newline. A
   model doing exactly what it was told can copy that sentence into `old`. This
   is a one-character fix and it is actively harmful today.
2. **The window can exclude the line the model anchored on.** The located block
   was lines 16-30; the model's `old` began at `def word_wrap(text, width):` on
   line 8. It was shown a slice starting mid-docstring and ending mid-block, so
   it still could not copy an `old` that begins where it intended. The window
   should cover the model's first `old` line when that line exists in the file.

Neither is measured yet. Defect 1 needs no measurement — it is a formatting
bug in text we instruct the model to copy verbatim.

### 5.17 The model authors `old` instead of quoting it — one cause under both top failures

The 5.16 table says 84 misses were "single-line `old`, absent from the file".
That describes the symptom. The cause is measurable and it is unambiguous.

For every single-line miss in the b87+ corpus where the file had been read
(**87 cases**), compare the model's `old` against (a) the most similar real line
in the file and (b) the model's own `new` in the same call:

| | median similarity to `old` |
|---|---|
| best matching line actually in the file | 0.67 |
| **the model's own `new` in the same call** | **0.97** |

**87 of 87** — every single one — had `old` closer to its own `new` than to
anything in the file. Zero exceptions.

The model is not mis-copying the file. It is **writing its intended replacement
into `old` as well as `new`**, then tweaking one of them. From `b94`:

```
sent as `old`:  current = [word] if current_len + len(word) < width else [word]
actually there: elif current_len + 1 + len(word) < width:
```

and across successive calls in the same run the model iterates `< width`,
`<= width`, `+ 1 <= width` — refining the code it wants while still using that
invented text as the search key. It never matches, so nothing ever lands.

**This unifies the two largest edit failures.** The no-op edit (`new` identical
to `old`, 67 events, 81% of b94 runs) is the *degenerate case of the same bug*:
when the tweak between the two fields happens to be empty, the search key does
match, and the edit changes nothing. Same misconception, two different error
messages, ~154 events between them. They have been counted and chased
separately for six builds.

The current messages both address the surface. "Copy the target text EXACTLY as
it appears in the file" is true but generic, and the model believes it is doing
that. Nothing tells it the specific thing that is wrong: that `old` is a search
key describing the present, not a draft of the future.

**Build 96 target.** When an edit fails to match and `old` is highly similar to
`new`, name that directly — the two fields are ~N% identical, so the
replacement was written into both; `old` must be text already in the file,
copied character for character; here is the line you appear to be aiming at,
verbatim; put that in `old` and your version in `new`. The same signal is
available on the no-op path, where similarity is 1.00 by definition.

Ranking note: this supersedes the elision work (5.11d lever 1, 28 events) and
the window fix in 5.16 as the highest-value editing lever. The 5.16 defect 1
(advice text concatenated onto the copy-me block) still ships regardless — it
is a formatting bug, not a hypothesis.

### 5.18 `replace_lines` makes the model supply indentation that `edit_file` supplies for it

Found while watching the `b95-seam` sweep: every guard rejection in it took the
"your `new` is malformed" branch, correctly. The model had sent

```
replace_lines path=textkit.py start=23 end=25
new: current = [word]
     current_len = len(word)
     elif current_len + len(word) < width:
```

with all three lines flush at **column 0**, replacing lines that live two levels
deep inside a function. Python reports `expected an indented block` at line 23,
which is the first line of the supplied text, so build 95 correctly declines to
blame the seam. The message is accurate and the model still cannot act on it.

Sizing it over the b87+ corpus, `replace_lines` guard rejections only:

| events | shape |
|---|---|
| 14 | indentation error **on the first supplied line**, `new` starting at column 0 |
| 16 | indentation error elsewhere in the supplied text |
| 22 | other syntax error |

27% of `replace_lines` rejections are this one thing: the model writes the block
with its own *relative* indentation and expects the tool to place it.

**That expectation is not unreasonable — it is what our other editing tool
does.** `edit_file` preserves each matched line's original indentation (it says
so in its own no-op message). `replace_lines` demands absolute indentation and
gives no hint that it differs. The model is being punished for a consistency gap
in our API.

**Target.** On a `replace_lines` rejection, if re-indenting every line of `new`
by the indentation of the first line being replaced makes the file parse, apply
that and say plainly what was done and by how much. If it still does not parse,
fall back to the current message plus the specific observation that `new`
started at column 0 while the replaced region is indented. Auto-reindent is not
a new liberty here; it is `edit_file`'s existing contract extended to the tool
that lacks it.

Ranked after 5.17 (154 events) but ahead of the elision work (28).

### 5.12 A dead serving thread is invisible to us for ten minutes a run

Found the hard way on 2026-08-06: the first `b93-readfirst` sweep produced
**22 ungraded runs out of 24**, all tagged `infrastructure`, each having burned
the full 600 s wallclock. Archived at
`evals/results/b93-readfirst-VOID-serverdead/`; it is not evidence of anything
and must not be graded.

Cause, from `~/.local/state/locode/mlx-server.log`:

```
Exception in thread Thread-1 (_generate):
  File ".../mlx_lm/server.py", line 891, in _generate
    r.logprobs[r.token].item(),
ValueError: Slice indices must be 32-bit integers.
Exception ignored in: <function BatchGenerator.__del__>
  RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory
```

An upstream `mlx_lm` bug killed the **generating** thread at 22:21:17. The OOM
underneath it is the *cleanup* failing after the fact, not the trigger. The
HTTP thread survived, so `/v1/models` kept answering **200** for the next four
hours while every `POST /v1/chat/completions` was accepted and never served.
Each locode run therefore sat at `assistant_start` until the wallclock killed
it: `{"phase": "assistant_end", "chars": 0}` at `t=600.08`, then
`{"phase": "error", "text": ""}`.

**Three defects, all ours, in increasing order of how much they cost:**

- `[ ]` **5.12a Readiness is measured on the wrong thread.** `/v1/models` is
  served by the HTTP thread and stays green after the generate thread dies. A
  real probe has to be a one-token completion. Build 77's `arch_supported()`
  preflight catches a model that *cannot load*; nothing catches a server that
  loaded fine and later lost its worker.
- `[ ]` **5.12b The build-77 watchdog cannot see a server that was ALREADY
  dead.** It races the wait for response headers against new bytes in
  mlx-server.log, attributed **by byte offset from the start of the request** —
  deliberately, so a previous run's traceback can't be blamed on this one. That
  is right for the case it was built for and exactly wrong here: the fatal
  traceback predates every one of the 22 requests, so the watchdog had nothing
  to match. Wanted: when a request has produced **zero bytes** for N seconds,
  widen the scan to the whole log since server start and name what it finds.
  Note the tension with the byte-offset rule — the fix is a *second, later*
  check with a different scope, not loosening the first.
- `[ ]` **5.12c `{"phase": "error", "text": ""}`.** The run's entire diagnosis
  was an empty string; `stdout/*.txt` contains the literal five characters
  `[error]`. Whatever else changes, an infrastructure failure must say what
  happened.
- `[ ]` **5.12d `evals/ab.py` should abort a sweep that is producing nothing.**
  It ground through 22 consecutive ungraded runs — **~3.7 hours** — before
  reporting. k consecutive ungraded runs (k≈3) should stop the sweep and say
  why. This is the cheapest fix here and would have saved the whole night.

**Self-inflicted footnote, recorded so it isn't repeated.** The relaunch was
then killed within seconds by a monitor *I* wrote to watch for this very
traceback: it grepped `tail -400` of the log, matched the **stale** exception,
and shot the sweep it was protecting. Anchor a log watch to the byte offset
captured at launch — the same discipline build 77 already applies inside
locode, which is a little pointed.

### Worth stealing, not yet scheduled

- **Say what already worked.** On a partial failure Aider names the blocks that
  succeeded and instructs "Don't re-send them." locode says nothing about what
  landed, so a model re-deriving a batch can undo its own good work.
- **Window the file viewer.** SWE-agent swept this and it is non-linear in both
  directions: 30 lines 14.3%, **100 lines 18.0%**, 400 lines 17.0%, **whole file
  12.7%** — full-file dumps were the *worst* viewer variant they tested.
  locode's `read_file` returns the whole file by default. Tempting, but this is
  external evidence on a 2024 model and a default change touching every flow —
  it needs its own A/B before we believe it here.
- **Consolidate actions, don't fragment them.** SWE-agent's search ablation:
  one action returning all results 18.0% vs. iterative next/prev 12.0% (−6.0).
  Argues against ever splitting a locode tool into finer steps.
- **Blocklist bare interactive commands.** SWE-agent blocks `python`, `bash`,
  `vim`, `less` etc. as *exact* matches so the model cannot strand itself in a
  REPL. locode's bash tool has no such guard.

---

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
