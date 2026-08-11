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

**Audit answered, 2026-08-07 (build 96): it is not.** Found by accident — two
build-96 tests asserted `is_error` on an unmatched `old` and instead got
`edited …, fuzzy ~86%`. `try_edit` labels tier 3 "fuzzy (human-gated)" at
`fs.py:168`, but it returns plain `status="ok"` and `EditFile.run` writes the
file. There is no gate *in the tool*. The only gate is the generic
edit_file approval prompt — precisely what `--allow-tool edit_file` removes.
So every eval run we have ever scored, and any headless user session, applies
0.8-similarity fuzzy edits silently.

That lands badly next to 5.17. The model *authors* `old` rather than quoting it
(87 of 87), so the fuzzy tier is resolving invented search keys against real
code whenever the invention happens to land within 0.8 of a line. When it
misses, the model at least gets told. When it hits, we silently write `new`
over a region the model never actually identified, and report success. This is
the one edit path that can corrupt a file while looking green — see 5.19.

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

#### 5.13d The residual is smaller than 5.13c claims — do not build the fix

Went to specify that lever and measured it first. Three hypotheses, all dead:

1. *"The stall nudge rarely fires."* It fires in 26 of the 65 runs my scan
   called a stall — 40%. Suspicious, so:
2. *"`error_sig` keys on exact content, and pytest's duration line resets the
   streak."* Wrong. Of 471 consecutive failing-bash pairs, **40% are
   byte-identical** — matching the nudge rate almost exactly — and **zero**
   become identical only after normalising timings, temp paths, or addresses.
   The signature is not being defeated by volatile output.
3. *"Then the failure identity is stable while the bytes move."* Also wrong. Of
   the 281 byte-different pairs, **2** share a failure identity. 86 are plainly
   different failures and the rest have no extractable identity.

A representative "different" pair:

```
- E   ImportError: cannot import name 'title_case' from 'textkit'
+ E   ImportError: cannot import name 'dedupe_spaces' from 'textkit'
```

That is *progress* — one function fixed, the next one up. My stall scan counted
it as a repeat because it keyed on the first `\w*Error` token within 60
characters, which collapses every `ImportError:` into one bucket.

**So the same-error stall detector is calibrated about right**, and the "278
genuine edit → still red → same edit again" figure above is inflated by the
same coarse-signature error. `max_error_stall = 3` on byte-identical output is
firing when it should and staying quiet when the error is genuinely moving.

Do not build the signature-normalisation fix. What remains worth asking is
narrower: when the error *is* byte-identical three times, the nudge fires a
median of 5 tool calls after the stall began — but that number came from the
same bad scan and needs re-deriving before anyone acts on it.

Third measurement today to refute a lever I had reasoned my way into from
reading code (with 5.19a and the whitespace tier). Recording the pattern, not
just the result: **mechanism justifies a fix, frequency justifies its rank, and
only measurement supplies frequency.**

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

#### 5.15a result — UNPROVEN, exposure zero

`b95-seam`, exec-bugfix, qwencoder14, 8 pairs, base `ceb42aa`:

```
base 0.625   cand 0.656   delta +0.031
pairs W3/L2/T3 — 5 informative · sign-flip p = 1.0
noise floor +0.281 (k=2)          →  INCONCLUSIVE
```

The score is a null result, and the reason is not subtle. **The seam branch
fired zero times, in either arm.** Five guard rejections happened across the 16
runs (4 base, 1 cand) and every one of them was a genuine column-0 dedent, where
build 95 deliberately keeps the old message. The changed code never executed.

This is `b93` again, and the same discipline applies: the sweep cannot credit
the change and cannot blame it. What justifies build 95 remains the archived
evidence — 31 of 58 rejections broke outside the supplied text, and the message
we sent about them was false.

**Two numbers in that table I am explicitly not crediting**, recorded here
because they are exactly the shape of the b94 mistake: the candidate arm landed
**3.9 edits per run against the baseline's 1.4**, and verified 3/8 against 2/8.
With zero exposure there is no mechanism by which build 95 could have caused
either. Exposure was also asymmetric (4/8 base vs 1/8 cand), which makes the
arms non-comparable on anything downstream of a guard rejection. Divergence,
not effect.

The eval case simply does not produce seam breaks often enough to measure this
way. Testing it needs a case built to strand a block — noted, not built.

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

#### 5.16a Defect 2 is not worth fixing — the snippet converts nothing

Measured before building it, and the answer closes the whole line of work.

For every b87+ not-found event where the reply **did** show real file content
("this is what the file ACTUALLY contains there") and the model's very next
call was another edit:

```
64 events, across 39 distinct runs (max 3 from any one run)
edits that landed: 0
```

**Zero for sixty-four.** Handing a weak model the exact text and telling it to
copy that text produces a landed edit essentially never. For contrast, the
*other* branch — "NOTHING in this file resembles `old`", which shows no content
at all and tells the model to re-read — is followed by a landing edit 11 times
in 35 (31%).

**Correction, same session — the snippet is not the variable.** The 64/35 split
above and the edit_file/replace_lines split of the same 99 events are both
64/35, which is a coincidence I initially read as one effect. Cross-tabulating
them separates it:

| shown a snippet? | next tool | landed | failed | |
|---|---|---:|---:|---|
| yes | `edit_file` | 0 | 48 | 0% |
| yes | `replace_lines` | 0 | 16 | 0% |
| no | `edit_file` | 0 | 16 | 0% |
| no | `replace_lines` | **11** | 8 | **57%** |

The real finding is sharper and it is about the tool, not the snippet:
**retrying `edit_file` after an `old`-not-found landed 0 times out of 64, in
every cell.** Whether we showed the file's text made no difference to it. The
one cell that ever recovers is `replace_lines` on the wrong-file branch, and
that is confounded — "nothing here resembles `old`" often means an earlier edit
already changed the file, where line numbers still work and text no longer
does.

So the honest claims are: widening the window is not supported (0/64 with the
snippet, at two widths, plus build 90's measured harm), and **an `edit_file`
retry immediately after a miss is worthless.** The second is the more
actionable of the two and was not visible before this cross-tab.

Caveat kept deliberately: this counts only the *immediately following* call.
Runs do recover later, so "0/64" is not "these runs all failed" — it is "the
next thing the model does is never the fix".

This retires defect 2, and it retires the window as a lever generally — which
also explains build 90, where a *wider* window measured actively harmful (0 →
4 surrenders out of 5). Both results say the same thing: the snippet is not
being read as a source to copy from.

**And it is the strongest evidence yet for 5.17.** If the model were trying to
quote and failing, more visible text would help; it demonstrably does not. The
model is not copying at all — it is composing `old` from intent, which is
exactly what 87 of 87 says. Build 96 bets on naming that misconception in
words, because showing has been tried, at two widths, and does not work.

That bet is now the thing under test in `b96-authored-old`, and this raises its
stakes honestly: if words fail here too, the next move is structural (a tool
that does not ask the model to reproduce text at all — line numbers, or an
`old` we supply for confirmation) rather than another wording pass.

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

#### 5.17a Shipped — build 96

Three changes in `locode/tools/fs.py`:

1. `_quoted_fraction(old, text)` — the share of `old`'s non-blank lines that
   appear verbatim in the file. This is what separates an *invented* `old`
   (scores 0) from an *elided* one (scores 1.0, every line real but the middle
   dropped). The two need opposite advice and today get the same message.
2. `_authored_old_note(old, new, text)` — fires only when `old` quotes nothing
   real **and** is ≥0.75 similar to `new`, and leads the not-found reply with
   the diagnosis: your two fields are N% identical, no line of `old` is in the
   file, so you wrote the code you want into both; `old` is the search key, the
   text in the file RIGHT NOW.
3. The no-op message names the same misconception, since that path *is* the
   degenerate case.

Plus 5.16 defect 1: `_TRY_REPLACE_LINES` now leads with a newline instead of a
space, so it stops fusing onto the last line of the block we just told the
model to copy verbatim.

**Validated against the archive before shipping**, which is what the threshold
rests on: fires on all 88 nothing-quoted cases, silent on all 28 elisions and
all 27 partly-quoted. One nothing-quoted case sat at 0.78, so the cut is 0.75
rather than 0.80. A false positive costs nothing — the rest of the message is
unchanged — and no successful edit reaches this path.

10 tests (1031 total, green). Measurement not yet attempted; per 5.15a the
exec-bugfix case is a poor instrument for a branch that fires rarely, and
*this* branch should fire often — `old`-not-found is 187 events across 168
runs. That makes it the first change in a while worth A/B-ing on exposure
grounds. Not yet run.

**5.17b — the b96 A/B: INCONCLUSIVE, and do not read it as a win.**
`b96-authored-old`, exec-bugfix, qwencoder14, r8 (16 runs). Score delta
**−0.125** (base 0.656, cand 0.531), W1/L3/T4 → 4 informative pairs, sign-flip
p=0.625, and the calibrated noise floor for this exact setup is ±0.281. Four
informative pairs cannot reach p<0.05 under any arrangement (floor 2/2⁴), so
the sign here carries no information either way.

The endings (armstats, the measure that matters) lean *against* the candidate:
VERIFIED 3/8 → 1/8, self-terminated-gave-up 3 → 0, stopped-by-repeat-guard
1 → 6, iterations 14.2 → 21.0, edits landed 2.4 → 3.6.

What is *not* ambiguous is exposure: **the branch fired, 7 of 7 cand not-found
events carried the note** — the first real exposure in three builds (b93 and
b95 were both 0%). Exposure was asymmetric though (7/8 cand runs vs 4/8 base),
so even the ending counts are comparing unlike populations.

Reading a trajectory (`r2__cand`, per methodology rule 3) explains the shape and
is the useful output of this sweep. The note fired at 62.8 s and the model
corrected *immediately* — it quoted a real file line and landed three edits
(93.6 s, 99.7 s, 105.6 s). The run then died on a **different** failure: the now
genuinely-quoted `old` matched 2 places, the ambiguous-match error came back,
and the model resent it byte-identical twice before flailing into the repeat
guard. The per-arm counts say the same thing: ambiguous-match events base 8 →
cand 20, edits landed 17 → 26. An ambiguous `old` is *by construction* real file
text, so build 96 moved the model from inventing `old` to quoting it, roughly
2.5×, exactly as designed — and the higher iteration/stopped counts are what
happens when a run that used to die early now survives to the next wall.

Verdict: mechanism confirmed, score unproven, next bottleneck identified (5.20).
Do not cite the −0.125 in either direction; do not re-run this case expecting
resolution — 8 pairs cannot resolve a 0.281 floor.

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

### 5.19 The fuzzy tier's "human gate" does not exist on the path that matters

Found while writing the build-96 tests (full account in the competitive-analysis
section above, under 5.7b). `try_edit` comments tier 3 as "fuzzy (human-gated)"
and then returns `status="ok"`; `EditFile.run` writes the file. The gate is the
ordinary edit_file approval prompt, so it is present interactively and **absent
in every headless run** — including every eval sweep we have scored.

Why this is worse than it looks in isolation: 5.17 establishes that the model
composes `old` rather than copying it. A composed `old` that happens to score
≥0.8 against some real region gets `new` written over that region, silently,
reported as `edited (fuzzy ~86%)`. The model never identified that text. The
failure is invisible — it does not error, it does not stall, and a run can pass
its tests while carrying an edit nobody chose.

I am deliberately not changing this in build 96. It is a behavior change to the
edit path with real blast radius, it needs its own measurement, and bundling it
into a message-wording build would make both unreadable. Options, cheapest
first:

1. **Make the gate real.** Return a distinct status from tier 3 and require
   `ctx.confirm`; with no confirm available, decline and fall through to
   `_not_found_help`. Headless runs then behave like every competitor's
   exact-only default. This is the honest reading of what the comment already
   claims.
2. **Raise the threshold** toward Roo's 1.0 / Cline's off-by-default. Cheap,
   but picks a number without evidence.
3. **Keep it, and say so** in the success message — "matched at ~86%
   similarity, verify this is the line you meant" — so at least it is visible.

#### 5.19a Measured, same day — the alarm above is overstated

I ran the free check before picking an option, and it moves this down the list.
Every fuzzy application in the entire results archive:

| | |
|---|---|
| applications, all sweeps ever | **29** |
| similarity min / median / max | **85% / 100% / 100%** |
| applications in the b87+ corpus (185 runs) | **0** |
| last sweep containing one | r25 (build 41) |

Nothing has ever been applied near the 0.80 floor; the median is an exact
match that only missed tier 1 on whitespace. The tier is behaving as a
typo/drift rescue, not a speculative rewrite, and it has not fired at all in
the current architecture.

**And the reason it stopped firing is 5.17.** An authored `old` sits ~0.67 from
the closest real line — *below* the 0.8 bar — so the fuzzy tier cannot rescue
it and the call falls through to `not_found`. That is exactly why there are 187
not-found events and zero fuzzy applications. The two findings are the same
fact seen from either side.

So the corruption scenario is real as a mechanism and unobserved in practice.
Revised call: **option 3** (name the similarity in the success message so a
fuzzy apply is at least visible), low priority, and drop option 1 — making the
gate real would change nothing measurable while removing a rescue that costs us
nothing. The misleading `(human-gated)` comment at `fs.py:168` should be
corrected regardless; it is what sent me down this path.

Standing lesson, since this is twice in one day: I flagged this as a live
hazard from reading the code, and the archive said it has never happened.
Mechanism first, measurement before priority.

### 5.20 The ambiguous-match error showed the model nothing to choose with (build 97) ✅

The wall build 96 uncovered (5.17b). Sized before building — but see **5.20a
below: the sizing I shipped this on was partly wrong**, and the corrected
numbers make a stronger case, not a weaker one. Correct figures: **124
ambiguous-match events across 74 of 104 b87+ runs (71%)**, after which the next
edit attempt lands only **33% of the time**. It outranks the reindent work
(5.18, 14 events), which is drafted and deferred.

The message was self-defeating. Verbatim from the sweep:

```
`old` appears 2 times in …/textkit.py, so it is not clear which one to change.
Either add more surrounding lines to `old` so it matches exactly ONE place, or
pass replace_all to change every one. The matches are at:
  line 23: current = [word]
  line 30: current = [word]
```

Every listed site is byte-identical **by construction** — `_match_locations`
echoed the model's own search text back once per match. So the reply told the
model to "add more surrounding lines" while showing it no surrounding lines and
no basis for preferring line 23 over line 30. Resending unchanged is close to
the rational response to that message.

**Build 97** rewrites `_match_locations` to render each site with the real lines
around it and a `>` marker on the matched span:

```
  ── match at line 6 ──
     5 |          if current_len + len(word) < width:
     6 |>             current = [word]
     7 |              current_len = len(word)
  ── match at line 10 ──
     9 |              lines.append(" ".join(current))
    10 |>             current = [word]
    11 |      return lines
```

and rewrites the surrounding prose to say plainly that resending will fail
identically, then offer three concrete moves: extend `old` with a
distinguishing line *copied from what is shown*; use `replace_lines` with that
match's line number; or pass `replace_all` if every occurrence really is meant.

`_AMBIG_SITES = 4` and `_AMBIG_WINDOW = 1` are named constants, not inlined, so
the width is A/B-able. Both are deliberately small: **build 90 measured a wide
context window to be actively harmful** — a large block inside an error message
reads to a 14B model as a listing to discuss rather than text to copy. Four
sites at one line of context is ~12 lines.

7 new tests plus one pre-existing test updated (it asserted the old `line N:`
format); **1038 total, green**. Not yet A/B-ed — and per 5.17b, the exec-bugfix
r8 instrument cannot resolve it. Exposure is the thing to check first, and the
triggering condition both arms emit is "appears N times", never the new
wording.

**5.20a — correcting my own sizing (self-audit, same day).** I shipped 5.20
claiming "43 of 125 answered by resending a byte-identical `old`". Re-measured
under a stated definition — for each ambiguous event, find the next
`edit_file`/`replace_lines`/`write_file` call and compare its `old` and `path`
to the failed one — the true count is **5 of 124**. The 43 does not reproduce
and I cannot reconstruct what produced it; treat it as withdrawn. This is
methodology rule 7 turned on my own justification, and the third such
correction in two days.

The event count and the run coverage survive, and are worse than I said: **124
events across 74 of 104 runs — 71%**, not 37%.

What the corrected classification shows is a *different and more interesting*
failure. Of the 117 `edit_file` calls that follow an ambiguous error:

| next `edit_file` after "appears N times" | n | share |
|---|---|---|
| a **NEW, invented** `old` **+ `replace_all`** | 74 | 63% |
| the same `old` + `replace_all` | 38 | 32% |
| resent byte-identical | 5 | 4% |

and the whole population lands only **41 of 122 (33%)**.

So the model almost never sits still — it reaches for `replace_all` **96% of the
time** (112/117), and in *most* of those it has also rewritten `old` into
something that then comes back "not found". That is the old message read
literally: it offered exactly two routes, "add more surrounding lines" or
"pass replace_all", while showing no surrounding lines. With nothing real to
extend from, "add more surrounding lines" can only mean *invent* more — which
is precisely the 5.17 authoring bug, re-triggered by our own error text — and
`replace_all` is the one route that needs no information the model doesn't
have. It is also the dangerous one: it changes every occurrence, including the
ones that were correct.

Build 97 is the right fix for *this* reading too, and more squarely so: showing
the real neighbouring lines is what makes "extend `old`" possible without
inventing, and adding `replace_lines` as a third named route gives a precise
alternative to the blunt one. **The metric to grade the b97 sweep on is
therefore the post-ambiguous landing rate (33% baseline) and the
invented-old+`replace_all` share (63%)** — not the resend rate, which was never
the problem.

### 5.21 The stall, decomposed — half of it is edit-landing (2026-08-07)

Measured while the b97 sweep ran, and the most useful thing in this milestone:
a taxonomy of the user's *"repeating and stalling out is the norm"*, built from
what happens **between two consecutive pytest runs that print the identical
failing assertion**. 144 such gaps across the b87+ archive (104 runs):

| between two identical test failures | n | share |
|---|---|---|
| the model tried to edit and **every attempt failed** | 71 | 49% |
| **no edit was attempted at all** | 43 | 30% |
| an edit **landed** and the failure did not move | 30 | 21% |

**Half of every stall episode is the edit not landing.** Across those 71 gaps
the failure kinds are ambiguous-match 65, not-found 64, no-op 53 — which is
builds 96, 97 and 88 respectively. This is the measurement that justifies the
whole edit-landing line of work, retroactively and better than the sizing I
originally gave any of them: the model is not mostly confused about the bug, it
is mostly unable to express the change.

The 30 "landed and it didn't help" gaps are the genuine reasoning failure, and
they are **capability-bound** — the same wall as 3.1 (own_tests_pass 0/12 across
model sizes). Worth knowing that ROADMAP 5.13c proposed a message for exactly
this bucket: it is the *smallest* of the three, and the one least likely to
respond to better wording. **De-prioritise 5.13c accordingly.**

**The 43 "no edit attempted" gaps are a new and cheap finding.** Broken down by
what the model did instead:

- **21× `pytest` → `update_plan` → `pytest`** — ticks its plan checklist, then
  re-runs the suite without having touched a file.
- **19× `pytest` → `pytest`, nothing in between at all** — the identical command
  re-issued against an unchanged tree, necessarily producing the identical
  output.
- 3× a read or an `ls` in between.

So **40 of 43 are "re-ran the tests without changing anything"**. That is not a
reasoning failure and not an editing failure; it is the model not noticing that
nothing on disk has changed. It is also the cheapest lever left, and unlike a
prompt tweak it can be stated as a fact rather than advice: when a `bash`
command is byte-identical to the immediately preceding one **and no file has
been modified since it last ran**, append that to the result — the output is
unchanged because the tree is unchanged, so a file must be edited before any
test result can differ.

Candidate **build 98**, scoped deliberately narrowly: it must key on a real
mtime/hash check, not on the command string alone (a legitimately repeated
command after a successful edit must stay silent), and it must be advisory text
on a successful result, never an error — the same shape as the build-22 syntax
warning, which is the precedent for this kind of inline note.

Ranking after this measurement: **edit-landing (96/97) > the unchanged-tree
re-run (98, 40 events) > 5.18 reindent (14) > 5.13c (30 events but
capability-bound)**.

### 5.22 The escape hatches never miss, and the model rarely takes them

Follows 5.21's finding that half of all stalling is edit-landing. If the
`edit_file` retry is where runs die, the question is what the *alternative*
routes are worth. Measured over b87+, taking each `` `old` not found`` event and
the next edit attempt of any kind — 124 events, 4 turn-ends:

| route taken after a miss | attempts | landed |
|---|---|---|
| retry `edit_file` | 87 (73%) | 32 — **36%** |
| switch to `replace_lines` | 16 (13%) | 16 — **100%** |
| switch to `write_file` | 17 (14%) | 17 — **100%** |

**Thirty-three attempts on the two escape hatches, thirty-three landings, zero
failures.** And the model reaches for one only 27% of the time; nearly
three-quarters of the time it retries the tool that just failed, at a bit over
one-in-three.

The per-sweep split is stable, not one outlier: `edit_file` recovery runs
0/9, 4/15, 5/17, 5/17, 8/17 across b93–b95, while `replace_lines` is 16/16 and
`write_file` 17/17 pooled.

**The confound, stated up front.** This is not a randomised comparison — the
model chooses the route. It plausibly reaches for `replace_lines` precisely
when it already knows the line number, i.e. on the cases it understands, so
some of that 100% is selection rather than the tool being better. What the
selection story cannot explain away is the *absence of a single failure* in 33
tries: whatever causes the model to pick a hatch, picking one has never yet
been a wasted call, while the default route wastes two calls in three.

**Supersedes a number in 5.16a.** That entry says "retrying `edit_file` after a
miss landed 0 of 64, in every cell". Under the definition used here — next
edit attempt of any kind, landed = the result is not not-found/ambiguous/no-op/
error — the figure is 32 of 87. The two do not reconcile and I cannot recover
5.16a's exact predicate, so **treat the 0/64 as withdrawn** and this table as
the live one. The qualitative claim 5.16a was making (retrying `edit_file` is a
bad bet) survives, and the corrected number still says it.

**One suggestive thing, flagged and not claimed.** In the b96 sweep, not-found
events fell to 13 (from 22, 23, 26, 26 on the same case and model in b93–b95)
and `edit_file` recovery rose to 10/12. Both point the way build 96 intended.
Both arms are pooled and n=12; per the standing rule this is a hypothesis for a
future sweep, not evidence.

**5.22a — the 36% was hiding a 2%/67% split, and it reconciles 5.16a.** Before
building anything on the table above I tested the obvious alternative
explanation: maybe the hatches aren't special, and what actually separates a
recovery is whether the model *looked at the file again* first. Re-cut the same
124 events on whether any `read_file`/`grep`/`glob` intervened before the next
edit attempt:

| route after a miss | attempts | landed |
|---|---|---|
| `edit_file`, **no re-read** — retry from memory | 41 | 1 — **2%** |
| `edit_file`, **after a re-read** | 46 | 31 — **67%** |
| `replace_lines`, no re-read | 16 | 16 — **100%** |
| `write_file`, no re-read | 17 | 17 — **100%** |

**Retrying `edit_file` from memory after a miss is 1 for 41.** That is the real
shape of the failure, and it is worse than any aggregate I have quoted. It also
**reconciles 5.16a's "0 of 64"**: that figure was measuring this population, not
the one in 5.22's table. The finding was right and the number was close; what
was missing was the variable that produces it. I am leaving 5.22's withdrawal
note standing, because 0/64 and 1/41 are still not the same count, but the
qualitative claim is now confirmed with its mechanism attached.

The two hatches need no re-read to work, because neither asks the model to
reproduce text it cannot see. That is the whole story in one line: **after a
miss, any route that does not require quoting from memory lands; the one that
does, does not.**

Note also what this says about **build 93** (`require_read_before_edit`, 5.11c,
shelved as UNPROVEN at zero exposure): its premise is exactly this gap, and the
correlational support is now strong. The gate never fired in its sweeps, so it
remains unproven as *implemented* — but it is no longer a hypothesis in search
of evidence.

**Candidate build 99, revised by 5.22a.** The not-found message currently names
one action — "copy your `old` out of it verbatim", i.e. retry `edit_file`, the
2% route when done from memory — and buries `replace_lines` in
`_TRY_REPLACE_LINES` behind a self-diagnosis the model cannot make ("if the
target text is hard to reproduce EXACTLY"). Build 96's lesson applies: advice
gated on the model correctly diagnosing itself does not reach it.

We already compute the line numbers to cut the snippet, so 99 adds no
information — it re-ranks the routes and fills in the arguments:

1. **`replace_lines` with `start=N, end=M`**, stated as a concrete call, no
   `old` required. Only when `_best_block` was confident and the block was not
   truncated — a wrong line number is worse than none.
2. **Re-read, then edit** — the 67% route, named as a route rather than left
   implicit.
3. Say plainly that re-sending an `old` composed from memory is the one move
   that does not work.

A narrower version of build 93 also becomes well-founded here: rather than a
blanket read-before-edit gate, require a re-read only for a content-anchored
edit **to a file that just returned not-found**. That targets the 41-attempt,
2%-landing population precisely and leaves every other edit alone.

**Ship 99 with the 5.18 reindent rescue**, which is why that deferral now
reverses. 5.18 was ranked at 14 events as a standalone fix; build 99
deliberately moves traffic onto `replace_lines`, whose undocumented demand for
absolute indentation is what 5.18 fixes. Promoting a route without its safety
net converts a not-found miss into a syntax-guard rejection and calls it
progress. Grade the pair on the not-found recovery rate (2% from memory, 67%
after a re-read) **and** on the `replace_lines` syntax-guard rejection rate,
which must not rise.

Ranking: **99 + 5.18 (87 events) > 98 (40 events)**.

### 5.20b Build 97 worked, and made everything worse — the order of the routes is the lever (build 98) ✅

The b97 A/B is the clearest result this project has produced, and it is a
**regression**. `b97-ambig`, exec-bugfix, qwencoder14, r8:

- score delta **−0.3125** (base 0.781, cand 0.469), W1/L5/T2, **6 informative
  pairs**, sign-flip **p=0.0625**, against a calibrated floor of 0.2812. First
  time a delta has exceeded the floor. It exceeds it downward.
- endings: **VERIFIED 5/8 → 0/8**, DONE 6/8 → 0/8, stopped 2/8 → **8/8**, mean
  landed edits 4.4 → 1.2. Exposure symmetric: 8/8 runs both arms.

And on its own target metric, build 97 **succeeded**:

| after an ambiguous match | base | cand |
|---|---|---|
| next edit landed | 8/17 (47%) | 8/11 (**72%**) |
| invented `old` + `replace_all` | 9/17 (52%) | **0/11** |
| `` `old` not found`` events, whole run | 7 | **0** |

Zero not-founds in the candidate arm. The model stopped inventing text
entirely. The mechanism counts say why, and they are not subtle:

| | base | cand |
|---|---|---|
| `edit_file` calls with a **multi-line `old`** | **0** | **22** |
| **syntax-guard refusals** | **1** | **20** |
| syntax warning on a saved file | 8 | 24 |

**The model did exactly what the message told it to, and that was the problem.**
Build 97 offered three routes — extend `old`, use `replace_lines`, use
`replace_all` — and listed extending first. The model took it essentially every
time and took `replace_lines` **zero** times. Extending `old` across lines means
`new` must now be a correct multi-line replacement, and qwencoder14 cannot write
one: the syntax guard refused 20 of them, the model re-sent, and 8 of 8 runs
died on the repeat guard. Checked and ruled out: not one `old` carried a
line-number gutter copied from the new rendering, so the format itself is clean.

Read the trajectory (`r1__cand`) and it is one motion: ambiguous at 36.5 s →
extend `old` to two lines at 41.8 s → syntax-guard refusal → identical resend →
refusal → `write_file` the whole file → SyntaxError → spiral.

**The lesson, and it is the generalisable one: the route named first is the
route taken.** Not the route best argued, not the one marked reliable — the
first. That is a much sharper version of what 5.22a inferred correlationally,
and here it is causal, from a controlled comparison where only the message
changed. It also means every multi-option error message in this codebase is
making a choice on the model's behalf whether or not its author realised it.

**Build 98** keeps build 97's rendering — the surrounding-lines block is what
drove not-found to zero, and that part is unambiguously good — and reorders the
routes:

1. **`replace_lines` with `start`/`end` from the match list**, stated as a
   concrete call, single line, no `old` to disambiguate. First, because it is
   the only route with no authoring burden.
2. `replace_all` if every occurrence really should change.
3. Extending `old` **last, with its cost stated**: `new` must then contain the
   whole extended block, "and that is where this edit usually breaks."

Shipped in the same change, per 5.22a: **the 5.18 reindent rescue**. Build 98
deliberately pushes traffic onto `replace_lines`, which silently demands
absolute indentation while `edit_file` supplies it. `_reindent_to` shifts a
`new` whose first non-blank line sits at the wrong column, but *only* after the
literal text has already failed `_syntax_reject`, so no currently-succeeding
edit can take the path; when it rescues, the result says so; when it cannot,
`_column_hint` names the specific column mismatch. The tool description now
states the absolute-indentation contract. 16 tests, **1054 total, green**.

**Do not re-run the b97 configuration.** The question it answered is settled.
The open question is whether reordering recovers the base arm's 5/8 VERIFIED
while keeping the 0 not-founds — that is what the next sweep must show, and it
must be graded on **both** (a repeat of the 47%→72% landing win alongside 0/8
VERIFIED would mean the same trap with a different first route).

### 5.23 The model named the tool in the fence tag and we called it prose (build 99) ✅

**Found while trying to read the b98 sweep, which measured nothing.**
`b98-routeorder` returned delta +0.000, **0 informative pairs**, both arms
identical at 4.0 iterations, 0 landed edits, gave-up 8/8, zero exposure — from
the *same base commit* that had produced 22.2 iterations and 5/8 VERIFIED an
hour earlier. A 4-run smoke sweep reproduced it exactly. The server was healthy
(0.5 s probe, coherent 174-token generation), so the arms were not the story:
something was ending every turn at iteration 3.

The last event of a degenerate run says it outright:

```
[turn_end] result = "```update_plan\n{\"tasks\": [\n  \"[x] Run initial tests…\",
                      \"[>] Fix the bug in `word_wrap` function\", …]}\n```"
```

A well-formed `update_plan` call, emitted as the turn's **final answer**. The
model had put the tool name where Markdown expects a language tag. `extract()`
matched no tier: `_FENCE_OPEN_RE` accepts only ```` ```tool / ```tool_call /
```json ````, and the body names no tool, so tier 2 never opened it and tier 3's
strict salvage refused a nameless object. No malformed note, no nudge — the loop
saw a text-only reply and ended the turn. **A perfect tool call, discarded in
silence.**

**How much of it there is.** Every `turn_end` in the archive whose result opens
with a name-tagged fence:

| tag | n | body | why every tier missed it |
|---|---:|---|---|
| `update_plan` | 33 | valid JSON, nameless | tag not an envelope tag; no name key |
| `bash` | 13 | JSON-ish, **already holds `{"name": "edit_file", …}`** | tag was just a wrong language guess |
| `tool` | 5 | Python `"""` used as a JSON delimiter | a different bug (below) |

46 turn-ending messages, and **20 of 20** across `b98-routeorder` and
`b98-smoke`. Sporadic before (b93 12/24, b96 4/16) and total now. This is why
the sweeps were unmeasurable, and it silently taxed every prior sweep too.

**Build 99 — tier 2b.** A fence tagged with a **live tool name** is opened and
its body parsed as that tool's arguments. Three constraints keep it narrow:

- **Only tags that are known tool names** are opened. A ```` ```python ```` or
  ```` ```diff ```` illustration is stepped over by the tag alone, so
  `_closing_fence` — which tracks JSON string state — never runs over prose it
  cannot track, and cannot swallow a real call that follows.
- **The body must be JSON** (strict, embedded-object, or the loose key-anchored
  recovery). A non-JSON body is prose and stays prose, silently — no malformed
  note, no execution. This is what makes ```` ```bash ````, also the commonest
  Markdown language tag, safe to accept: `pytest -q` in a bash fence is not a
  call. (Zero plain-shell bodies exist in the archive, so the rule costs
  nothing today and holds the door shut.)
- **A name in the body always wins over the tag** — those 13 ```` ```bash ````
  fences are `edit_file` calls, and must run as `edit_file`.

Tier 2b runs *before* the malformed early-return, so a recovered call beats a
sibling block's nudge; native `tool_calls` still win over everything.

**Verified by replay, not by argument.** Re-running all 51 archived
turn-enders through the new parser: **49 recovered** (33 `update_plan`,
13 → `edit_file`/`write_file` from ```` ```bash ````, 3 from ```` ```tool ````),
2 still dropped. 13 new tests, **1067 total, green**.

**The 2 survivors are a separate bug**, left open deliberately: the model wrote
Python triple-quoted strings as JSON values (`"content": """\ndef summary…`).
That is a malformed-JSON problem, not a fence-tag one, and wants its own fix.

**Consequence for measurement.** Every A/B before build 99 was run through a
parser that discarded a fraction of the model's calls as prose, at a rate that
varied by sweep (0% to 100%). Deltas are still paired within a sweep, so the
comparison holds — but the *absolute* iteration counts and VERIFIED rates in
5.11–5.22 are depressed by an unknown amount, and any sweep whose two arms drew
different rates carried noise we attributed to the change under test. The
0.2812 floor was calibrated under this defect and should be recalibrated.

**The b98 sweep must be re-run against a base that includes this fix**, or it
measures the parser rather than the route order.

### 5.24 The b98 verdict: the score says nothing, the mechanism moved a long way (2026-08-07)

First sweep run on a base that includes the b99 parser fix (`b98-abbase` =
89cf3bc + only `toolparse.py`), so route order is isolated from parsing.
Candidate is build 98 — b97's match rendering, b98's replace_lines-first
ordering, and the 5.18 reindent rescue, together.

**The score is INCONCLUSIVE and must be reported as such.** Delta **+0.031**,
W2/L3/T3, 5 informative pairs, **p=1.0**, against a 0.2812 floor. A 4-pair smoke
had shown +0.4375 (W3/L0/T1) and it did not survive the full run — a clean
reminder that a 4-pair smoke is a liveness check, never evidence.

**What the events show is not ambiguous at all:**

| per arm, n=8 | base | cand |
|---|---:|---:|
| `edit_file` calls | 48 | 73 |
| `replace_lines` calls | **0** | 10 |
| `read_file` calls | **16** | **61** |
| syntax-guard rejections | **33** | **4** |
| landed edits / run | 2.2 | **7.8** |
| mean iterations | 18.0 | 39.0 |
| VERIFIED | 0/8 | 1/8 |

Two things follow. The syntax-guard rejection was the base arm's single largest
error class (33 of its 68 tool errors) and it nearly vanishes — the candidate
does not author broken multi-line replacements, because it is no longer being
pushed toward extending `old`. And `read_file` quadruples: the candidate arm
re-reads before editing, which 5.22a measured as the difference between landing
2% and 67%. Landed edits per run go 2.2 → 7.8.

**The ambiguous-match comparison is one-armed and cannot be cited as a delta.**
Base logged **zero** ambiguous events in 8 runs; it died on syntax rejections
before reaching one. Within the candidate arm alone: 11 events, next attempt
landed **9/10 (90%)**, took `replace_lines` 9/10, invented `old`+`replace_all`
**0/10** — against a b87+ archive baseline of 33% landing and 63% inventing.
Strong, but measured against history, not against a live control.

**The honest summary: editing mechanics improved and the wall moved.** The
candidate lands 3.5× more edits, avoids the guard almost entirely, and still
verifies 1 run in 8. Its new failure signatures are the ones you get from a run
that *works* and does not converge: `context compacted` ×5, `budget: max
iterations reached`, and 36 red-test results. Base's failures were the ones you
get from a run that cannot act at all.

This closes out the edit-landing line of work as the binding constraint. Build
98 stays — it is better on every mechanism metric and worse on none — but the
next lever is no longer "help the model land an edit". It is **"the edit landed,
the tests are still red, and nothing tells the model that its theory is wrong"**
(5.13c), now reachable because runs survive long enough to get there.

**Also worth its own line:** `This edit does NOTHING: new is identical to old`
appears 6 times in the candidate arm (8% of its `edit_file` calls) and 0 times
in base — the exact error the user reported from a live session. Base simply
never got far enough to emit it. It is a real residual, not a regression.

### 5.24b The model re-runs the tests, reads the same failure, and updates its plan

The lever underneath 5.24, measured on `b99-routeorder` — the first sweep whose
runs survive long enough to reach this state at all:

| | |
|---|---:|
| failing test runs | 84 |
| ...**identical to the immediately preceding test run** | **47 (56%)** |
| runs containing at least one | **16/16** |

And on the (landed edit → next test) transition: **63% leave the failure
unchanged**, in 11 of 16 runs. What the model does next is the finding that
shapes the fix — **23 of 37 times it calls `update_plan`**. It does bookkeeping.
It ticks the task and moves on, because nothing in what it just read says the
attempt failed to matter. Exactly **one** of 37 re-read a file.

**Why the existing stall nudge doesn't cover this.** `_nudge_stall` already says
close to the right thing and almost never fires: `error_sig` is the joined
content of *every* errored call in the batch (`loop.py:1304`), so an edit error
landing in the same iteration as a test failure produces a different key than
the test failure alone. Measured on the same sweep: **97 distinct error
signatures under that keying vs 37 real test-failure identities** — 2.6×
fragmentation. It fired 8 times against 47 opportunities.

I checked the obvious alternative first and it was **wrong**: I expected
pytest's varying `in 0.42s` to be breaking byte-exact matching. It is not — 20
of 68 consecutive failing results are already byte-identical, and normalising
the duration changes nothing. The fragmentation is the batch join, not timing.

**Build 101 (shipped).** Append the note to the *result*, not as a nudge: it is
an observation about the very output the model is reading, and it is true the
first time it happens rather than after a streak. Same shape as `_EMPTY_OK` and
the build-22 syntax warning. Wording leads by closing off `update_plan` and then
names re-reading the failing test, per methodology 9.

**This also CLOSES 5.21's stat walk rather than deferring it.** That draft
proposed proving the tree unchanged, and correctly warned an fs-only counter
would lie whenever the model edits through bash. Dropped: *"this is the same
failure as the previous run"* is a pure observation about two outputs we hold,
always true when the identities match, needing no theory about the tree — and it
covers 5.21's population (re-ran having changed nothing) and 5.24b's (changed
something that didn't matter) in one sentence that cannot be false.

**Validated before shipping, on the corpus, not by argument.** The identity
function (progress line + failed test names + exception types; deliberately not
byte-exact) over all **9,644** archived tool results: 1,958 identified as
failing tests, **100% of them produced by a pytest command — zero false
positives on any other command**, zero fires on a run reporting only passes,
zero fires on a non-`bash` tool. It would annotate **692 times across 79 run
files**. The largest measured exposure of any lever in this project.

Re-run at ship time over the full archive (9,654 results): **1,961 identified,
all 1,961 from `bash`, all carrying pytest markers, zero green runs among them**
— **693 annotations across 370 run files**. Repeat depth is a long tail: 446
first repeats, 92 seconds, 41 thirds, and **78 at six or more**, which is what
justifies the escalation — the paragraph earns its length once, then the running
count carries the signal.

**One defect caught in review, worth recording because it would have been
invisible.** `result_sig` — the key the no-progress repeat guard compares — was
computed from `results` *after* the annotation. An annotation carrying a running
count makes every repeat a different string, so the guard would have stopped
firing on precisely the case it exists for, and nothing would have failed
loudly. `result_sig` is now taken from the raw results before annotating, with a
test (`test_the_annotation_does_not_disable_the_repeat_guard`) that fails if it
moves back. 9 tests, 1082 total.

### 5.24c The b101 verdict: the cleanest causal result yet, and it converts nothing

`b101-samefail`, 16 runs, base `e160435` (build 100) vs the live tree.

**The target metric moved as decisively as anything measured in this project.**
What the model does next after a stuck transition (an edit landed, the failure
did not move):

| next action | base | cand |
|---|---:|---:|
| `update_plan` | **20 of 28** | **0 of 28** |
| `edit_file` | 3 | 17 |
| `write_file` | 1 | 7 |
| `read_file` | 0 | 4 |
| `bash` / turn ended | 4 | 0 |

Zero. The bookkeeping response was eliminated outright — and it is *local*:
total `update_plan` calls across the sweep were **59 base vs 55 cand**,
essentially unchanged. The model did not stop planning; it stopped planning at
the one moment planning was useless. That specificity is what makes this causal
rather than a general shift in behaviour.

Supporting movement, cand vs base: `replace_lines` 8 → 34, `write_file` 4 → 17,
landed edits 7.2 → 12.5 per run, repeat rate 69% → 58%, repeat depth ≥4 down
from 14 to 4, `repeated call` nudges 14 → 5.

**And nothing converted.** VERIFIED **0/8 on both arms**. Stopped 8/8 both.
Score delta **−0.0938**, W1/L4/T3, p=0.375 — inside the 0.2812 floor, and
therefore no evidence of harm either. **Verdict: KEEP, do not credit.** It
removes a demonstrably useless behaviour at zero measured cost; it does not
rescue the run.

**Why not, from the trajectory (methodology 3) — `r4__cand`, the best run.** At
118.0 s pytest returns the failure identical to 85.7 s, and the very next action
is `edit_file`, not `update_plan`. The redirect fired. What it edited was a
**reversal of its own correct edit from 44.6 s**. Then an identical re-send at
153.4 s (no-op), then the repeat guard killed it. Redirected off bookkeeping,
it thrashes instead.

The specific failure is legible in that same trajectory. The note said *"read
the failing test itself first — open it and the function it exercises."* The
model answered with `read_file` on **`textkit.py`, the source it had been
editing** — never once on the test, in any iteration. Told to do something it
had no identifier for, it substituted the nearest thing it already knew how to
do. That is build 102.

### 5.24d Two defects in build 101, both found by grading it (build 102) ✅

**1. The annotation is invisible to the archive.** `result` events are written
per call at `loop.py:1264`, *before* `_run_calls` appends the note — so the
sweep recorded **0 annotations while 28 fired**, and exposure could only be
established by inference plus the `update_plan` discontinuity. A lever you
cannot see is a lever you cannot grade (methodology 2). Now emits
`{"phase": "nudge", "reason": "same failure (N runs in a row)"}`, which
`armstats` already tallies and the REPL already renders — so it also surfaces
the stall to the user, which is the symptom they reported in the first place.

**2. The note named an action the model had no identifier for.** Measured
whether it can be named at all, over the b99 + b101 repeat events: `FAILED
path::test` survives in **33 of 106** (the short summary is usually truncated
out of the result), but pytest's FAILURES banner — `____ test_name ____` —
covers **106 of 106**. Between the two the test is *always* nameable. So the
note now says which test, and says explicitly *"the TEST, not the source file
you have been editing"*, which is the exact substitution the trajectory caught.
Two names shown, the rest counted, so a 12-failure run doesn't paste a list.

9 tests, 1091 total.

**3. …and then the naming itself rendered badly (build 103).** ✅ Found by
printing what the model *actually saw* on a live b102 run rather than by
reading a unit test. Every failing test was in `test_textkit.py` and the
filename was repeated inside every `file::test` id, so the one actionable
token — *open `test_textkit.py`* — sat buried in a 140-character run-on.
`_split_test_ids` now pulls out the shared file when all the ids have one; the
note says the file once as the thing to **open** and the bare names as the
thing to **read**. Mixed-file ids and the banner fallback render as before.
Rendering only — no new information, no new trigger — so it ships alongside
the next real lever rather than getting its own sweep. 5 tests, 1096 total.

### 5.25 Route-order audit of the remaining multi-option messages (5.20b applied)

`_not_found_help` is ordered exactly backwards against the 5.22a measurements:

| position in the message | route | measured landing |
|---|---|---:|
| **1st (the lead sentence)** | "Copy the target text EXACTLY…" → retry `edit_file` from memory | **1/41 (2%)** |
| 2nd (the snippet) | copy `old` out of the block shown | 31/46 (67%) |
| **last**, gated on self-diagnosis | `replace_lines` | **16/16 (100%)** |
| never named | `write_file` | 17/17 (100%) |

The gate on the last route is the same defect build 96 fixed elsewhere: *"if the
target text is hard to reproduce EXACTLY — it has backslashes, quotes, or
unusual whitespace"* is a condition a model that believes its `old` was already
exact will never match. The 100% route is both last and conditional; the 2%
route leads.

Correct order: `replace_lines` with the line numbers filled in (we already print
them) → re-read then edit → copy-exactly last.

**Draft: `$CLAUDE_JOB_DIR/tmp/b102_draft.py`. The older `b99_draft.py` is
SUPERSEDED and must not be applied** — it was written before 5.20b and *appends*
the two good routes to the end of the message, leaving "Copy the target text
EXACTLY" as the lead. Appending is precisely the mistake 5.20b names.

**This one cannot be A/B'd, and that is the finding.** Not-found exposure has
collapsed as b96 and b98 moved the traffic elsewhere:

| sweep | not-found events | runs hit |
|---|---:|---:|
| b97-ambig | 7 (base only) | 7/8 base, 0/8 cand |
| b98-routeorder | **0** | 0/16 |
| b99-routeorder | **2** (cand only) | 1/16 |

A fresh sweep would land in the same "exposure 0 → UNPROVEN" trap as builds 93
and 95. Ship it on mechanism plus a replay of the 87 archived events (the
message is deterministic given text/`old`/`new`, so the route ORDER can be
checked directly), and record it as **unproven-by-sweep** rather than reading an
arm difference. That measures what changed; it cannot measure what the model
then does, which is what a sweep is for — so the honest ceiling here is "the
message is now ordered correctly", not "this helps".

**The census that displaced it.** Error results by kind, b99-routeorder cand:
failing test runs **57** (36 of them repeats → build 101), ambiguous **11**,
syntax-guard **8**, no-op **6**, unread **3**, not-found **2**. The
ambiguous-match message is now the largest remaining message target — but b97
rewrote it and its endings went to 0/8 VERIFIED, so read that trajectory before
touching it again.

#### Shipped as build 105 ✅ — unproven-by-sweep, by design

The message now runs: **`replace_lines start=N end=M`** (arguments filled in,
no gate) → the block, with the copy route named after it → the from-memory
warning last, phrased as a measurement rather than an instruction (*"that lands
1 time in 41"*), because "copy it exactly" does not reach a model that believes
it already did. The header states the failure and nothing else.

Two choices worth recording:

- **The stated range is the DISPLAYED block, window included** — not the region
  that matched `old`. One range, one meaning, and it is the block printed
  directly beneath it, so a number that disagrees with what the model can see
  is impossible. The cost is that `new` must carry the unchanged context lines,
  so the route says exactly that on multi-line spans. A wrong line number is
  worse than no line number: it sends a *correct* edit to the wrong place.
- **Gated on `confident and not tail`.** An unconfident match has no
  trustworthy numbers, and a truncated block's end line is not the one printed;
  both keep the old wording, `_TRY_REPLACE_LINES` included. The two no-op call
  sites are untouched — no located block there, so nothing to fill in.

**Exposure, measured on the archive instead of a sweep.** 316 not-found events
are stored; 155 predate build 92 and carry the old "The nearest text is around
line N" wording. Of the 161 in the current message's format, **160 took the
confident path** and one did not — so the reorder reaches **99.4%** of modern
not-found events. That is a statement about *reach*, not about behaviour, and
the ceiling stays where the section already put it: the message is now ordered
correctly. Whether it converts is not measurable at current exposure (2 events
in the last sweep). 12 tests, 1112 total.

**One thing the reorder did NOT fix, found while testing it.** When
`_authored_old_note` fires (build 96 — `old` is a draft of `new`), it prefixes
the whole message and ends *"Put the file's existing text in `old` and your
corrected version in `new`"* — which is the 2% route, back in first position,
on exactly the population most prone to it. It is a diagnosis rather than a
route and it was separately validated, so it was not touched here. Open
question: should the diagnosis keep the lead, or hand off to `replace_lines`
after naming the misconception?

### 5.26 No A/A calibration has been able to FINISH since the floor was added (build 100) ✅

The `b99-floor` recalibration died 40 minutes in, after run 1 of 16:

```
[1/16] exec-bugfix · qwencoder14 · r1 · base…
        base: score=0.25 480.3s
  File "evals/ab.py", line 453, in _persist
    a["why"] = (f"A/A calibration: both arms ran identical code, so this "
TypeError: unsupported format string passed to NoneType.__format__
```

`_persist` runs after **every** run for checkpointing, so its first call happens
with one run banked and **no complete pair** — `mean_delta` is `None` there, and
the A/A branch formatted it unconditionally. Any A/A therefore crashes on its
own first run, every time, and always has since that branch landed.

That is the answer to a question standing since 5.14: **why does the only
calibrated setup have just 2 samples?** Not because calibration is expensive —
because it has been impossible. The 0.2812 floor rests entirely on the two
samples that predate this call site, and every "clears the floor" judgement in
this document has been resting on them.

Fixed: say *"no complete pair yet"* when there is no delta. 3 tests
(`tests/test_ab.py`, 45 total) covering the A/A checkpoint, the complete A/A
pair, and the ordinary A/B checkpoint, since the same call site serves all
three. Recalibration re-queued as lever 0.

Noticed only because I read the log rather than trusting the process-exit
notification — a sweep that exits does not mean a sweep that ran.

### 5.76 pre-registration — b128, UNIQUENESS-FIRST (two cases at once)

Written before the code change and before any data. Base = `fa12194` (build 125
code), cand = working tree (build 126). Cases **exec-ambig AND exec-bugfix**,
qwencoder14, r24 each — 96 runs. 5.75 required a two-case sweep and this is it.

#### What 5.74 actually exposed: the description contradicts itself

`edit_file`'s description has said this since long before the selector:

> "Keep `old` to the SMALLEST unique snippet that needs changing (a few lines),
> NOT the whole file"

Build 120 carries that clause and still aims wide **18/24** on exec-bugfix. Add
the rescue sentence and it drops to **10/24**. The rescue did not introduce the
"go small" instruction — it **removed the downside of obeying it**. A short
`old` that matched twice used to be a hard failure; now it is recoverable, so
"smallest" wins the tie and the model drops the word *unique*.

#### The change

One semantic move — make uniqueness the binding constraint and the selector an
explicit fallback — expressed in two clauses:

1. "Keep `old` to the SMALLEST unique snippet that needs changing (a few
   lines), NOT the whole file" → **"Make `old` just long enough to appear
   EXACTLY ONCE — a few lines, extended up to the `def` line if a shorter
   snippet would match in more than one place — NOT the whole file"**
2. The rescue sentence keeps every word b126 credited, plus one: "If `old`
   **still** turns out to appear more than once…"

**Attribution is deliberately deferred and I am saying so in advance.** If b128
wins I will not know which clause did it. That is acceptable *here* because the
two clauses are one instruction, and 5.71 is the record that splitting a
sentence from its context is itself a separate, runnable experiment. Establish
the effect on both cases first; attribute after, if it survives.

#### Calls, per case

0. **Primary, population-independent:** `fully_fixed`.
1. **Mechanism:** first `edit_file` `old` reaches def/docstring.
2. **Guard, new for this sweep:** `edit_file` not-found rate. A longer `old` is
   harder to match exactly. If not-found spikes, the change costs what it buys.
3. **Guard:** false completions. Nonzero in any arm voids the result.

#### The decision rule, fixed now

Build 125's exec-ambig result (18/24 and 22/24 fixed, two replications) is a
**banked asset**. It is not tradeable for a speculative gain elsewhere.

- **exec-ambig fixed < 13/24 → REJECT**, whatever exec-bugfix does. No further
  argument, no re-reading of the trajectories to find a reason to keep it.
- **exec-ambig holds (≥ 15/24) AND exec-bugfix wide ≥ 15/24 AND exec-bugfix
  fixed not down > 1 run → SHIP build 126.**
- **exec-bugfix wide does NOT recover (< 15/24) → the 5.74 licensing story is
  WRONG.** Record that, stop tuning this sentence, and go back to lever 0c.
- Anything else → no ship, write it up, no third guess at the wording.

Noise bars unchanged: |Δfixed| < 15 pts on exec-bugfix is noise at n=24; on
exec-ambig the observed effects have been 40+ pts, so the bar there is real.

## 5.77 INSTRUCTION-SURFACE AUDIT — every contradiction the model actually reads (2026-08-10)

Prompted by 5.74, which showed that two clauses of one description can pull in
opposite directions and that the *net* effect is invisible until measured. This
is a systematic sweep of every surface the model reads: tool descriptions,
schema arg descriptions, the system prompt, and all 31 nudge texts.

Method: descriptions checked against their **runtime assembled strings**, not
the source (they are split across source lines, so `grep` misses them — three
of these were invisible to a naive search). Exposure measured across **1701
archived runs**. Prioritised by *exposure × strength of evidence*, not by how
bad each one reads.

### P0 — tool descriptions. Read by 100% of runs, and b126 proved this surface is the strongest lever in the product.

**D1. `edit_file` states a falsehood, and states it first.**
At 23% into the description: *"must match once unless replace_all is true."*
That has been untrue since build 123 — `occurrence` is the third path, and it
is the one b125 credited with 7/24 → 22/24. The correction sits at **56%** into
the same string, and the word `occurrence` does not appear in the first 400
characters at all. So the model reads a false constraint before it reads the
true one, on the surface where position demonstrably matters.

**D2. `replace_lines` claims the case `edit_file` now owns.**
Verbatim: *"This is the RIGHT tool for … a snippet that isn't unique."* That is
precisely what `occurrence` was built for. `replace_lines`'s description never
mentions `occurrence` at all. This is not cosmetic: archived exec-bugfix runs
that used `replace_lines` and never hit a syntax reject — i.e. did exactly what
this sentence tells them, single-line patching — are **0/132 fixed, in every
one of 15 strata** (5.73). The description routes the model into a strategy
that has never once worked on that case.

### P1 — a nudge fighting the highest-value instruction in the codebase

**D3. The `repeated call` nudge contradicts the ambiguous-match message.**
Exposure: **47.4% of all runs** — the most-fired nudge there is. It says *"try a
genuinely different approach (different arguments, a different tool …)"*. The
ambiguous message says *"Do NOT rewrite your edit … send the SAME call again,
with the same `old` and the same `new`, and ADD one field."* Of the 639 runs
that see the ambiguous message, **388 (61%) also get this nudge**.

Honest scoping: the guard itself is **not** buggy — `_call_identity` includes
the args, so adding `occurrence` reads as a new call and never false-fires. And
the two messages land *adjacently* (within 4 events) only **31 times in 1701
runs**. So the collision is common at run scope and rare at adjacent scope; the
wording is wrong either way, but I am not claiming the 61% is all harm.

**D4. The truncation nudge still ships the clause build 126 deleted.**
`loop.py:1573` says, verbatim, *"keep `old` to the SMALLEST unique snippet that
needs changing (a few lines), not the whole file"* — the exact text build 126
rewrote because it loses the word *unique* and drives narrow aim (5.76). Low
exposure (**2.7% of runs**) but it is delivered at the precise moment the model
is re-deciding how to aim, and it now contradicts the shipped description.

### P2 — the stated finish protocol is not the enforced one

**D5.** The system prompt says: *"After you have everything you need, reply
normally with no tool block."* The model does that, and the loop **rejects it**
while `plan.open`, firing "open plan tasks" — **17.9% of runs**, 461 times.
`update_plan`'s description says to call it "each time a task's state changes"
but never says that finishing requires every task closed. 5.68 measured the
cost: a median of **4 extra tool calls** after the suite is already green. The
model is not being wasteful; it is obeying the prompt and being punished by the
loop.

### P3 — nudge stacking. CORRECTED after re-measuring; the first reading was wrong.

My first pass counted 649 "back-to-back" nudges and reported that 224 of them
demand different next actions. **That detector was wrong.** A prose-only model
reply emits no event, so it counted two *separate turns* as a stack. Re-measured
against `iteration` boundaries:

| | count |
|---|---|
| consecutive nudges **within one iteration** (true stacking) | **242** |
| consecutive nudges **across a turn boundary** | **407** |

Both defects are real, but they are different problems and want different fixes.

**D6 (revised). True same-turn stacking is almost entirely ONE redundant pair.**
"same failure (N in a row)" + "error unchanged across edits" accounts for
**210 of 242 (87%)**. They do not conflict — both demand a `read_file` — they
are two long directives making the same demand in the same message, which
dilutes both. The conflicting-demand pairs I first reported are nearly all
cross-turn, not stacked. *Fix: suppress one when the other fires.* Small, safe,
unit-testable.

**D7 (revised, and the more interesting one). The nudges do not CONVERGE
across turns — 407 cases.** The model is nudged, replies in prose without a tool
call, and is nudged again, often by a *different* guard:

| sequence | count |
|---|---|
| same failure → open plan tasks | 72 |
| repeated call ↔ open plan tasks (both orders) | 86 |
| **repeated call → repeated call** | 56 |
| error unchanged → open plan tasks | 29 |

This is not "contradictory orders in one message". It is a model that cannot
satisfy two guards in sequence: it is told to read a file, then told it has open
plan tasks, then told it is repeating itself — each guard correct alone, no path
through all of them. Note "repeated call → repeated call" (56): the same guard
firing twice with no progress between, which by its own logic means the first
message did not work.

*Fix: needs design, not a wording tweak* — escalation and suppression across
turns (a guard that has just fired and not been obeyed should escalate or stand
down, not repeat verbatim). This is agent-loop work and stays on the top tier.

### P4 — real but smaller, or needing measurement first

- **D8.** `replace_lines`: *"never re-issue the same start/end after an edit"* is
  over-broad. After a **rejected** call nothing changed, so the numbers are
  still valid — and 45% of `replace_lines` calls are rejected (5.73), so this
  tells the model to discard good line numbers routinely.
- **D9.** `replace_lines` never warns that the line at `start` is itself
  destroyed, nor against beginning or ending a range inside a docstring. That
  single off-by-one is **278 of 470** archived syntax rejects (5.73).
- **D10.** `read_file` does not mention that its line-number prefixes must not
  be copied into an edit; the warning exists only in the two editors that suffer
  from it.
- **D11. Needs measurement, do not touch on reasoning.** `write_file` instructs
  the model to split any document over ~6000 chars across `append_file` calls.
  For a `.py` file the first section will not parse, so `_syntax_warning` fires
  "SyntaxError at line N" on a file that is merely incomplete. It is advisory by
  design and may be harmless; measure the repair-loop rate before changing it.

### How these get fixed — NOT all at once

5.74 is the standing reminder that a plausible wording fix can have opposite
sign on a different case. Shipping eleven of them on reasoning alone would
repeat exactly the mistake this session was spent uncovering. So:

1. **D1, D2, D4 are corrections of statements that are FALSE or contradict
   shipped behaviour.** Leaving a falsehood in place is not the conservative
   option. They go in one build and get one sweep together.
2. **D6/D7 are a code fix, not a wording bet** — emit at most one directive
   nudge per turn, ranked. Low risk, testable by unit test, and it cannot make
   any individual message worse.
3. **D3 and D5 are wording bets** on high-exposure surfaces. Each needs its own
   pre-registration, and D5 should be considered together with 5.68's plan-
   credit lever rather than separately.
4. **D8/D9/D10** ride along with the next `replace_lines` change.
5. **D11** is a measurement task first.

**Blocked until b128 exits** — it is mid-sweep and D1/D2 sit in the very
description its candidate arm is testing.

### 5.74 b127 verdict — the sentence does NOT transfer, and it is case-dependent in DIRECTION (2026-08-10)

`b127-transfer`, `--base a175ed7` (build 120) vs build 125, **exec-bugfix**, r24.
The pre-registered NULL fired on the primary outcome. The mechanism did
something I did not predict.

| call | base (b120) | cand (b125) | |
|---|---|---|---|
| 0 `fully_fixed` (primary) | 12/24 | 11/24 | −4 pts, **p = 1** |
| 1 first aim WIDE | 18/24 | **10/24** | −33 pts, **p = 0.039** |
| 2 false completions | 0 | 0 | clean |
| score | 0.750 | 0.729 | W7/L8/T9, p = 1.0 |

**Verdict on the pre-registered question: NULL. The description-audit plan is
dead**, and it died for £0 of implementation. That was the point of running it.

#### It is worse than null: the same sentence has the OPPOSITE sign on the two cases

| | sentence absent | sentence present |
|---|---|---|
| exec-ambig (b125/b126) | wide **0/24**, 0/24 | wide 21/24, 15/24 |
| exec-bugfix (b127) | wide **18/24** | wide 10/24 |

Not a general "aim wide" lever. **Case-dependent in direction**, which is a
stronger and more useful claim than "it didn't help."

The mechanism that explains both: the sentence makes the model reason about
`old` **multiplicity**. On exec-ambig the intended `old` genuinely is ambiguous,
so thinking about multiplicity pushes it to a bigger, unique `old` — wide. On
exec-bugfix the fragments fail by *indentation and not-found*, not multiplicity,
so the same framing instead **licenses a smaller `old`** — narrow. One sentence,
two cases, opposite effects, same mechanism.

#### Why the outcome stayed flat anyway: the selector paid for the damage

Wide aim is still strongly causal on this case *within this very sweep* — wide
20/28 fixed (71%) vs narrow 3/20 (15%), Fisher p = 0.00014. So losing 8 runs of
wide aim should have cost ~4 fixes. It cost 1, because:

| narrow-aim runs | fixed |
|---|---|
| base, no selector available | **0/6** |
| cand, sent `occurrence` | 3/9 (33%) |
| cand, didn't | 0/5 |

Build 125 talks the model out of the 71% strategy and into a 33% one, and the
selector is *just* good enough to cover the loss. **That is a latent risk, not a
success**: on a case where the selector cannot rescue, this bundle would be net
harmful. Recorded as lever 0b-vi.

**Slot bias ruled out** before believing any of it (rule 49): archived
exec-bugfix wide-aim by slot is 77/234 base vs 78/234 cand, **p = 1**.

#### Build 125 stays shipped

Strongly positive where it was measured (exec-ambig 22/24 and 18/24), outcome-
neutral here, 0 false completions in all four arms. Nothing in this result
justifies a revert. What it justifies is **not generalising**, and fixing the
sentence's phrasing rather than its presence — 5.75.

### 5.75 next candidate — stop the sentence licensing narrow aim (NOT YET RUN)

Hypothesis from 5.74, to be pre-registered before any code: the sentence
currently reads as an unconditional permission —

> "If `old` turns out to appear more than once, do NOT rewrite it: resend the
> same call with `occurrence` set to which one you mean."

It buys the exec-ambig win by making the model think about multiplicity, and
pays for it on exec-bugfix by implying a small `old` is fine because there is a
rescue. The candidate keeps the rescue but stops it reading as a preference:
state that `old` should be big enough to be unique *first*, and offer
`occurrence` only as the fallback when it isn't.

**Do not ship this on the reasoning above.** It must beat build 125 on
exec-bugfix *without* losing exec-ambig, which means a two-case sweep and a
pre-registration that says in advance what a mixed result means. 5.74 is
precisely a record of what happens when one case is treated as the world.

### 5.72 pre-registration — b127, THE TRANSFER TEST (2026-08-10)

5.70 pre-committed: *"if the sentence wins, the next move is to test the same
idea on a DIFFERENT tool and a DIFFERENT case before claiming anything
general."* This is that test, and it is written before the sweep runs.

**Question.** The build-123 sentence was tuned on `exec-ambig`. Does it help on
a case it was never designed for?

**Design.** `--base a175ed7` (build 120, verified: sentence absent, `grep -c`
= 0) vs the working tree (build 125, sentence present). Case **`exec-bugfix`**,
qwencoder14, r24. **No source edits are needed** — this measures code already
shipped, so nothing can drift mid-sweep.

#### Why exec-bugfix is the right second case, from the archive

Same mechanism variable, measured independently on 488 archived exec-bugfix
runs: does the FIRST `edit_file` `old` reach the def/docstring?

| first aim | fully_fixed |
|---|---|
| WIDE | 102/190 (54%) |
| narrow | 62/298 (21%) |

Fisher p = 6.7e-13, Mantel-Haenszel pooled OR **11.15** across 9 within-label
strata, clean in 7 of 9 (the two exceptions are `b99-*`, n=3 and n=8, predating
the modern harness). Reading one pair of trajectories from `b115-stallcap`
shows the same thing directly: the winning run's every `old` is
`def word_wrap(text, width):\n    """Wrap …`, the losing run's are fragments
(`for word in words:`, `result.append(lower)`) that miss, fall back to
single-line `replace_lines` patching, and stall-stop.

**This is correlational.** The sentence has never run on exec-bugfix. That is
the whole point of running it.

#### The honest prior: this may well be NULL, and that is worth knowing

Unlike exec-ambig, exec-bugfix is **not starved of wide aim**. Base-arm rates at
build ~120: `b119` 11/14 wide (79%), `b120` 8/14 (57%), `b116` 8/14 (57%). The
sentence took exec-ambig from **0/24** to 21/24 because wide aim was absent
there; here there is at most ~40 points of headroom, and possibly none that the
sentence can reach.

So both outcomes are informative, and I am committing to the reading now:

- **fixed goes UP and wide goes UP** → the sentence is a general aim lever.
  Then, and only then, is it worth auditing every tool description.
- **NULL (neither moves)** → the sentence is not a general "aim wide" lever; it
  works by *supplying a missing strategy* to a model that had none. That is a
  narrower and much more useful claim than the one I would otherwise have made,
  and it kills the description-audit plan before I spend a week on it.
- **fixed DOWN** → the sentence is case-specific and possibly harmful
  off-distribution. Build 125 gets re-examined, not defended.

#### Calls

0. **Population-independent outcome (rule 48).** `fully_fixed`, defined for
   every run regardless of which failure it hits. Primary.
1. **Mechanism.** Wide first aim, same detector as `grade126.py`. Must move in
   the same direction as call 0 or the attribution does not hold.
2. **Guard.** False completions — a run not `fully_fixed` that never stopped.
   0 in both arms or the result is void, as always.
3. **Power warning, stated in advance.** Base-arm `fully_fixed` on this case
   ranges 14%–50% across archived sweeps, so between-sweep variance is large.
   The paired design controls for it (both arms, same session) but I will not
   read a delta under ~15 points as anything but noise at n=24.

**Not in scope, deliberately.** `replace_lines`'s description is the obvious
*second* transfer target — its failure signature is fully characterised below
(5.73) — but changing it in the same sweep would confound two edits. One change
at a time.

### 5.73 the replace_lines finding, banked for later (2026-08-10)

Characterised while picking the transfer target; recorded so it is not
re-derived. `replace_lines` fails on **45% of its 1052 archived calls**, and 470
of those are one thing: a syntax rejection, 313 of them "unterminated
triple-quoted string literal".

The cause is a single off-by-one, visible in the arguments: **278 of 470
rejects use `start=16`**, and line 16 of `textkit.py` is the *closing* `"""` of
`word_wrap`'s docstring. The model means "replace the body, which starts at 17",
begins at 16, deletes the closing delimiter, and leaves the docstring opened at
line 9 unterminated. Nothing in the description warns that the line at `start`
is itself destroyed, or that a range must not begin or end inside a multi-line
string.

**Do not "fix" this by chasing the reject count.** Two false trails, both
walked:

1. Crude association says runs that hit a reject are fixed 51% vs 4% — which is
   entirely confounded by build era (the reject-heavy runs cluster in b110–b120,
   whose fix rates are 54–71%).
2. Stratifying does not rescue it either. The rejects are **incidental**: they
   are the two failed detours a *winning* run makes before returning to wide
   `edit_file` calls. Suppressing them would not have made a single run pass.

The real lever here is the same one as everywhere else — where the model aims —
and it is already being tested as 5.72.

### 5.71 b126 verdict — it was THE SENTENCE, and the schema property is not innocent (2026-08-10)

`b126-attribution`, `--base 57de838` (build 123), cand = build 124 = build 123
**minus one sentence of tool description**, everything else identical.

| | build 123 (sentence in) | build 124 (sentence out) | |
|---|---|---|---|
| `fully_fixed` | 18/24 | **7/24** | Fisher **p = 0.0034** |
| first `old` reaches the def/docstring | 15/24 | **0/24** | the mechanism, gone completely |
| score channel | 0.875 | 0.646 | W3/L14/T7, p = 0.0127 |
| false completions | 0 | 0 | |

**Call 1 and call 2 agree, and call 2 is total.** Removing one sentence from
`edit_file`'s description takes the wide-aim behaviour from 15/24 to **zero**.

#### Both conditions now have two independent replications

| condition | `fully_fixed` | wide first aim |
|---|---|---|
| no sentence, no schema, no selector (b125 base) | 7/24 | 0/24 |
| no sentence, **with** schema + selector + message (b126 cand) | **7/24** | **0/24** |
| sentence + schema + selector + message (b125 cand) | 22/24 | 21/24 |
| the same, in the other arm slot (b126 base) | 18/24 | 15/24 |

The two "no sentence" cells land on 7/24 and 0/24 **twice, exactly**, in
opposite arm slots, with and without the entire selector machinery. The two
"sentence" cells land at 22 and 18. This is as clean as this rig gets, and it
also happens to show the 5.59 arm-slot bias is not driving it — build 123 scored
*higher* in the cand slot (22) than the base slot (18), the opposite of the
direction the bias predicts.

**Verdict: the sentence is the whole effect. Restored as build 125**, with a
comment on it saying why it must not be trimmed as redundant.

#### The schema property is not neutral — it is mildly harmful on its own

Build 124 still carried the new `occurrence` argument, and it did not behave
like plain base code. Its failure *signature* is different:

| | b125 base (build 120) | b126 cand (build 124) |
|---|---|---|
| first `old` | `if x > 100:\n        return 100` — correct indent, 24/24 | `if x > 100:\n    return 100` — **wrong indent**, 19/24 |
| runs that saw ambiguity | 24/24 | 5/24 |
| edit_file calls per run | 5 or 8 | **exactly 1 in 17/24** |
| how it ended | stall stop, 17/24 | **repeated the same call, 17/24** |

Offering the argument *without* the sentence that explains it made the model
mis-indent its `old`, so the edit never matched, so it re-sent the identical
broken call until the repeat stop killed it. Same 7/24 outcome as base, reached
by a worse route. **An argument added to a schema with no prose to anchor it is
a perturbation, not a feature** — which is the same lesson as the sentence, from
the other side: the words do the work, not the affordance.

#### What this licenses, and what it does not

Licensed: rule 47 is no longer provisional **for this tool and this case**. The
description is read before the model aims; the error message is read only after
it is stuck; and on this evidence the former is worth more than six builds of
the latter.

**Not licensed:** any claim about other tools. One case, one tool, one 14B
model. 5.70 pre-committed to testing transfer on a *different* tool and a
*different* case before generalising, and that stands — the whole reason this
result is trustworthy is that I stopped and attributed instead of banking the
7→22 and moving on.

**Next:** the transfer test, then lever 0c (now instrumented, 64% first-position).

### 5.70 pre-registration — b126, which half of the prompt change did it? (2026-08-10)

Written and committed before the sweep starts. This is an **attribution** run,
not an improvement run: build 124 is expected to be *worse* than build 123, and
that is the result I am buying.

**Why it is worth a sweep.** 5.69's effect is the largest this case has seen and
I do not know its cause. Build 123 changed three things at once; only two of
them (`edit_file`'s **description** and its **schema**) are visible before the
first edit, which is where the runs show the change happening. Build 124 removes
**the description sentence only** and keeps the schema property, the selector,
and the rewritten message. So:

- if build 124 collapses to ~7/24 → **the sentence did it**, and the finding is
  that tool *descriptions* steer where a model aims. That generalises to every
  tool in the product and is worth far more than this case.
- if build 124 stays near 22/24 → **the schema property did it**, i.e. merely
  offering an argument changes aim, which is a stranger and narrower finding.
- if it lands in between, both contribute and I will need a third arm.

**Ruled out in advance: generic prompt perturbation.** Across b124 and b125,
**48 of 48** base runs opened with the byte-identical first `old`
(`if x > 100:\n        return 100`). The first call is deterministic under this
server; noise does not move it. So a change that flips it in 21/24 is causal,
whatever its content. (Outcomes still vary later — same base code gave 11/24 and
7/24 `fully_fixed` — so the divergence is downstream, not at call one.)

**Calls:**

1. **Primary, population-independent** (5.69's lesson — always carry one):
   `fully_fixed`. Base here is build 123's own 22/24. Build 124 **collapsing to
   ≤12/24 credits the sentence**; **≥19/24 credits the schema**; 13–18 is
   "both", and says so.
2. **The mechanism, measured where it happens.** Share of runs whose FIRST
   `edit_file` sends a whole-function `old` rather than the 2-line guard:
   build 123 = 21/24, base-code = 0/48. Whichever way call 1 lands, this must
   move in the same direction, or I have the mechanism wrong and neither
   attribution holds.
3. **Ambiguity exposure**, as a cross-check on call 2: build 123 = 3/24 runs,
   base-code = 48/48. Not a pass/fail bar — it is the same quantity as call 2
   seen from the other side, and the two disagreeing means the story is wrong.
4. **No false completions.** 0 in both arms of b125 and 0 in b123; any run that
   finishes clean with a red suite is a REVERT of build 124 regardless of
   everything above.

**Sweep:** `--base 57de838` (build 123 itself, so the removed sentence is the
only difference), exec-ambig, qwencoder14, r24.

**What I will NOT do with this result.** If the sentence wins, the next move is
to test the same idea on a *different* tool and a *different* case before
claiming anything general. One case is not a product-wide finding, and 5.69 is
already a record of what happens when I generalise from a mechanism I inferred
rather than measured.

### 5.69 b125 verdict — build 123 KEPT, and it did not work the way I said it would (2026-08-10)

`b125-occurrence`, `--base a175ed7`, exec-ambig, qwencoder14, 24 pairs.

**The outcome, which is not in doubt:**

| | base (b120 msg) | cand (build 123) | |
|---|---|---|---|
| `fully_fixed` (grader re-runs pytest) | 7/24 | **22/24** | Fisher **p = 1.7e-05** |
| score channel | 0.646 | 0.958 | W15/**L0**/T9, sign-flip p = 0.0001 |
| stopped by the loop | 17/24 | **2/24** | |
| finished clean but NOT fixed (false completions) | **0** | **0** | |

Largest effect anything has had on this case. `ab.py` still prints INCONCLUSIVE
because the delta (+0.3125) is under the k=1 A/A requirement of 0.375 — that is
the score channel's coarseness (5.27), not a real doubt, and the `fully_fixed`
channel and the zero-loss pair record both say otherwise.

#### Call 0 failed, so calls 1–5 are UNGRADED. I am honouring that.

Exposure parity: **24/24 base runs saw an ambiguous message, 3/24 candidate
runs did.** 5.67 says that makes every call below it ungraded (rule 40), and it
does. But the *reason* for the miss is not the one the gate was built for. The
gate exists to catch arms landing in different situations **by chance**. What
happened here is that the intervention acted **upstream of the population I
chose to measure**.

#### What actually happened, from reading the runs

Build 123 changed three things: the ambiguous *message*, plus — unavoidably —
`edit_file`'s **description** and **schema**, which live in the system prompt of
every run. The message is what I designed and pre-registered. The description
is what moved the number.

The first `edit_file` call of each run, before any ambiguous message exists:

| first `old` sent | base | cand |
|---|---|---|
| `if x > 100:\n        return 100` (2-line guard — ambiguous) | **24/24** | 3/24 |
| `def clamp_score(x):\n    """Clamp…` (whole function — unique) | 0/24 | **21/24** |

The candidate model stopped aiming at the duplicated guard and started aiming at
the whole function, which is unique, so the ambiguous branch never fires. That
is the entire effect. **Zero** `occurrence` arguments were sent before an
ambiguous message, so it is not preemptive use of the new argument — it is the
model reading a tool description that now talks about `old` appearing more than
once, and widening its aim.

Six builds of rewriting the ambiguous *message* (119, 121, and 123's message)
moved this case by nothing comparable. One sentence in the tool *description*
moved it from 7/24 to 22/24. **I have been tuning the wrong surface for six
builds.**

#### The selector itself: it works, and it is not what saved these runs

In the 3 runs that did reach ambiguity, the model used `occurrence` on **25 of
25** post-ambiguous edits, and:

| post-ambiguous edit outcome | base | cand |
|---|---|---|
| LANDED | 21/117 (18%) | **23/25 (92%)** |
| no-op | 41/117 (35%) | 2/25 (8%) |
| came back ambiguous | 55/117 (47%) | **0/25 (0%)** |

The mechanical failure is gone — an in-range `occurrence` cannot return
ambiguous, and it doesn't. **And 2 of those 3 runs still failed**, which is the
important part. Reading r1: every edit lands, and the model targets
`clamp_byte` (correct, occurrence 1) instead of `clamp_nibble` (buggy,
occurrence 2), breaks the working twin, notices the suite got worse, flips it
back, and thrashes to the stall stop.

Across all 25 selector edits it chose **occurrence 1 in 16 (64%)**. In this
case's ordering the correct twin is always listed first, so occurrence 1 is
*always the wrong target*.

**So the selector converts an invisible matching failure into a visible
targeting failure** — which is strictly more useful, and it hands 5.61's
**lever 0c (position vs reasoning) its first real instrument.** The question
"does the model pick by position or by reading the docstring" was unmeasurable
before, because the model never got to express a choice. Now the choice is a
single integer in the tool call. 64% first-position is the first datum.

#### Decision: KEEP build 123

A +15/24 improvement with zero false completions and zero losing pairs is not
something to revert because the mechanism surprised me. But it is credited for
the right reason: **the description, not the message.** Two follow-ups, in
order:

1. **Attribution A/B (next).** I cannot separate (a) the new description
   sentence, (b) the new schema property, (c) generic system-prompt
   perturbation. Cheapest discriminator: candidate = description sentence only,
   with the message and the selector reverted. If that reproduces most of
   7→22, the finding is "tool descriptions steer aim" and it generalises to
   every tool in the product.
2. **Lever 0c, now instrumented.** Grade twin choice directly off the
   `occurrence` values.

#### Recorded honestly

- The base arm scored `fully_fixed` **11/24 in b124** and **7/24 here** on
  byte-identical code. That is the run-to-run spread on this arm, and it is
  wide enough that no single-sweep base figure should be quoted as a constant.
- My pre-registered expectation that call 1 would fail (would a 14B model use a
  brand-new argument?) was **wrong** — where it was offered it was used 25/25.
- 5.67's population choice (post-ambiguous edits) is a fourth instance of rule
  43: I fixed the denominator to the failure I already knew about, and the fix
  arrived somewhere else. **A pre-registration should always carry one
  population-independent outcome measure.** `fully_fixed` was in there as call
  5 and is the only reason this sweep is readable at all.

### 5.68 lever candidate — the plan walk after a run has already proved success (2026-08-10)

5.64 noticed this in passing and I have now measured it across every sweep with
events (b121-aa, b122-aa, b123-stalestale, b124-noop).

Take every run that ever reached a **green pytest** (n=19 — that is the honest
denominator; most runs never get there). Count the tool calls issued after that
first green:

| | |
|---|---|
| median further tool calls | **4** |
| p90 / max | 4 / 4 |
| stopped immediately | **0 / 19** |
| total further calls | 49 |
| …that were `update_plan` | 29 (59%) |
| …`read_file` | 10 (20%) |
| …`bash` (re-running what it just ran) | 10 (20%) |

Attribution is not ambiguous: **19 of 19** of those runs get an `open plan
tasks` nudge after the green, and **19 of 19** answer it with `update_plan`.

**The candidate.** Once a verify command has come back green, the run has its
proof. Walking the plan to closure afterwards is bookkeeping, and it costs a
fixed ~4 calls at exactly the point where the budget is most likely to run out —
which is how a finished run turns into a truncated one.

**What stops this being a finding yet — and it must not be written up as one.**
There is no control group: 0 of 19 green runs escaped the nudge, so the archive
cannot say what those runs would have done without it. "Wasted" is my inference,
not a measurement. Two specific things to check before building anything:

1. Does the loop currently *require* a closed plan to declare DONE? If so these
   calls are not waste, they are the exit protocol, and the lever is a different
   one (let a green verify close the plan implicitly).
2. Of the 10 post-green `bash` calls, how many re-run the same command? A model
   re-verifying once is cheap insurance, not a defect.

**Do not pre-register this until both are answered.** Rule 46 — I have now been
wrong twice about a lever whose prose argument sounded finished (5.63's premise,
5.66's option 2), and both times ten minutes of counting settled it.

#### Both checks, answered (same day, before anything was built)

**1. Yes — the loop requires a closed plan to stop.** `loop.py:790` refuses to
end the turn while `self.plan.open` and nudges instead. So most of that tail is
NOT waste; it is the exit protocol, and "wasted calls" would have been the wrong
write-up. There is already a narrow credit above it (`loop.py:776`): a green test
auto-completes the current task, but only when the current task is
*verify-shaped*. It fired in **10 of the 19** green runs — so in the other 9 the
green landed while the model sat on an implementation task it had finished and
never marked done.

**2. No — the post-green `bash` calls are not insurance.** All **10 of 10**
re-run the byte-identical command that produced the green. Zero were a different
command. That is the nudge's own wording doing it: it says "do the work now with
a tool call", and a model with nothing left to do re-proves the same green.

**So the lever sharpens, and it is not "skip the plan".** The defect is that
*finishing a task and recording it are two separate acts, and this model only
does the first*. The existing credit already accepts that for verify tasks; the
question is whether it can safely extend to an implementation task whose files
demonstrably changed while the suite went green. The hard constraint is build
120's headline — **zero false completions** — so any widening has to be gated on
real evidence (a green verify AND landed edits), never on the model's say-so.

**Status: ready to pre-register, queued behind b125.** One lever in flight at a
time; stacking them is how the arm-slot bias went unnoticed for nine sweeps.

### 5.67 pre-registration — build 123, the `occurrence` selector (2026-08-10)

Written and committed BEFORE `b125-occurrence` starts. Every baseline figure
below came out of `grade125.py`, dry-run against **b124-noop's base arm**, which
is byte-identical code to b125's base arm (rule 43 — the numerator and the
denominator, from the grader, off a closed sweep).

**Base (b124 base arm, n=24 runs, 129 post-ambiguous edits, 24/24 runs exposed):**

| outcome of an edit issued after an ambiguous message | |
|---|---|
| LANDED | 33/129 (26%) |
| no-op | 37/129 (29%) |
| came back ambiguous | 59/129 (46%) |
| other error | 0/129 (0%) |
| `fully_fixed` | 11/24 (46%) |

**What changed.** The message no longer asks for a rewrite of `old`. It numbers
the sites and asks for one thing: resend the same call plus `occurrence: N`.
`replace_lines` is gone from this message entirely (it was a competing route to
a demoted tool). See the build-123 commit for why a *third wording* was not
tried: both orderings have been run at n=24 and the model obeys whichever demand
leads, so the lever is removal, not ranking.

**Calls, all pre-registered:**

0. **Exposure parity.** Both arms must put ≥70% as many runs in front of the
   message as the other. If not, every call below is UNGRADED (rule 40) — the
   arms diverged upstream and I am comparing different situations.
1. **The model actually sends it.** ≥50% of post-ambiguous edits in the
   candidate arm carry `occurrence`. This is the call 5.64 taught me to make
   explicitly: a lever that never fires cannot be credited for anything
   downstream, and I have no prior at all for whether a 14B model will use a
   brand-new argument. **I expect this to be the call that fails**, and if it
   does the finding is about argument discoverability, not about the idea.
2. **Landing rate.** 26% → ≥46% (a 20-point rise). This is the outcome that
   matters; the two failure modes below are just where the missing 74% goes.
3. **The repeat loop halves.** "came back ambiguous" 46% → ≤23%. The selector
   attacks this one directly and mechanically: an in-range `occurrence` cannot
   return ambiguous. A miss here means the model is not sending the number.
4. **No new no-ops.** 29% must not get worse by more than 10 points. Build 121
   drove no-ops to zero by destroying the landing rate; the reverse trade is
   just as available and just as worthless.
5. **`fully_fixed` holds** at ≥6/24 (the 5.59 slot expectation for an unchanged
   candidate arm — NOT the base arm's 11, which is the arm-slot bias, not a
   target).

**Revert condition, stated in advance:** if calls **2 and 3 both miss**, revert.
That is exactly the b124 shape — a message the model obeys, into a different
dead end — and one more instance of it means the whole ambiguous-message channel
is exhausted and the next lever has to be somewhere else (5.61 lever 0c).

**Known non-call.** Nothing here tests whether `occurrence` helps a *strong*
model or a human; it is scoped to the one failure this corpus actually has.

### 5.66 b124 verdict — build 121 REVERTED, and the message is asking for one thing too many (2026-08-10)

The pre-registered revert condition (5.65 call 5) fired. Build 121 is out as
build 122 (`62cb10f`).

| | base (b120) | cand (b121) |
|---|---|---|
| post-ambiguous edits | 129 | 72 |
| ...no-ops | 37 (29%) | **0 (0%)** |
| ...came back ambiguous | 59 (46%) | **72 (100%)** |
| ...**landed** | 33 (26%) | **0 (0%)** |
| `fully_fixed` | 11/24 | **0/24** |

`ab.py`: W0/L11/T13, delta −0.229, p=0.001. The score channel is veto-only
(5.27) and this is it exercising the veto.

**Call 1 passed and it did not matter.** The no-op is gone — completely, 37→0 —
and nothing took its place. Read `r3__cand`: the model now makes exactly the
correction we asked for, `if x > 100:` → `if x > 50:`, and sends the two-line
guard as `old`. That guard is byte-identical between the twins, so it matches
twice, so it comes back ambiguous, so it sends it again. 131 of 131 repeats
were fragments. Zero runs fixed anything.

**This is build 119's defect with the halves swapped, and that is the finding.**
119 led with copying and got copying without the correction (29% no-ops). 121
led with the correction and got the correction without the copying (100%
fragments). The message makes two demands — *copy this whole four-line block*
and *change one line inside it* — and this model obeys **whichever one the
sentence leads with, and only that one.** Both orderings have now been run at
n=24 with a clean, opposite, significant result. There is no third ordering.

So **the lever is not wording, and the wording lever is now closed.** What the
data says is that a two-instruction repair message is beyond this model's
budget, and the fix has to *remove* an instruction rather than re-rank them.
The tool already knows the exact text of every candidate block and its line
numbers — the model is being asked to hand back information the tool printed a
moment ago, purely so the tool can look it up again. Options, in order of how
much they shrink the ask:

1. **Let the model name the site instead of quoting it.** The message already
   labels each block ("match at line 3"); accept `old` as-is plus an
   occurrence/line selector, so the only thing the model authors is the change.
   Removes the copying instruction entirely. Needs an API decision (new arg on
   `edit_file` vs. honouring a bare line number) — ask before landing.
2. ~~**Auto-widen a fragment to its unique enclosing block.**~~ **DEAD, and
   killed before it was built.** I wrote this up as "the one to build" and then
   measured it against the 245 archived fragments first. For each one, how many
   of the blocks the message had just offered contain it?

   | contained in | b124 | b121-aa |
   |---|---|---|
   | 0 blocks | 46 | 36 |
   | exactly 1 — *widenable* | **0** | **0** |
   | 2 blocks | 85 | 78 |

   **Zero of 245.** Obvious in hindsight and invisible in prose: the offered
   blocks are windows *around the match*, so the matched fragment is inside
   every one of them by construction. Widening can never disambiguate. (The 82
   zero-hit cases are a different thing — the model has moved on to another
   function pair and hit the same wall there.)
3. **Name the sub-slice in the message** ("you sent 2 of the 4 lines I gave
   you"). Still a two-instruction message, so 5.66 predicts it fails the same
   way. Do not run this one.

**So option 1 is the only one standing**, and b124 makes it much stronger than
it looked: in the candidate arm the model's `new` was already *correct*
(`if x > 100:` → `if x > 50:`). It knew the fix. The only thing it could not do
was point at the right site. An `occurrence` selector converts those edits
directly, and reduces the message to a single instruction — *don't change
anything, just add the number* — which is the one thing this model has proven,
twice at n=24, that it can do.

### 5.65 PRE-REGISTRATION — the no-op rewrite (build 121, written before b124 runs)

Lever 0b, shipped as `dc7b8bb`. Bars set before looking, and this time the
incidence question 5.64 got wrong is handled by grading on the **exposed**
population from the start (rule 40): the denominator is *edits that follow an
ambiguous message*, not runs.

1. **Primary — no-op rate among post-ambiguous edits.** See the amendment
   below: base is **~27–50%**, and the bar is **cand under half the base arm's
   rate in the same sweep, and under 20% absolute**. At or above 0.75× base is
   a miss.

**AMENDMENT, written after building the grader and BEFORE any b124 data
existed** (the sweep was still on its base arm; the corrected numbers come from
re-reading b121-aa and b122, which are closed). *Both figures this lever was
justified with were numerator/denominator mix-ups of mine — the same class of
error as the `cmd`/`command` and `rep`/`repeat` bugs, and the third instance.*

| | claimed | actual (grade124.py) |
|---|---|---|
| b121-aa | "78/132 = 73%" | base **36/132 (27%)**, cand **42/114 (37%)** |
| b122 | "96/96 = 100%" | **48/96 (50%)** in *each* arm |

78 was the no-op count summed over **both** arms divided by **one** arm's edit
count; 96 was the per-arm *edit* count read as if it were the no-op count. The
defect is real and worth the build — a third to a half of the edits a model
makes after this message still do nothing — but it is not the near-total
failure I wrote down, and the "under a third" bar I pre-registered was
**already satisfied by the baseline**, which would have handed build 121 a free
pass. Hence the corrected bar above.

Second: in b121-aa the two arms ran *identical code* and their no-op rates were
27% and 37%. That 10-point spread is the noise floor for this metric, so a
b124 result inside ±10 points means nothing.

**And the third thing is bigger than the lever.** Classifying all 246
post-ambiguous edits in b121-aa by what the tool said *back*:

| | share | n |
|---|---|---|
| **got the ambiguous message AGAIN** | **46.3%** | 114 |
| no-op (`old == new`) | 31.7% | 78 |
| landed | 22.0% | 54 |

**114 of those 114 repeats sent a FRAGMENT of an offered block** — typically the
two-line guard (`if x > 100:` / `return 100`) instead of the four-line block the
message printed and labelled *"these are lines 2-5, copy all 4 of them"*. Not
one sent a whole block and got ambiguity anyway. So the single biggest failure
after this message is **the model taking a sub-slice of exactly the text it was
handed**, and on exec-ambig the sub-slice it takes is the part that is
byte-identical between the twins — the one part that cannot disambiguate
anything. That is a loop, not a mistake: fragment → same message → fragment.

The 5.16-defect-2 "widen the not-found window" item on the backlog is *not* this
— these are not near-misses, they are exact sub-slices. The targeted fix is a
message that recognises the sub-slice and says so ("you sent 2 of the 4 lines I
gave you"), which no current message does. Queued as the next lever.

**RISK TO BUILD 121, recorded before its data exists.** The rewrite leads with
*"decide the ONE line in it that is wrong"*. Given the above, that phrasing
could plausibly make fragment-sending **worse** by focusing attention on a
single line at the exact moment the model has to copy four. So a fifth call:

5. **Fragment rate must not rise.** Base ~46% of post-ambiguous edits come back
   ambiguous again; cand must not exceed base + 10 points (the measured noise
   floor). If it rises past that, build 121 gets **reverted** whatever the no-op
   number does — trading a no-op for an infinite loop is a bad trade.
2. **Lever fires equally in both arms.** Both arms must emit a comparable number
   of ambiguous messages — this message is the trigger, not the treatment, so a
   lopsided exposure means the arms diverged upstream and the primary is
   ungraded rather than won or lost.
3. **`fully_fixed` does not fall,** read against the 5.59 slot bias (identical
   code gives base ~12/24, cand ~6/24) rather than against base directly.
4. **No new syntax refusals.** b97 lost 20 runs to a copy-the-block message; the
   guard is that `old`-not-found and syntax-reject counts do not rise.

DONE rate is *not* a bar here. Build 120 owns that channel now, and both arms
carry it.

### 5.64 b123 verdict — build 120 CREDITED, and the miss is in my bar, not the fix (2026-08-10)

Graded against 5.62, written before the sweep. `ab.py`: −0.062, p=0.58, NO
DETECTABLE DIFFERENCE — expected, and not the result (rule 37).

| call | bar | base | cand | |
|---|---|---|---|---|
| 1 DONE rate | cand ≥ 6/24 | **0/24** | **8/24** | **PASS**, Fisher p=0.0039 |
| 2 lever fires | ≥60% of cand runs | 0/24 | 8/24 (33%) | **MISSED AS WRITTEN** |
| 3 no new grinding | Δmedian ≤ ~2 | 17.0 | 17.0 | PASS |
| 4 fully_fixed holds | must not fall | 11/24 | 8/24 | **raw MISS**, p=0.56 |

**The mechanism is as clean as anything in this archive.** The reprieved runs
and the DONE runs are *the same 8 runs* — 8 reprieved, 8 DONE, 0 reprieved-and-
still-stopped, 0 DONE-without-a-reprieve. All 8 answered the nudge with `bash`
on the very next call, all 8 came back green, and **all 8 were `fully_fixed`.
Zero false completions** — the failure mode that would have killed this outright.
Read one end to end (`r10__cand`): reprieve → nudge → `python3 -m pytest -q` →
`13 passed` → closes out.

**Call 2 is a miss, and the bar was the thing that was wrong.** 60% was a guess
at how often the stale condition *occurs*; it is not a property of the lever. The
actual incidence is 33%, and b121-aa predicted it almost exactly (18/48 = 38%
green-but-killed). Within the population the fix exists for, conversion is 8/8.
Recording it as a miss rather than rewriting the bar (rule 40 exists to stop me
doing that) — but rule 40's "instrument failed, call 1 is UNGRADED" escape does
**not** apply here: it is for a lever that never fired. This one fired, and
converted totally.

**Call 4 is a raw miss that the calibration overturns.** The A/A in 5.59 gave
base 12/24 vs cand 6/24 on *identical* code — a ~2× slot bias against the
candidate. Against that expectation, base 11 and cand 8 is the candidate coming
in **above** its slot, not below its baseline. p=0.56 on the raw pair. So: no
evidence of a fixing regression, and no license to claim an improvement either.
This is the second verdict in a row bent by the slot bias; 5.59's note that it
touches every past A/B on this setup is still unactioned.

Also visible in cand: `fully_fixed` is 8/8 among reprieved runs and **0/16**
among the rest. The reprieve does not cause fixing — it is fired *by* having
finished. What it recovers is the ending.

**Secondary, not pre-registered, so a lead and not a result:** after a reprieved
run goes provably green, it still spends a median of **4 more tool calls** — the
`open plan tasks` nudge sends it back to `update_plan`/`read_file` and a second
identical pytest before it will stop. Worth a look after 0b: the plan gate
outranks proof.

### 5.63 Lever 0b — build 119 teaches the copying and loses the correction (design, 2026-08-10)

The user-reported defect ("edit_file … ✗ This edit does NOTHING: `new` is
identical to `old`") is now reproducible on demand, and build 119's own message
is what produces it.

> **The counts first written here — "96 of 96 in b122, 78 of 132 in b121-aa" —
> are WRONG; see the amendment in 5.65.** The measured rates are 27–37% in
> b121-aa and 50% in b122. The lever survives the correction, its bar did not.

The message currently says:

> Pick the block you meant and copy it VERBATIM into `old` — every line of it,
> with its leading spaces exactly as shown above. **Put that same block in
> `new` with your correction applied to it.**

The load-bearing instruction — *change something* — is a trailing subordinate
clause on a sentence whose main verb is "put that same block in `new`". The
model executes the main clause and drops the modifier, which is exactly the
failure 5.32 describes: these models act on the sentence's SUBJECT, and here the
subject is copying. Verbatim-copying is stressed three times; the correction
once, last, and grammatically demoted.

**The rewrite** (to land once b123 frees the source tree): make the correction
the thing being asked for and copying the mechanism that aims it, and add the
explicit negative these messages otherwise lack —

- lead with *decide the one line that is wrong and what it becomes*;
- then the two fields, `old` verbatim and `new` the same block **with that one
  line rewritten**;
- then a flat prohibition: a block pasted into both fields unchanged is an edit
  that does nothing and will be rejected;
- keep the read-the-whole-block paragraph and the `replace_all` /
  `replace_lines` escape hatches at the tail, per 5.20b's route order.

Graded on the no-op rate among edits that follow an ambiguous message (base
~73%, and the bar is a fall to under a third), with `fully_fixed` as the
non-regression guard. Note this metric is *directly* readable from the event
stream — `no_change` rides on the result event — so unlike 5.62 it does not need
the ending channel.

### 5.62 PRE-REGISTRATION — the stale-reading reprieve (build 120, written before b123 runs)

Lever 0 from 5.61, shipped as build 120 (`54b594b`). The escalated-stall stop
asserts a named test *still fails*; its evidence is the last test run the MODEL
watched, and the model keeps editing after that. In b121-aa all 48 runs ended on
that message and **18 were GREEN** when the grader re-ran pytest. Build 120
tracks `_edits_at_last_verify` and, when the budget expires with edits the last
reading never saw, spends one iteration demanding a current reading before
stopping. One-shot; it re-arms the same budget, so a model that edits and never
verifies still terminates.

**This is a turn-ENDING change, so `ab.py`'s score delta cannot grade it** (rule
37 / 5.27): the grader re-runs pytest, so those 18 runs already scored as fixed.
The score is expected to tie and that tie is *not* the result. Read
`evals/armstats.py` on the events.

Pre-registered, before looking:

1. **Primary — DONE rate.** Base ≈ 0/24 (b121-aa was 0/48). Candidate ≥ 6/24.
   Anything under 4/24 is a miss and build 120 does not get credited on this
   case, whatever else moves.
2. **Lever fires.** The `stall_budget/reprieved` event appears in ≥ 60% of
   candidate runs and in 0 base runs. If it fires in under half, the instrument
   failed (rule 40) and 1 is ungraded rather than negative.
3. **No new grinding.** Candidate median iterations rises by at most ~2 — the
   reprieve is one iteration by construction, and a bigger rise means the
   re-armed budget is buying loops, not readings.
4. **`fully_fixed` does not fall.** The reprieve adds a nudge mid-run and could
   perturb the early branch that decides these runs (5.61). A drop here is a
   regression even if 1 converts.

The arm-slot bias from 5.59 (~2× in the BASE slot's favour on this case) runs
*against* the candidate, so a candidate win is conservative and a candidate loss
is not clean evidence on its own.

### 5.61 b122: the probe failed, and found the bigger bug on the way out (2026-08-10)

**The pre-registered call in 5.60 is UNGRADED.** I predicted POSITION. I got no
data: across 48 runs the model never reached the reordered pairs even once, so
the "buggy listed FIRST" row is empty and neither hypothesis was tested. Not
confirmed, not refuted. The question is still open and needs a design that does
not disturb the run's early branch.

Reordering alone destroyed the case — 0/48 runs made a single real edit, against
18/48 under the v2 ordering, Fisher p = 1.1e-06. Real edits per run are bimodal,
**0 or exactly 3, never in between**, and the 18 runs with 3 are exactly the 18
that fully fixed it. An early branch decides the entire run (the same shape as
the 5.48 call-2/call-3 branch), and moving two function definitions removed the
good branch. v3 is reverted.

**The bigger find: build 119's message is half-obeyed, and the dropped half is
the one that matters.** The message says copy the block VERBATIM into `old`,
then put that same block in `new` *with your correction applied*. The model does
the first clause and drops the second — it copies the block into **both** fields:

    OLD:  """Clamp `x` into the inclusive range [0, 100]."""  ...
    NEW:  """Clamp `x` into the inclusive range [0, 100]."""  ...   (identical)

Rates of applied edits that changed nothing: **96/96 in v3, 78/132 in v2.** This
is exactly the defect reported from real sessions — *"edit_file ✗ This edit does
NOTHING: `new` is identical to `old`"* — and it is now reproduced on demand with
a measured rate. Note the irony to be honest about: build 119 is credited and
deserved it (0/38 vs 25/72), and build 119 also *causes* this, because leaning
hard on "copy VERBATIM" gets a verbatim copy into both fields. The next lever is
to make the message lead with the change and treat the copying as the mechanism,
not the instruction.

**A second, independent product defect: the agent abandons work it has already
finished.** All 48 runs of the v2 A/A ended with the stall message "`limits.py`
still fails — 10 iterations since the repeat was flagged changed nothing", yet
**18 of those 48 were green when check.py re-ran pytest.** The stall detector is
reading the model's last observed pytest, but the model edits after its last
pytest, so the run is judged on a stale observation and killed after it has
succeeded. This is rule 41 — a read point that measures when you last looked
rather than what is true — appearing in the product rather than in my metrics.
It is a plain bug and likely worth more than any prompt wording: up to 18/48
runs here were finished and thrown away.

Both go on the lever list, the stale-stall bug first, since it is a correctness
defect rather than a persuasion problem.

### 5.60 PRE-REGISTRATION — position or reasoning? (written before b122 finished)

b122-v3probe is running: 48 runs of build 119 on exec-ambig v3, which lists the
buggy twin first in two pairs of three. On v2 the model copied the first-listed
block 66% of the time, and that was necessarily also the wrong twin.

Two hypotheses, and they now predict different numbers:

- **POSITION** — the model takes whichever block is listed first. Predicts the
  picked-FIRST rate stays near 66% in BOTH rows, so on buggy-first pairs it
  gets the right answer ~66% of the time by luck.
- **REASONING** — the model is reading the docstring and preferring the twin
  that looks correct. Predicts the picked-BUGGY rate stays near 34% in BOTH
  rows, independent of order.

Called in advance: I expect POSITION, because on v2 the wrong pick was total
(65 of 65 wrong picks were first-listed) and nothing in the message ranks the
blocks. If POSITION holds the fix is cheap — order the blocks, or label them —
and it should recover a large share of the 66%. If REASONING holds, the model
is actively mis-reading the docstrings and the fix is a harder prompt problem.

Recording this before the numbers exist so the outcome cannot be narrated
either way after the fact.

### 5.59 A/A calibration: build 119 is CREDITED, and the noise ran against it (2026-08-10)

The A/A (`--base HEAD --allow-identical`, build 119 on both sides) came back
with a genuine surprise: **identical code scored `fully_fixed` 12/24 in the base
slot and 6/24 in the cand slot.** This design has an arm-slot effect of roughly
2x, and it favours **base** — the arm build 119 was never in.

That is the opposite of the failure mode the gate was written to catch. The
worry was a design that flatters the candidate; what exists flatters the
baseline. So b121-confirm's +7/-0 was measured against a headwind, and the
pre-registered test was conservative rather than generous.

Re-cut by build and by slot, which is precisely what calibration data licenses:

| | build 118 | build 119 |
|---|---|---|
| base slot | 0/38 | 12/24 |
| cand slot | — | 13/48 |

- **Same slot, so no slot bias is possible — build 119 12/24 vs build 118 0/38,
  Fisher p = 1.25e-06.** Both arms in the base worktree, same mechanism, same
  position in the pair. This is the cleanest comparison available and it is not
  close.
- **Build 119 handicapped into the disfavoured slot against build 118 in the
  favoured one — 13/48 vs 0/38, p = 0.000377.** The effect survives a
  deliberately adversarial arrangement.
- The slot bias itself is only p = 0.0689, so it may be noise; it does not
  matter, because every arrangement of the data points the same way.

**Build 119 is credited.** Across four independent arm-slots build 118 solved
exec-ambig 0 times in 38, and build 119 solved it 25 times in 72. The
disambiguation message converts the edit route completely (0 -> 62 wide-block
copies, 70 -> 0 line-number fallbacks) and that conversion turns an unsolvable
case into a solvable one.

**What is NOT established: the magnitude.** Identical code produced 27% and 50%
on the same case, so run-to-run variance here is large and the true rate is
somewhere in a wide band. The existence of the effect is solid; any specific
number is not. Do not quote 29%.

**Rule 42. An A/A can strengthen a result, not only kill one.** If the measured
bias runs against the candidate, a marginal pre-registered p becomes a
conservative one, and builds can then be compared within a single arm slot,
which removes the bias entirely. Run the calibration before deciding what it
would have meant — I fully expected this one to be a retraction.

### 5.58 b121-confirm: the pre-registered bar is cleared — pending A/A (2026-08-10)

The confirmatory r=24 sweep, pre-registered on `fully_fixed` alone, no peeking
and no extension of b121-ambigcase.

**Primary: cand 7/24, base 0/24, Fisher two-sided p = 0.0094.** Clears the 0.05
bar. Attribution guard passes again at 24/24 in both arms, and the mechanism
replicates exactly: base 70 `replace_lines` and 0 wide-block copies, cand 0 and
62, all landing.

Two independent sweeps of the same comparison now exist, so pooling is fair:

| sweep | cand | base | Fisher p |
|---|---|---|---|
| b121-ambigcase | 4/14 | 0/14 | 0.0978 |
| b121-confirm | 7/24 | 0/24 | 0.0094 |
| pooled | **11/38** | **0/38** | **0.00042** |

The baseline has now failed to solve this case **38 times out of 38**. That is
the part worth holding onto: build 119 is not nudging a rate, it is the
difference between a case that is never solved and one solved ~29% of the time.

**Not credited yet.** ab.py returned INCONCLUSIVE on its own endpoint for a
reason unrelated to the p-value: **UNCALIBRATED** — no A/A has measured this
setup's noise floor. The gate is not pedantry here; its message cites a prior
sweep of this project that read +0.375 at p=0.031 while an A/A of the same
setup returned +0.281. A paired design that flatters the candidate arm with
identical code on both sides would produce exactly the numbers above. An A/A at
`--base HEAD --allow-identical`, same case/model/r, is running now, and the
credit decision waits on it. If the A/A shows a materially non-zero delta, the
pooled p is worthless and this gets recorded as another b120.

**The selection failure replicates and is now significant.** Pooled over both
sweeps the model picked the wrong twin **65 times against 33** — 66% wrong,
binomial p = 0.0016 against chance. It is not fumbling at random; it reliably
rewrites a function that was already passing. But the confound is still total:
all 65 wrong picks were also the first-listed block, because v2 always lists
the correct twin first. Whether this is "takes the first block" or "takes the
block that looks correct" is unresolved and v3 answers it.

### 5.57 b121: the lever fires, the route converts totally, the outcome is underpowered (2026-08-10)

First A/B in this whole line of work where the **attribution guard passes**:
the ambiguous-match message fired in **14/14 runs of both arms**. b120's lever
fired in 1 of 14. The instrument built in 5.56 does what it was built to do.

**The mechanism is not in doubt.** Counting what the model does on the very
next call after an ambiguous message, across 28 runs:

| next call after the message | base (b118) | cand (b119) |
|---|---|---|
| `replace_lines` (line-number fallback) | 41 | 0 |
| wide unique block copied, edit lands | 0 | 36 |
| read/bash instead | 14 | 10 |

That is a total route conversion, 0→36 against 41→0, and every one of those 36
copies landed cleanly. Build 119 produces exactly the behaviour it was written
to produce. Rule 38 says grade a steer by what it converts TO, and this steer
converts to the intended thing.

**The outcome does not clear its pre-registered bar.** Primary endpoint,
`fully_fixed`: **cand 4/14, base 0/14, Fisher two-sided p = 0.0978.** The
direction is clean — ab.py scored it W4/L0/T10, the candidate never lost a
single pair, and the baseline solved this case *zero* times out of fourteen —
but 0.0978 is not < 0.05 and the pre-registration does not bend. **Not
credited.** ab.py independently computed the design needs ~21 runs/arm; a
confirmatory sweep at r=24 is running, pre-registered on `fully_fixed` alone,
no peeking and no extension of this one.

**The secondary endpoint was invalid and is withdrawn.** It read the failing-
test count from the last pytest the model ran, and reported the candidate 0.36
tests *worse* — the one result pointing against build 119. It is an artifact.
Runs that edit after their final pytest: **cand 14/14, base 0/14.** The metric
scored the candidate mid-repair and the baseline at rest, in every pair. It
measures when each arm last looked, not what each arm left behind. check.py
re-runs pytest against the final tree, so the score already is the unbiased
form of that endpoint.

**Rule 41. A metric read from the model's own self-check measures when the arm
last looked, not what it left.** Before differencing such a metric across arms,
check that the read point is arm-independent — here it was 14/14 against 0/14.
Sibling of rule 37: both are predicates that quietly mean something different
per arm, and both produced a confident number pointing the wrong way.

**The next failure is already visible, and it is selection, not disambiguation.**
Of the 36 wide-block copies, **24 rewrote the CORRECT twin** and only 12 the
buggy one. Build 119 successfully teaches "copy the whole unique block" and the
model then applies it to the wrong function — which is actively destructive,
since it edits a passing function. That is the best available explanation for
why total route conversion yields only 4/14 finishes.

Caveat on that finding, and it is my own design fault: in v2 the correct twin
is always listed **first**, so "picked the first block" and "picked the correct
twin" are perfectly confounded — all 24 wrong picks were also first-listed. v3
is drafted (buggy twin first in two of three pairs) to decouple them, and will
land only after the confirmatory sweep, since the case must stay frozen while
it runs.

### 5.56 The ambiguity was never there: the model edits at guard granularity (2026-08-10)

5.55 ended by building `exec-ambig`, a case whose bugs sit on duplicated lines
so the ambiguous-match message would fire in nearly every run — the instrument
b120 lacked. Before spending 2.5h on a full sweep I ran a paired r=1 pilot with
one question: *does the message actually fire?*

It fired **zero times, in both arms.** Both runs finished VERIFIED in nine
calls and three clean edits. `ab.py` returned UNDERPOWERED and warned the case
might be saturated, but saturation was not the story — the event log was:

    edit_file old='if x > 255:\n    return 100' new='if x > 255:\n    return 255'
    edit_file old='if x > 1:\n    return 0'     new='if x > 1:\n    return 1'
    edit_file old='if x > 127:\n    return 128' new='if x > 127:\n    return 127'

Six edits across two arms, every one anchored on the **enclosing guard**. I had
duplicated `return 100` and `return 0`; the model never sent either of those as
`old`. It sends the guard line plus its return — and that two-line block was
unique, so the ambiguity the case was built around did not exist.

The generalisable point is not about this case. **Ambiguity is a property of
the string the model actually sends, and the model chooses that string at a
granularity of its own.** A case that duplicates the line that is *wrong* tests
nothing if the model habitually copies the line above it too. This is also a
partial second explanation for b120: ambiguous matches are rarer in practice
than the file's duplicate-line count suggests, because anchoring on control
flow disambiguates for free. Worth noting that this is the model doing
something *sensible* — the failure was in my instrument, not its behaviour.

Rebuilt as v2 (`bcefdd2`): six functions in three pairs, each buggy function's
whole body byte-identical to its correct twin's, distinguished only by a
one-line docstring naming the real range. Now the bare return, the guard plus
return, and the entire body each occur exactly 2x, so no anchor choice escapes
ambiguity, and `_unique_window` must widen out to the docstring — which is
exactly the copy-the-wider-block behaviour build 119 is meant to teach. Guards
verified before landing: seed 4 failed / 9 passed, targeted fixes 13/13,
`replace_all` fixes the buggy twin, breaks the correct one, stays at 4 failed.

Cost of the pilot: ~10 minutes, against the 2.5h sweep it would have wasted —
and a v1 result would have read as "the message doesn't help," which is a
conclusion about the message that the run contained no evidence for.

**Rule 40. Pilot a new instrument for whether the LEVER FIRES, not for whether
the metric moves.** An r=1 pilot cannot measure an effect, but it can prove the
mechanism is reachable, and that is the failure mode that wastes whole sweeps.

### 5.55 b120: the metric moved, the lever didn't fire, do not credit it (2026-08-10)

Graded by `grade120.py`, written and validated against b119 *before* the sweep
finished (it reproduces b119's 7/14 vs 0/14 at p=0.0058 exactly).

**Primary looks like a big win and is not one.** VERIFIED base 2/14 → cand
9/14, Fisher p=0.018. `ab.py` itself returned **INCONCLUSIVE**: delta +0.25
clears the noise floor 0.1429 but falls short of the 0.4287 required at k=1
A/A calibration.

Then the branch mix, per rule 36:

| arm | edit-first | read-first | ambiguous messages | VERIFIED |
|---|---:|---:|---:|---:|
| base | 8 | **6** | 12 | 2/14 |
| cand | 13 | **1** | **3** | 9/14 |

**The lever fired in one of fourteen candidate runs, and that run lost.** Zero
edit-first runs saw an ambiguous message in either arm — 5.52's perfect
collinearity holds exactly. So the entire VERIFIED gap lives in the branch
draw, and the branch **cannot** be caused by this change: it is fixed at tool
call 3, the message cannot fire before call 4, and the prior context is
byte-identical between arms (5.48). Inside the edit-first stratum — runs that
never see the message at all — base 2/8 vs cand 9/13, p=0.08: not significant,
and not attributable to the change in either direction.

By the pre-registered rule in 5.53: *primary up + mechanism flat ⇒ the message
is not what moved it, and I will say so.* Mechanism was worse than flat — it
was **unevaluable**, at n=1. Saying so. **Not credited.**

Guardrail clean (0 syntax rejections both arms) but on 3 events, so it has no
power to detect the b97 regression either.

**What the one firing run actually shows** — worth more than the headline. The
message rendered exactly as designed, with the buggy line sitting in the block:

```
  ── match at line 24 — these are lines 23-25, copy all 3 of them ──
            current = [word]
            current_len = len(word)
        elif current_len + 1 + len(word) < width:
```

The model did not copy the block. Across every ambiguous event in the sweep:

| route taken next | base (b118) | cand (b119) |
|---|---:|---:|
| `replace_lines` | 11/12 | 0 |
| `edit_file` + `replace_all` | 0 | **3/3** |
| **copy the block verbatim — the route promoted to FIRST** | — | **0/3** |

**This refutes build 98's rule.** "The model takes whichever route is named
first" held when the first route was cheap (b118: replace_lines first, taken
11/12). It fails here: copy-a-3-line-block-with-exact-leading-whitespace is
first and is taken zero times, and the model falls to `replace_all` — which is
second in my message, and which it can satisfy by adding one boolean.

Corrected rule: **position selects among the routes the model can afford;
an unaffordable route is skipped regardless of position.** Both routes ever
observed being taken — `replace_lines` and `replace_all` — are near-zero
effort. Reordering a high-effort route to the top does not get it taken; the
cheap alternatives have to be *removed* from the menu. n=3 in cand, but
consistent with all 42 events in 5.28 and all 12 in base here.

And `replace_all` is the worse outcome: it applied the model's wrong one-line
fix to **both** sites instead of one.

**Do not iterate the wording tonight.** The instrument cannot resolve this
lever: it fires in the minority branch, and the branch draw (6 vs 1 here)
dominates the metric it would be graded on. That is 5.48 and rule 36 in their
sharpest form yet — **an A/B cannot grade a lever whose population is itself
the dominant source of outcome variance.** The fix is an instrument where the
ambiguous message fires on nearly every run, not more r14 sweeps on this one.

Build 119 is left in place: unvalidated but not condemned, and the gutter
removal stands on 5.28's own reasoning independent of the route order.

### 5.54 The archive cannot license 5.52, because both powered cases are rigged for it (2026-08-09)

Run while b120 swept, read-only. 5.52 flagged its own threat to validity —
exec-bugfix seeds 2 one-line bugs and 1 needing a block re-derivation, so it
rewards block rewriting by construction. The obvious cheap check is whether the
big-edits-win association replicates on the other cases already in the archive.
It does, spectacularly, and the replication is **worthless**.

Association between per-run median `new` payload and VERIFIED, every case with
usable event data:

| case | runs | verified | median payload, verified | lost | |
|---|---:|---:|---:|---:|---|
| exec-bugfix | 597 | 205 | 96 | 70 | 1.4x |
| **exec-stall-trap** | **77** | **50** | **394** | **56** | **7.0x**, Mann-Whitney p=2.4e-8 |
| exec-from-plan | 45 | 41 | 355 | 412 | 0.9x — 4 losers, no power |
| e2e-spec-to-code | 45 | 1 | 958 | 411 | 1 winner, no power |
| syntax-fix | 68 | — | — | — | 68/68 never run a test; ungradable here |

exec-stall-trap looks like a clean independent replication at n=77 and p=2.4e-8,
and the 5.48 branch effect reappears inside it untouched (edit-first 42/53
verified, read-first 3/13). Then read its `case.json`: *"structural control-flow
bug that baits naive text-swap edit loops."* **It was purpose-built to punish
surgical editing.** It is not independent evidence for 5.52; it is the same
confound, deliberately engineered instead of accidental.

So both cases with the power to answer the question are rigged for the answer,
one by accident and one on purpose. The three that are not rigged have no power
— and the only one of them that discriminates at all, exec-from-plan, points
mildly at **null** (0.9x), though on 4 losers that is worth nothing either.

**5.52's generalization stays withdrawn.** Nothing in 648 archived exec-bugfix
runs plus 359 others can license it. That is the whole finding here, and it is
worth more than the p=2.4e-8 would have been.

**Built tonight: `exec-pinpoint`, the missing instrument.** The structural
mirror of exec-bugfix — same 13 tests, same 3 failures, same check.py, same
prompt, one difference: every bug is a genuine single-line, single-token fix
that the failing traceback names directly.

- `median` — `ordered[n // 2 + 1]` should be `ordered[n // 2 - 1]`
- `clamp` — `min(x, hi + 1)` should be `min(x, hi)`
- `top_n` — `(kv[1], kv[0])` should be `(-kv[1], kv[0])`

Verified before landing: seeds at exactly 3 failed / 10 passed, and the three
substitutions are each unique in the file and take it to 13/13. Nothing here is
easier to fix by rewriting a whole function, so surgical mode is not
handicapped by construction. If surgical editing still loses on exec-pinpoint,
5.52 is about the strategy and generalizes. If it does not, 5.52 is about
exec-bugfix's bug shapes and the finding shrinks to one case. Either way it is
the first honest test, and it also gives every future message lever a second
instrument — 5.17b's complaint that "the exec-bugfix r8 instrument cannot
resolve it" has been standing since build 97.

Staged in scratch, not committed: landing files mid-sweep would change the
candidate arm's working tree under b120. Lands when b120 finishes.

### 5.53 Build 119 — the ambiguous message now prescribes the winning strategy (2026-08-09, pre-registered)

5.52's lever, built. The ambiguous-match message fires on **100% of the losing
branch** (37/37 read-first runs, 0/117 edit-first) and is the only harness
intervention point inside it. It currently prescribes single-line surgery
verbatim — *"passing only the replacement line as `new`"* — and explicitly
argues against the alternative: *"extending `old` … is where this edit usually
breaks."* That is the losing strategy, recommended, at the moment the model is
most receptive.

**The change (two halves that cannot be separated, deliberately).**

1. **Route order reversed.** Copy-the-block-verbatim is now first;
   `replace_lines` is demoted to the can't-reproduce-the-characters fallback;
   `replace_all` unchanged. Plus one new sentence aimed at 5.52's measured
   failure — *"the line you need to change is often one of THEM rather than
   the line you first picked"* — because the losing runs mis-localize to a
   plausible line one below the defect.
2. **The `NN |` gutter and `>` marker are gone**; blocks render verbatim and
   unnumbered like `_not_found_help`, line numbers stated in the header prose.

Half 2 is the precondition 5.28 wrote down for half 1: *"if the extend route is
ever promoted again, the gutter has to go first."* b97 promoted extend **with**
the gutter, the model stripped `NN |`, lost the indentation with it, and 8 of
its 20 syntax rejections came off this message. So this **knowingly violates
rule 28** (never bundle a behaviour change into a wording sweep), and the
justification is that the two are not independently shippable: promoting extend
with the gutter present is the known-catastrophic b97 configuration, and
removing the gutter without promoting extend changes nothing, since nothing
currently takes that route. If the sweep loses I cannot attribute which half
lost — accepted, and recorded here in advance.

**New: the uniqueness guarantee.** The message now claims each block "occurs
exactly ONCE in the file", so `_unique_window` widens any site whose ±1 window
is not yet unique (ceiling 4, only for the sites that need it — build 90
measured a wide window actively harmful, so the common case stays at ~12
lines). Advice the model follows has to be true, or it fails ambiguously twice.

**Mechanism, and why this case may flatter it.** For the exact `old` the losing
runs actually send, the ±1 window contains the bug:

```
  ── match at line 24 — these are lines 23-25, copy all 3 of them ──
            current = [word]
            current_len = len(word)
        elif current_len + 1 + len(word) < width:
```

That third line is the defect (`<` should be `<=`). Copying the block into
`old` and its corrected form into `new` puts the operator in the model's own
output. Strong mechanism here — and exactly the reason it may not generalize,
since nothing guarantees a real bug sits within one line of the ambiguous
match. Filed as case-favorable in advance.

**Pre-registered predictions**, graded on `b120-ambigblock`, r14, paired, base
= build 118:

- **Primary:** VERIFIED rate up. Read-first runs are 1/37 (3%) and the branch
  is ~24% of runs, so the ceiling on this case is ~+18pp; I predict a smaller
  real effect and would call **any** significant gain a win.
- **Mechanism (the honest test):** among read-first runs, median `new` payload
  rises off 70 chars, and the `<= width` write rate rises off 14%. **If VERIFIED
  moves but these do not, the message is not what moved it** and I will say so.
- **Guardrail (the b97 failure mode):** syntax rejections downstream of an
  ambiguous message must stay at **0**. It has been 0 across 31+ events since
  build 98. Any regression here condemns the change regardless of the primary.
- **Null is live.** The mode is chosen before the message arrives; the message
  never created it. This tests only whether the message can *break* it.
- Per 5.45's standing habit fix: a 2-run VERIFIED gap at r14 is **not** a
  result. It needs the mechanism numbers to move with it.

### 5.52 What kills read-first runs: they never make a big edit (2026-08-09)

Answering "investigate what kills read-first runs" — the 5.48 branch, where the
run's 3rd tool call is `read_file` (1/37 verified) rather than `edit_file`
(90/117). Population: the guard-on runs of b110/b111/b113/b115/b116 plus
b119's base arm, n=154.

**"Read-first" is a misnomer. The branch is an edit-granularity mode that the
model locks into at call 3 and never leaves for the rest of the run.**

| | median `new` | p90 | **largest edit in the entire branch** |
|---|---|---|---|
| edit-first (117 runs, 1087 edits) | 673 ch | 2019 | 2020 |
| read-first (37 runs, 346 edits) | **70 ch** | **79** | **537** |

Across 346 edits in 37 runs, the read-first branch emits exactly one payload
above 79 characters. It is doing single-line surgery, exclusively, start to
finish. The edit-first branch rewrites whole function blocks.

That decides the case, because of how the three seeded bugs are shaped:

| seeded bug | shape | edit-first | read-first |
|---|---|---|---|
| `truncate` `- len(suffix) + 1` | drop a `+ 1` — **one line** | fixed | **100%** |
| `title_case` `append(lower)` | add `.capitalize()` — **one line** | 100% | **100%** |
| `word_wrap` `... < width` | `<` → `<=` — **an operator the model must first localize** | **84%** | **14%** |

Surgical mode fixes both one-line bugs in every single run, then stalls forever
on the operator. Median best-reached failure count is exactly 1 (vs 0), and
**36/36 of the budget-killed read-first runs end still failing
`test_word_wrap_exact_fit_stays_on_one_line`** — the same test, every time.
Edit-first hits that wall in 15/117.

Writing `<= width` is very nearly necessary and sufficient: **1/51** runs that
never wrote it verified; **90/103** that did. A block rewrite gets it for free —
re-deriving the function from its docstring forces the model past the
comparison. Surgical mode never rewrites the region the operator lives in, so
it never re-reads the contract, so it mis-localizes: the losing runs patch
`current_len = len(word)` → `+ 1`, then `+ 1 if current else len(word)`,
plausible-looking arithmetic one line below the actual defect.

Both branches are **100% monotone-down** in failure count. Nothing here is
thrashing or regression — read-first runs are stuck, not confused.

**Four things I was wrong about, in order.**

1. *The `NN |` gutter contaminates the next edit* (5.28 recorded it collapsing
   b97). Measured: **0/76**. The gutter is never once copied into a subsequent
   `old`/`new`.
2. *The multi-match message fails.* It does not: **76/76** multi-match messages
   are answered by a `replace_lines` that succeeds. It is, locally, the
   best-performing message in the system, exactly as 5.28 said.
3. *Read-first runs never verify.* Extractor bug of mine — bash args are keyed
   `cmd`, not `command`, so my first pass reported "36/36 never ran pytest."
   False. Every run in both arms runs pytest; read-first runs it **more**
   (median 6 vs 4).
4. *5.49-style prediction failure again.* I expected the killer to be a message
   defect. It is a strategy the message never touches.

**Rule 31, sharper.** 5.28 graded this message on next-call conversion (32/42)
and called it the system's best. Both facts hold at n=76 — and it is the losing
branch's message. A steer can convert perfectly and still be worse than
neutral, because what it converts *to* is the losing strategy: the message
ends with "pick the match you want and call `replace_lines` … passing only the
replacement line as `new`," i.e. it explicitly prescribes single-line surgery
to a model that is already trapped in single-line surgery.

**Collinearity (5.48's caveat, unresolved).** Multi-match and the branch are
perfectly collinear: 37/37 read-first runs see it, 0/117 edit-first runs do.
The model picks a 31-char `old` (`lines.append(" ".join(current))`, which
occurs twice) versus a 536-char block — so the mode *causes* the message, and
message and mode cannot be separated in observational data. The message did
not create the mode. The open question is only whether it can **break** it.

**Threat to validity, stated plainly.** This is one case with 2 of 3 bugs
shaped as one-line fixes and 1 shaped as a block re-derivation. That is a
design that rewards block rewriting by construction. The finding "surgical mode
loses" may be partly "this case's hard bug happens to punish surgical mode." A
second case whose hard bug *is* a one-liner would separate them, and until one
exists I will not generalize this beyond `exec-bugfix`.

**Lever (not yet built, not yet sized).** The multi-match message is the only
harness intervention point that fires inside the losing mode, at 100% of it.
Rewrite it to prescribe widening — "`old` is not unique; extend it with
surrounding lines until it matches once" plus a wide raw window — instead of
prescribing the single-line `replace_lines`. Per b119's lesson the population
is checked first and it is real (37/154 = 24%, and 100% of the losing branch);
the mechanism is not, since the model is in surgical mode before the message
arrives. A/B it before believing it.

### 5.51 The read guard is load-bearing — and not for the reason it was built (2026-08-09)

`b119-readguard`, the probe pre-registered in 5.49. Base = guard on, candidate =
`require_read_before_edit = False`, one behavioural line, r14.

| | VERIFIED | mean iters | mean landed edits |
|---|---|---|---|
| base (guard **on**) | **7 / 14** | 23.6 | 5.1 |
| cand (guard **off**) | **0 / 14** | 33.3 | 8.4 |

Fisher **p = 0.0058**; every candidate run stopped. Conditioned on the edit-first
opening — the tightest comparison available, since it removes 5.48's branch —
base goes **7/11** and candidate **0/9**, **p = 0.0047**. This is the largest
effect ever measured in this project, and the first measured value of the guard
build 93 shipped on an argument rather than a number.

**The mechanism check refuted my own framing of it.** 5.49 predicted that with
the guard off "its call-2 edit now lands, unaimed". It does not land. The
attempted edit at call 2 is **byte-identical in both arms** (one payload hash,
11 base / 9 cand) and **fails in both**. The guard was never preventing a good
edit from applying, and the forced read is not what earns the 7 runs. The only
thing that differs is *which rejection the model reads*:

> **guard on:** You have NOT read `textkit.py` yet, so you cannot know the exact
> text it contains. Call read_file on it FIRST, then copy `old` verbatim… Do not
> reconstruct the code from a traceback, from the tests, or from memory.

> **guard off:** `old` not found in `textkit.py` (84 lines). Easiest fix:
> `replace_lines` with start=15, end=32 — it targets lines 15-32 by NUMBER, so
> there is no `old` to reproduce.

The second message recommends **blind line-number surgery to a model that has
never seen the file**. It takes it: the candidate lands 8.4 edits per run to
base's 5.1, runs 33.3 iterations to base's 23.6, and reaches its first no-op
re-send at iteration 13 instead of 22. More edits, applied faster, all wrong.

So the guard's real job is **triage of the first failure**. Both arms make the
same mistake; one is told to go look at the file, the other is handed a
power tool and pointed at coordinates it cannot check. That reframes 5.48: the
third call correlates with the outcome because of what the *harness says* in
response to it, which is a lever, unlike the model's choice of branch.

**The obvious follow-up is closed before building it.** If the not-found message
is harmful to a model working from an unverified view, make it read-aware —
recommend `read_file` when the file is unread and `replace_lines` only when it
is not. There is no population for that fix: across b110–b116 with the guard on,
**115 of 115** not-found messages that recommended `replace_lines` went to a run
that had already read the file. The guard intercepts that state completely, so
the message is only ever dangerous in a configuration we do not ship. Do not
build it (methodology 17: a lever that cannot fire is not a lever).

**Disposition: `require_read_before_edit` stays ON, default unchanged, the probe
flag reverted.** Honouring 5.49's caveat: the flag governs every edit in the
run, not only the third call, so some of the collapse is downstream blind
editing rather than the first message alone. The identical call-2 payload and
the conditioned 7/11-vs-0/9 localize where the arms first diverge; they do not
prove the first message carries the whole 7 runs, and I am not claiming it does.

**Still unexplained:** read-first runs went 0/3 in base and 0/5 in candidate.
Whatever kills them is not the guard — it is untouched by this probe.

### 5.50 Recalibrating both budgets found nothing to recalibrate from (2026-08-09)

`escalated_stall_budget = 8` and `noop_resend_stall_budget = 10` were each fitted
to a single run (`b113 exec-bugfix r8 base`), which 5.44 flagged as the weakest
thing about build 116. Pooling the 84 VERIFIED runs across b110–b116 to replace
that n=1 with a distribution:

| | winners reaching it | survivor lead-time to green |
|---|---|---|
| escalated steer | **1 / 84** | 7 iterations |
| no-op re-send | **1 / 84** | 8 iterations |

Both are the *same run* — `b113 r8` again. Eighty-four winners over five sweeps
produced **zero** new survivors, so there is still exactly one sample per
trigger, and it still says K=8 and K=10 clear it (by 1 and 2 iterations).

**Disposition: both K's unchanged.** Not because the pool confirmed them — it
did not add a single data point — but because lowering a budget requires
evidence that the one survivor was noise, and 84 runs produced no evidence
either way. What the pool *does* establish is different and more useful than a
tuned constant: **a winner essentially never reaches these triggers.** The
budgets are not a trade-off between speed and success on this case; they are
almost pure waste-cutting, which is what 5.46 credited them with.

**And a correction to 5.46's invariant.** "Every VERIFIED run takes exactly 23
iterations" was scoped to b115+b116, where it holds. Pooled over b110–b116 it is
80 / 84:

| iterations | winners |
|---|---|
| 23 | 80 |
| 25 | 2 |
| 31 | 2 |

The claim should have been "95% of winners finish at 23, and the only ones that
run long are the ones that stalled and recovered". The exceptions are exactly
the population the budgets govern, which is the part that mattered, but the
absolute version overstated it and would have made any 24-iteration winner look
impossible instead of rare.

### 5.49 Probe: is the read guard load-bearing, or just a marker? (2026-08-09, pre-registered)

5.48 cannot tell cause from correlation because the model picks the branch. But
the *other* half of that branch is mine: `require_read_before_edit` is a config
flag, and it is what rejects the third call in **106 of 106** edit-first runs
across b110–b116 — a tight predicate, not a family of messages. So the guard can
be intervened on even though the branch cannot.

**Sweep `b119-readguard`:** base = `1b90642` (guard on), candidate = the same
tree with `require_read_before_edit = False`. `exec-bugfix`, `qwencoder14`,
r14. Per methodology 28 the flag is the only difference; nothing else moves.

**Predictions, fixed now.**

- *If the block is load-bearing* (the rejection is what forces the read that
  makes the fix aimable), the candidate should fall sharply — its call-2 edit
  now lands, unaimed, and no read is forced. Expect it to drift toward the
  read-first rate rather than the 78% edit-first rate.
- *If the branch is only a marker* (the sample that reaches for an edit was
  going to win regardless), the candidate should be flat within the ±2 floor.

**Read the mechanism, not just the rate.** Whatever VERIFIED does, check three
things in the candidate arm, because they distinguish the readings even if the
rate is ambiguous: does the call-2 edit actually apply; does the run ever read
the file at all afterwards; and does the no-op re-send of 5.44 arrive earlier.

**Interpretation caveat, stated in advance.** Turning the flag off changes every
edit in the run, not only the third call, so a candidate collapse would show the
guard is load-bearing *somewhere* without proving it is load-bearing *at call 2*.
That is a weaker claim than 5.48's, and I will not report it as the stronger one.
This is a probe. Neither arm is a proposed default: the guard stays on unless
this says otherwise, and it will not be turned off on a null result.

### 5.48 One coin flip at call 2 decides the run, and it decides the sweep (2026-08-09)

Within the post-restart era of 5.47 (b110–b116, 140 runs, one server process),
runs split at their **third tool call** and never recover from the split:

| 3rd tool call | outcome | VERIFIED | share of runs |
|---|---|---|---|
| `edit_file` | rejected — "You have NOT read … yet" | **83 / 106 = 78%** | 76% |
| `read_file` | succeeds | **1 / 34 = 3%** | 24% |

Fisher two-sided **p = 6.7e-16**. The winning path goes *through* a blocked
edit: `require_read_before_edit` refuses the edit, the model reads, then fixes
it. The runs that read the file of their own accord at that same point lose
almost every time.

**The context before the split is byte-identical.** All 140 runs open with the
same `bash` call, get the same pytest output (one md5 across every run in every
sweep since b102), and write the same `update_plan` — 233 characters, one hash,
28 out of 28, in both branches. There is no earlier state difference to point
at. Both branches then read the whole file; neither windows the read, so this is
not 5.41 wearing a new hat.

**This is the variance the endings channel has been fighting.** The branch mix
is drawn fresh per arm, and it tracks the arm's score almost exactly:

| sweep | base read-first | base VERIFIED | cand read-first | cand VERIFIED |
|---|---|---|---|---|
| b115 | 4 / 14 | 9 / 14 | 1 / 14 | 11 / 14 |
| b116 | 6 / 14 | 4 / 14 | 1 / 14 | 11 / 14 |

b116's headline gap — base 4/14 vs cand 11/14, p = 0.021 — sits on top of a
read-first draw of 6 versus 1 (p = 0.077) in arms whose only code difference is
a stall cap that **cannot fire before iteration 24**. The lever is downstream of
a split that has already decided the run. This is the mechanism behind 5.46's
conclusion, and it is why a 2-run VERIFIED gap at r14 is worth nothing.

**What this is not.** The branch is chosen by the model, not assigned by me, so
"blocking the edit causes the win" is one reading and "the sample that reaches
for the edit was going to win anyway" is the other, and no amount of staring at
these logs separates them. The pre-restart era argues for caution: there, *all*
28 runs were read-first and build 108's candidate arm still went 7/14, so
reading first is not intrinsically fatal — it is fatal in this process. Do not
turn this into a "force an edit first" lever without a randomized test.

**What to do with it now, which is free:** report the branch mix beside VERIFIED
in every sweep. It costs nothing and it converts b116's exciting-looking table
into the honest statement that one arm drew five more losing openings.

### 5.47 The 25%→71% was a server restart, and I emailed it as progress (2026-08-09)

**Retracting the headline of 5.45.** That section reported build 109 at 7/28 =
25% against build 114 at 20/28 = 71%, Fisher p = 1.1e-3, and called it the
project's first significant progress measurement. It compares two sweeps that
ran against **two different server processes**, and the restart alone accounts
for the jump.

The mlx-server log records every start. Laid against the sweeps:

| when | what |
|---|---|
| Aug 7 19:42 | server starts (Qwen2.5-Coder-14B-Instruct-4bit) |
| Aug 8 01:08 → 07:27 | b102, b106, b107, **b108** run here |
| Aug 8 16:08, **16:18** | server restarted twice; pid 69851 is still up |
| Aug 8 19:05 → Aug 9 05:14 | **b110**, b111, b113, b115, b116 run here |

Everything a sweep pins was held fixed across that line: same case fixture
(untouched since build 78), same 284-char prompt, same alias, same resolved
model id on both sides of the restart, `temperature = 0.3` unchanged since the
repo's first commit, same deterministic first pytest output. And the only
product change between build 108 and b110's base ref is `locode/__init__.py` —
the build number. Every other commit in the window is docs.

What moved anyway:

| | pre-restart (b102–b108) | post-restart (b110–b116) |
|---|---|---|
| distinct plans at call 1 | 3 | **1** (233 chars, 140/140 identical) |
| 3rd call is `read_file` | 100% | 24% |
| VERIFIED per arm | 0–7 / 14 | 4–11 / 14 |

A restart with identical weights should not do that. The most likely mechanism
is the prompt cache (`--prompt-cache-size 4 --prompt-cache-bytes 1610612736` on
the current process): a warm KV cache makes the shared prefix numerically
identical run to run, which is exactly the shape of the observation — one plan
instead of three. I cannot confirm the old process's flags; the log does not
record argv. That uncertainty does not matter for the retraction, because the
confound is established by the timing and the elimination of every alternative,
not by the mechanism.

**What survives.** Every *paired* verdict — b110, b111, b113, b115, b116 — is
untouched, because both arms interleave against one live process, and that is
precisely the property paired same-session A/B was built for. 5.33's build-108
result (0/14 → 7/14) is within a single sweep and stands. What dies is every
cross-sweep comparison of absolute rates that spans Aug 8 16:08: 5.45's
headline, and any future sentence of the form "build N was X%, build N+5 is Y%".

**The fix, landed in build 117.** `server_fingerprint()` reads the server's pid,
start time, model and full argv from `ps` and `ab.py` stamps it into every
`ab.json`; a sweep that starts against a different process than the newest
recorded one prints a loud warning that its absolute rate is not comparable
backwards. Nine tests in `tests/test_harness.py` pin the parse, including the
recycled-pid case (identity is pid **and** start time) and the rule that an
unknowable comparison returns None rather than a false alarm. The archived
`ab.json` files are deliberately **not** backfilled: I inferred their servers
from a log, and inferred data written into a results file is indistinguishable
from measured data six weeks later.

**Methodology.** New rule 35: *an A/B pins the code; only the paired design pins
the machine. Before comparing an absolute rate across two sweeps, prove they
ran on the same server process.* This is rule 24 (the population changed) with
a part of the population I did not know was a variable. A correction has gone
out by email, since the 25%→71% number was reported as a result.

### 5.46 b116: the cap works, and VERIFIED was never the metric for it (2026-08-09)

`b116-noopcap` (r14, base `a6f28f7` = build 115, so the only delta is the no-op
trigger and its telemetry). The headline numbers look spectacular and **none of
them may be credited to the change** — for a reason that finally makes the whole
lever family gradeable.

**1. It fires, and it is now visible.** Cand armed the budget three times: the
escalated steer twice (iteration 27, both runs ended at 34, inside K=8, so no
stop) and the no-op re-send once (iteration 12, K=10, expired and ended the turn
at 24). Base, which is build 115, hit its own cap **five** times. Build 115 is
no longer untested — it just had to be a *baseline* before it fired.

**2. Zero winners cut.** All six cap-stops landed at iterations 24-29. That is
past the window in which this case is ever won, which brings us to the finding
that reframes everything:

**3. Every VERIFIED run in this project takes exactly 23 iterations.** Across
b115 and b116, all four arms, 35 successful runs: `23, 23, 23, … 23`. Not a
median — the complete list. `exec-bugfix` has one deterministic winning path and
a run either falls into it or it does not. **A winner never arms the budget at
all**, because it never repeats a failure three times and never sends a no-op.
So the stall cap is structurally incapable of moving VERIFIED, in either
direction, and grading it on VERIFIED was a category error I built into my own
criteria in 5.43.

**4. Which makes the arm gap noise, and a warning.** Base 4/14 vs cand 11/14 —
a difference of seven — from a mechanism that fired once in cand. The causality
runs backwards: stuck runs cause firings, firings do not cause stuck runs. A run
is a ~54% coin flip between the 23-iteration win and a doomed grind, so a 14-run
arm has a standard error near 1.9 and a 4-vs-11 split is roughly a 2% draw. I
have run 28 sweeps; 2% events are due. Worse, 5.45's "floor is ±2" was itself an
underestimate from two A/A samples, and all three A/A sweeps on record lean the
same way (`b94-AA` +3, `b94-AA2` +1, `aa14-calib` +1), with the 28-sweep pool at
base 19% vs cand 25% (sign test p=0.18 — suggestive, not established).

> **VERIFIED at r14 on `exec-bugfix` cannot grade an effect smaller than about
> six runs, and may carry a small pro-candidate bias. Stop reading it as the
> primary channel for anything narrower than that.**

**5. The metric that does work: iterations spent on doomed runs.** It is a
within-arm measure, it needs no cross-arm comparison, and it is exactly what the
lever was built to move:

| | doomed runs | mean iterations | mean seconds | worst |
|---|---|---|---|---|
| build 114, no cap | 5 | 32.8 | 277 | 35 (the ceiling) |
| build 115, cap firing 5× | 10 | **26.1** | **242** | 29 |

**A run that was never going to succeed now costs 26 iterations instead of 33 —
about 20% less, and it stops naming the test that beat it instead of grinding
into the iteration ceiling.** That is the entire claim of 5.43, delivered.

Cost, stated honestly: 5 runs were cut at 26-29 that would otherwise have run to
35, and the archive says a level-3 run recovers about 1 time in 69. Expected loss
≈ 0.07 runs. That is the trade, and it is a good one.

**Disposition: KEEP builds 115 and 116, credited on iterations-on-doomed-runs,
not on VERIFIED.** The next sweep of any stall lever should report that table
and treat VERIFIED purely as a veto.

### 5.45 b115: the lever never fired, so the sweep graded the instrument instead (2026-08-09)

`b115-stallcap` (r14, base `02fbfc2` = build 114). The verdict has three parts
and the third is worth more than the first two.

**1. No regression.** Score 0.821 → 0.893 (+0.071, W3/L1/T10, UNDERPOWERED —
4 informative pairs against the 6 the sign-flip test needs). VERIFIED 9/14 →
11/14. Stopped 5 → 3. Mean iterations 26.5 → 24.0. Every number moves the right
way and not one of them is attributable, for the reason below.

**2. The lever fired zero times.** No cand run stopped with the new message.
Three cand runs reached the escalated steer and ended 3, 4 and 6 iterations
later — all inside the K=8 budget, so the cap was never due. Methodology 17: a
lever that fires zero times is untested. It is not credited.

Its counterfactual within the sweep is favourable, which is the most that can be
said: applied offline to the 14 **base** runs, K=8 would have cut 4 of them —
they ran 17, 18, 18 and 18 iterations past the steer — saving 39 iterations and
cutting **zero** VERIFIED runs. The corpus modelling holds; the live channel is
just silent.

**3. Because it never fired, the arms ran identical code — this was an A/A.**
Nothing else in build 115 changes behaviour: the new config field is inert, the
arming assignments write two attributes nothing else reads, and `_test_ran_green`
has no effect but clearing them. So b115 is a free A/A of the endings channel,
and it is only the second one ever run:

| A/A sweep | build | VERIFIED base vs cand | mean iters |
|---|---|---|---|
| `aa14-calib` | 109 | 3 vs 4 | 26.6 vs 31.4 |
| `b115-stallcap` | 114 | 9 vs 11 | 26.5 vs 24.0 |

**On identical code, VERIFIED swings by 1-2 runs in 14 and mean iterations by up
to 4.8.** That is the noise floor of the instrument 5.27 promoted to PRIMARY. It
means a 2-run VERIFIED difference at r14 is not evidence, and I have been
reading arm gaps of that size as if they were. 5.40 got this right by accident
(it declined to attribute a 9→6 VERIFIED gap because the build bundled a
behaviour change); the reason to decline was better than the one given.

**And the thing those two rows actually measure: build 109 → build 114.** Same
case, same model, same repeat count, and `git diff 553cc5e..HEAD -- evals/`
is empty, so the harness, the case and the grader are byte-identical. Comparing
A/A to A/A pools 28 runs of each against no candidate at all:

> **RETRACTED — see 5.47.** `aa14-calib` ran 2026-08-08 09:51 and
> `b115-stallcap` 2026-08-09 02:50, with the model server restarted between them
> (Aug 8 16:08/16:18). The code was byte-identical; the *process* was not, and
> the restart alone moves this number. The two rows below are not comparable and
> the p-value is meaningless. Everything else in 5.45 — the noise floor, the
> A/A reading, the disposition — is within-sweep and stands.

| | VERIFIED |
|---|---|
| build 109 (`aa14-calib`, both arms) | 7/28 = **25%** |
| build 114 (`b115-stallcap`, both arms) | 20/28 = **71%** |

Fisher exact two-sided **p = 1.1e-3**. This is the first significant measurement
of cumulative progress in the project, and it comes from the two sweeps that
were testing nothing. Worth remembering next time a sweep looks wasted: the
strongest number here was produced by the arms that were supposed to be boring.

**Disposition: KEEP build 115.** It is a correct guard on the right signal, it
costs nothing, its counterfactual on this sweep is 4 runs cut and 0 lost, and
there is no evidence against it. But it stays UNTESTED until it fires, and it
cannot be *seen* to fire: nothing in the event stream records the budget arming.
That is methodology 2 all over again, so build 116 fixes the telemetry in the
same change that gives the mechanism a trigger that fires often enough to grade.

### 5.44 The no-op re-send: 96% fatal, and it fires BEFORE the steer (2026-08-09, measured, not built)

Queued as the lever after b115. Measured while that sweep runs, on the same
five-sweep corpus (140 runs), with the tight predicate 5.42 says to use first.

**The standing claim survived this time.** 5.34 read 20 events and said the
`old == new` edit is not a malformed edit but a *redundant* one — the model
re-sending a change it had already applied. Against all 51 events in the
corpus, with the predicate demanding the same file AND a byte-identical match
to the `new` of an edit that actually landed earlier in the same run:

| what the `old == new` text actually is | events |
|---|---|
| the `new` of the run's OWN earlier landed edit, byte-identical | **48 (94%)** |
| text it had read but never written | 3 (6%) |
| a malformed "drafted the replacement into both fields" edit | **0** |

Median gap between the landing edit and the no-op re-send: 4 calls. Worth
stating explicitly because it was the alternative hypothesis and it is dead:
the equality test in `fs.py` is a strict `old == new` on the raw arguments, and
the archived arguments are byte-identical in all 51 cases. **No normalisation
bug, no parser bug — the model really does send the same string twice.**

The shape of it, from `b113 r10` (calls 10-16): edit lands → `bash`, test fails
→ **no-op re-send** → `bash`, same failure → `read_file` on the test → **the
same no-op again**. Note what that trajectory also shows: the build-111 message
converts. It asks for `bash` and gets `bash` on the very next call. Then the
model re-sends the no-op anyway, three calls later. Another instance of
methodology 31 — the wording is not the problem and a fourth revision of it
will not be either.

**Why it is the better trigger.** Two numbers make this the sharpest marker in
the archive:

| population | runs | VERIFIED |
|---|---|---|
| whole corpus | 140 | 63 (45%) |
| runs with at least one no-op re-send | 25 | **1 (4%)** |

and it is *early*: 22 of those 25 runs go on to reach the escalated steer, with
the no-op arriving first in 18 of 22, by a median of 4 iterations. Three more
runs emit a no-op and never reach the steer at all, so build 115's budget never
sees them.

So the lever is not a new mechanism, it is a second trigger on the one built in
5.43: **a byte-identical no-op re-send arms the escalated-stall budget.** Fires
~4 iterations earlier than the current trigger on the runs both catch, and
covers 3 the current trigger misses. Deliberately arms the budget rather than
stopping outright — 96% precision is not 100%, and the one survivor deserves
its 8 iterations. Check before building: whether that survivor finished within
8 iterations of its own no-op. If it did not, the trigger needs a longer budget
than the steer's, not the same one.

**Checked, and it did not — so the trigger needs its own K.** The survivor is
`b113 exec-bugfix r8`, the *same run* that is the lone survivor of the
escalated-steer population. Its first no-op is at iteration 22 and it went green
at 31: **9 iterations after, one past a K of 8.** Arming the shared budget on
the no-op would cut the only run in 140 that ever came back from either signal.
So the earlier trigger takes a longer leash — K=10 on this one, against 8 on the
steer — which is coherent rather than arbitrary: the no-op fires a median of 4
iterations sooner, so an equal budget is a strictly tighter cut, not the same
one moved earlier.

Say the uncomfortable part plainly: **both thresholds are now calibrated on the
same single run.** That is not a safety margin, it is one observation doing the
work of a distribution. It is the conservative direction to be wrong in — the
cost of too large a K is some wasted iterations, the cost of too small a K is
deleting the only evidence that deep stalls are survivable — but b115 adds 28
runs to the corpus and its survivors, if any, are the first real chance to
replace this with a number. Recalibrate then; do not tune either K down before.

**Not built yet, on purpose.** b115 is in flight and methodology 28 says one
change per sweep. It also has to wait for b115's verdict on principle: if the
budget mechanism itself regresses, adding a second, earlier trigger to it makes
things worse, not better.

### 5.43 Build 115: the signal that tracks stuck-ness has no stop on it (2026-08-09)

Every lever from 5.32 through 5.42 tried to make the escalated steer *work*.
Three sweeps and 5.38's wall say it doesn't and won't. So stop trying to convert
the steer and ask the other question: what does the turn do with the iterations
it spends after it?

Measured over the five most recent sweeps — 140 runs, 3787 iterations, arms
pooled because the question is about the harness, not about any candidate.

| depth reached | runs | VERIFIED | median iterations still to come |
|---|---|---|---|
| `same failure (2)` — the level-1 note | 92 | 17 (18%) | 15 |
| `same failure (3)` — the escalated steer | 69 | 1 (1%) | 8 |
| `same failure (4)` | 46 | 0 (0%) | 0 |
| `same failure (5)`+ | 5 | 0 (0%) | 0 |

The level-1 note sits in front of a population that succeeds 18% of the time and
is worth every iteration it costs. One step deeper the population is dead: 49%
of all runs reach the escalated steer, and between them they spend **712
iterations — 19% of every iteration in the corpus — to produce one verified
finish in 69.**

The obvious objection is that the existing guards already handle this, and they
do handle it — late. 61 of those 69 runs (88%) are eventually ended by the
repeat guard or by `max_error_stall`, a median of 10 iterations after the steer.
They arrive late for a structural reason worth naming: **`max_error_stall` keys
on the error TEXT, and the same-failure counter keys on the test's identity.**
5.24b measured the gap directly — 97 distinct error signatures against 37 real
failure identities on b99 — so a model that varies what it gets wrong walks past
the text-keyed guard while the identity-keyed counter tracks it perfectly. The
counter is the better stuck-ness signal and it is the one with no stop attached.
Build 115 attaches one.

**`agent.escalated_stall_budget = 8`.** Once the escalated steer fires, the turn
gets 8 more iterations; if it is still going after that, it ends itself naming
the test that is stuck. Going green disarms it, so a run that recovers and then
does legitimate follow-up work is not cut off.

8 is chosen to be **conservative, not optimal**, and the distinction matters
because it is fitted to n=1. The single recovery in the corpus
(`b113 exec-bugfix r8`) went green at exactly +8, so the budget is set to keep
it. That costs half the available saving:

| budget K | iterations saved | share of the waste | recoveries kept |
|---|---|---|---|
| 2 | 574 | 81% | 0 / 1 |
| 4 | 482 | 68% | 0 / 1 |
| 6 | 400 | 56% | 0 / 1 |
| **8** | **327** | **46%** | **1 / 1** |
| 12 | 210 | 29% | 1 / 1 |

Cutting at 2 would look twice as good on the only number this lever moves, and
would delete the one thing in 140 runs that argues the deep end is survivable at
all. Tune it down later if a sweep says the recovery was noise; do not tune it
down first.

**What this is and is not.** It is not expected to raise VERIFIED — nothing
downstream of the escalated steer verifies, so there is nothing there to save.
It converts a long useless turn into a short honest one, which is most of what
turn efficacy means from outside the process, and it is the first lever aimed at
that rather than at the success rate. The grading criteria, fixed before the
sweep runs:

- **Must not regress.** VERIFIED and score unchanged within noise. A drop means
  the budget is cutting live runs and 8 is too low.
- **Should show.** Median iterations down; `stopped` reasons shifting toward the
  new message; the post-steer iteration count down by roughly the modelled 46%.
- **Watch.** Runs where the stop fires and the case would have passed anyway —
  read them, they are the counter-argument.

Per methodology 28 this is the **only** change in its sweep. No wording moved.

### 5.42 Build 113 is a negative result, and it rested on a bad number (2026-08-09)

`b113-wholefile` (r14, base build 112). Graded in the planned order.

| | base (112) | cand (113) |
|---|---|---|
| `ENTIRE file` notes emitted (reach) | 0 | **21** |
| score | 0.786 | 0.786 (+0.000, W4/L4/T6, p=1.0) |
| VERIFIED | 8/14 | 8/14 |
| median iterations | 25 | 25 |
| runs hitting `ALREADY DONE` | 6/14 | 5/14 |
| prose after the escalated pair | — | 0/13 |

**The lever reached its target 21 times and moved nothing.** Not underpowered
in the usual way either: the exposure channel that was supposed to shrink
(`ALREADY DONE`, the fatal path) went 6 → 5, and every outcome tied.

**And the premise was false.** Re-running 5.41's mechanism check with the edit
required to touch the file that had just been read: of 75 windowed reads
followed by an edit, **53 (71%) edited a different file** — read the test,
patch the source, which is exactly right — 11 targeted text inside the window,
and only **7** were genuine misses. "60 of 71" was counting normal two-file
work as a failure. The one trajectory I read end to end was real and atypical,
and reading it is what made the wrong aggregate look confirmed.

**Build 114 reverts build 113.** No benefit measured, and the reason for
believing there would be one does not survive checking. Keeping a neutral
change whose justification has been withdrawn is how a codebase accretes
complexity that nobody can later argue for.

What survives 5.41: windowed reads still correlate with dying runs at the
per-run level (median 0% verified vs 25% died). With the mechanism gone that
reads as a **symptom** of a confused run, not a cause — and build 113 is the
experiment that says so, because removing the windows changed nothing.

**Methodology 30: a mechanism check must pin every dimension the claim names.**
"The edit targeted text outside the window" is a claim about one file. The
check allowed any file, so it counted the most ordinary thing the model does —
reading a test and editing the source — as the pathology. Reading a single
matching trajectory made it feel verified; one confirming example cannot test
an aggregate, only illustrate one. When a census produces a number that looks
decisive, re-derive it with the tightest possible predicate before spending a
sweep on it.

**Also: level 3 is not a literal zero.** `b113` base r8 is the first recovery
after an escalated steer in this whole stretch — read the file, edit, test,
edit again, 13 passed at 31 iterations. Nothing exotic; it simply got the
second edit right. So the standing count is **1 of ~69**, not 0 of 58. That
does not change 5.40's conclusion (level-3 compliance buys ~1.5%), but the
claim should be stated as vanishingly rare rather than impossible.

### 5.41 The model narrows the read, then edits from the older view (2026-08-08)

5.40 left the next lever as "structural, not verbal", with the `ALREADY DONE`
path (0 for 11) as the place to look. Mining it gave a mechanism.

**25 of 29 already-applied edits are the model re-sending an edit it made
itself**, a median of 4 tool calls earlier, and **21 of those 25 without having
re-read the file in between**. Reading one end to end (`b111-recipe` r10 cand)
showed what that actually looks like, and it is not what "already applied"
suggests:

```
bash pytest            -> 1 failed
NUDGE same failure (2)
read_file test_textkit.py
edit_file textkit.py   -> edited (lands)
update_plan / bash     -> the SAME failure
NUDGE same failure (3)
read_file textkit.py   offset=47 limit=20      <- obeys build 111, and windows
edit_file textkit.py   old = "def truncate(...  <- line ~40, ABOVE the window
                       new = the same text      -> ALREADY DONE
```

The model complied with the steer, then asked for a 20-line slice of an
**83-line** file, and composed `old` from an earlier, staler read — text the
window it had just fetched did not contain. The success path of `edit_file`
already echoes the changed region, so this is not staleness at edit time; it is
the model choosing to look at the wrong twenty lines.

**It generalises, and it survives methodology 27.** Across `b111-recipe`,
`b110-alreadydone` and `aa14-calib`:

| | runs that VERIFIED | runs that died |
|---|---|---|
| `read_file` calls that were windowed | 7/97 = 7% | 78/258 = 30% |
| **per-run windowed share (median)** | **0%** | **25%** |
| runs using a window at least once | 7/40 | 23/44 |

The per-run median is the number that matters — 5.39's cadence lever died
because its pooled gap reversed at run level, and this one does not.

> **CORRECTION (5.42).** The mechanism sentence that stood here — "after a
> windowed read, 60 of 71 following edits targeted text that was not in the
> window" — was **wrong**, and it is what this build was built on. The check
> did not require the edit to touch the file that had just been read. Redone
> properly over the same 75 events: **53 (71%) edited a DIFFERENT file** —
> reading the test and editing the source, which is correct behaviour and not
> a window miss at all — 11 (15%) targeted text inside the window, and only
> **7 (9%)** were genuine misses. The r10 trajectory below is real but
> atypical. The correlation above survives; the mechanism did not.

The requested windows are absurd on their face: the most common asks are
`offset=8 limit=10` and `offset=8 limit=20`, against a file of 83 lines.

**Build 113 (5.41).** `read_file` overrides the window when the whole file is
small — at or under 400 lines and 40 KB — and says so in one line ("this is the
ENTIRE file, not the window you asked for"), because a model that does not know
it holds the whole file will keep composing against the slice it thinks it
asked for. Paging still works where paging is the point. One change, swept
alone (methodology 28).

This is also the first lever in this stretch aimed at the *cause* of a stuck
run rather than at the message the harness sends once it is stuck — which is
where 5.38 and 5.40 jointly say the remaining headroom is.

### 5.40 Build 111: the steer converts perfectly and buys nothing (2026-08-08)

`b111-recipe` (r14, base build 110 at `09b0c16`, cand build 111). The clearest
result in this stretch, in both directions.

**Channel A — the reshape works, completely.** Prose-only after the two
escalated steers:

| arm | prose | what they called instead |
|---|---|---|
| base (110) | **6/6 = 100%** | — |
| cand (111) | **0/10 = 0%** | 10× `read_file` |

The 5.32 recipe transfers to any steer it is applied to. That is now three
steers and 86 events at 0%, against 100% for the same steer one build earlier.
The control (the level-1 note) held at 0/5 and 0/13.

**Channel B — and the wall did not move.** Runs reaching an escalated steer,
then verifying: base 0 of 3, cand **0 of 6**. The standing count goes from 0/49
to **0/58**. So the model now does exactly what level 3 asks — reads the file
it was told to read — and still never recovers. **Compliance at level 3 is a
vanity metric.** 5.38's fork resolves to its second branch.

**The arms also diverged, and build 111 cannot be cleared or convicted for
it.** VERIFIED 9 → 6, stopped 5 → 8, mean iterations 22.4 → 28.1, score −0.107
(p=0.51, inside the +0.143 floor). The proximate cause is exposure, not the
steers: runs that touch the `edit_file` "ALREADY DONE" path are fatal in *both*
arms — **0 of 4 base, 0 of 7 cand, 0 of 11 overall** — and the cand arm drew
seven of them against base's four. Every single VERIFIED run in the sweep, both
arms, finished in exactly 23 iterations.

**But I bundled, so I cannot finish that sentence honestly.** Build 111 carried
a second change: 5.36's "no_change edits are not landed edits". Two things about
it, both found only after the sweep:

1. **It fixed no metric.** `armstats` derives landed edits from the event
   stream, where a `no_change` result carries `error: false`. It counted them
   before and after. The eval number 5.36 set out to correct never changed.
2. **It did change the agent.** `_landed_edits` feeds the repeat detector's
   [verify-after-change] reset, so suppressing it makes repeat-stops fire
   sooner — and the cand arm's stop mix shifted exactly that way, from `edits
   kept hitting the same error` (3) to `the model repeated the same tool call`
   (6), with `repeated call` nudges going 2 → 15.

A behaviour change rode inside a sweep testing steer *wording*. Whatever the
regression is, this sweep cannot attribute it.

**Build 112** unbundles: revert the `_landed_edits` exclusion to build 110
semantics, and emit `no_change` on the result event instead, where a grader can
read it without touching how the agent behaves. Build 111's wording survives
intact — Channel A earned it, and it costs nothing.

**Methodology 28: never bundle a behaviour change into a sweep that tests
wording.** The temptation is that the behaviour change is "obviously correct
and unrelated". 5.36's was neither, and one bundled line cost the attribution
of a 2.4-hour sweep. Ship the wording, sweep it, then ship the behaviour.

**Methodology 29: before "fixing" a metric in the product, check where the
metric is actually computed.** This one lived in the grader the whole time. The
fix belonged in `armstats.py`, cost nothing, and risked nothing; putting it in
`loop.py` changed the agent to correct a number the agent does not produce.

**Where this leaves turn efficacy.** Three sweeps of steer-wording work have
established the shape that makes a local model act instead of narrate, and it
is reliable. But 5.38's law says VERIFIED is the level-1 escape rate, and
nothing downstream of level 1 has ever moved it. The remaining levers are
structural, not verbal:

- **The `ALREADY DONE` path is 0 for 11.** A run that sends an already-applied
  edit does not recover, under any of the three messages tried (build 55's,
  110's, 111's). Stop writing messages for it. The question worth asking is why
  the model believes an applied edit still needs applying — most likely it is
  reading stale file contents out of its own context rather than the disk.
- **Ending the turn at level 3** now costs nothing measurable: 0/58 recoveries,
  and the runs that get there spend 10–20 further iterations (cand's reached
  runs ran to 33, 35, 44) producing nothing.

### 5.39 Two level-1 levers, mined and declined (2026-08-08)

5.38 says the level-1 escape rate is the whole game, so I went looking for a
lever there. Both candidates died on inspection. Recording them so they are not
rebuilt.

**Declined 1 — "make the note prescribe the whole sequence, not just the
read".** The level-1 note converts 100% to `read_file`, yet only 22% of the
runs that receive it verify. Censusing the calls after each note in the
post-108 arms looked decisive:

| next three calls | runs that VERIFIED | runs that died |
|---|---|---|
| `read_file → edit_file → bash` | **14 of 14 (100%)** | 9 of 62 |
| `read_file → edit_file → update_plan` | 0 | 27 |
| `read_file → edit_file → read_file` | 0 | 18 |

Every survivor read, edited, and immediately ran the tests; half the dead
detoured through `update_plan`. The obvious lever is to name the whole sequence
in the note and forbid the detour. **Then I followed the dying branch two calls
further: all 27 of the `update_plan` detours reach `bash` anyway** (19 of them
`→ bash → bash`). The detour costs one call and nothing else. Survivors and
casualties run the *same* loop — read, edit, test. The difference is whether
the edit was **right**, which is a capability wall, not a steering one. No
lever here.

**Declined 2 — "dying runs don't test their edits".** Pooled over landed edits,
survivors verify 111/148 = 75% of their edits before making the next one and
casualties 210/370 = 57%, which reads as a real cadence gap. It is entirely
5.37's clustering trap: the per-RUN median is **60% for survivors and 67% for
casualties** — the gap reverses sign. Dying runs have more edits, so pooling
weights them by exactly the thing that makes them dying runs.

That second one is worth keeping as the sharpest example of methodology 27 in
the log: the same data says survivors test *more* (pooled) and *less* (per
run). **Compute the per-run median before believing any pooled per-event rate.**

### 5.38 Level 3 is a dead zone: 0 of 49 runs that reached it ever verified (2026-08-08)

Followed 5.37's depth finding one step further — if prose decays with depth,
what does *success* do? Pooled over the three recent r14 sweeps (84 runs),
using armstats' own VERIFIED definition:

| total iterations | runs | VERIFIED | stopped |
|---|---|---|---|
| 16–25 | 46 | 31 (67%) | 14 |
| 26–35 | 24 | 1 (4%) | 21 |
| 36+ | 14 | 0 (0%) | 10 |

A run either finishes inside ~25 iterations or it does not finish. Conditioning
on which steer a run ever reached makes it sharper:

| nudge reached | runs | VERIFIED | median iters |
|---|---|---|---|
| `same failure (2 runs in a row)` — level 1 | 64 | **14 (22%)** | 33 |
| `same failure (3 runs in a row)` — level 3 | 49 | **0 (0%)** | 33 |
| `error unchanged across edits` — level 3 | 45 | **0 (0%)** | 33 |
| `same failure (4 runs in a row)` | 32 | 0 (0%) | 33 |
| `unverified edits` | 43 | 18 (42%) | 23 |
| `repeated call` | 21 | 0 (0%) | 34 |
| `tests claimed passing but never seen green` | 7 | 0 (0%) | 40 |
| `context compacted` | 6 | 0 (0%) | 45 |

**Not one run that reached an escalated steer has ever ended verified — 0 of
49 — while runs that never reached one verify at 91% (32/35).** Part of that is
definitional: a run that recovers at level 1 never reaches level 3, so level 3
selects for runs already in trouble. But the *cliff* is the finding. Level 1 is
where essentially all the recoverable probability mass sits (22% still verify
after it); by level 3 there is none left, and the remaining ~10 iterations are
spent producing nothing.

**And VERIFIED is almost entirely determined by that one number.** Running
the same read across all six recent arms (14 runs each):

| arm | build | runs reaching level 3 | VERIFIED | 14 − reached |
|---|---|---|---|---|
| b108 base | 107 | 14 | 0 | 0 |
| b108 cand | 108 | 7 | 7 | 7 |
| aa14 base | 109 | 11 | 3 | 3 |
| aa14 cand | 109 | 9 | 4 | 5 |
| b110 base | 109 | 3 | 9 | 11 |
| b110 cand | 110 | 5 | 9 | 9 |

Four of six land exactly on `14 − reached`, the other two within two. VERIFIED
is not really a second channel at all — it is the **escape rate at level 1**,
reported in different units. Three consequences:

- Build 108's `VERIFIED 0 → 7` was *entirely* "runs reaching level 3 fell 14 →
  7". The level-1 note is the lever, and that is why it is the only change so
  far that moved an ending.
- The between-sweep VERIFIED drift that produced methodology 26 (build 109
  giving 3, 4 and 9) is drift in **how many runs get stuck at level 1** — real
  sampling variation in run difficulty, which is why pairing cancels it and
  levels do not travel.
- **Build 111 can only win by breaking the 0/49 wall.** Nothing it changes
  fires upstream of level 3, so if reaching level 3 stays fatal, its VERIFIED
  must come out flat no matter how well the reshaped steers convert.

**This reframes the running b111-recipe sweep, for the better.** Build 111
gives the escalated steers the recipe that makes the level-1 note convert 100%
of the time. The prose-only metric asks "did the model obey"; the question that
actually matters is now sharper and free — **does any run that reaches level 3
verify?** The answer has been 0/49 across three sweeps. If build 111's cand arm
produces even two or three, level 3 is recoverable and the steer wording was
the whole problem. If it converts the prose to `read_file` calls and *still*
returns 0, then compliance at level 3 is a vanity metric and the lever is
elsewhere. Either result is worth the GPU time; grade both.

**Two candidate levers behind that fork, neither built yet:**

1. **Stop asking at level 3 — inject.** 19 of 19 post-108 responses that acted
   on an escalated steer called `read_file`, and the ones that narrate never
   call anything. So the harness could simply attach the relevant source window
   itself and skip the compliance question. The tension is 5.37: depth is
   lethal, and pasting a file at iteration 31 spends the budget that is already
   killing the run — so a focused window (the function under the failing
   assertion), not the whole file.
2. **End the turn at level 3 instead of grinding.** If 0/49 is real, the ~10
   iterations after the first escalated steer are pure waste, and the user gets
   a stall report ten iterations later than the harness knew. Ending there
   would not raise VERIFIED, but it converts a long useless turn into a short
   honest one, which is most of what "turn efficacy" means from the outside.
   Gate this on the b111 result — if level 3 does convert, cutting it off would
   be throwing away the recoveries.

### 5.37 Prose is a function of DEPTH — except where the recipe is (2026-08-08)

Built the build-111 grader against the old sweeps first (methodology 25) and
it reproduced 5.35's table to the event. Then it turned up something the table
could not show, and it changes how every nudge-level metric in this project
must be read.

**Finding 1 — the events are not independent; they cluster by run.** Almost
every run is `0/2` or `2/2` on the escalated pair. Per arm in `aa14-calib`:
base `2/2 2/2 2/2 1/2 0/2 0/2 0/2 2/2 2/2 0/2 0/2`, cand `0/1 2/2 2/2 2/2 2/2
2/2 2/2 2/2 2/2`. So "n=22 events" is really n=11 runs, and every p-value I
have quoted on a per-nudge rate is inflated. A model that starts narrating
narrates at every subsequent steer in that turn — one draw, not two.

**Finding 2 — the arms differ systematically on identical code.** `aa14-calib`
is an A/A (both arms build 109) and the escalated pair splits 50% base vs 94%
cand; `b110-alreadydone`, which changed nothing about these steers, splits 0%
base vs 80% cand. Same direction, both times. Taken at face value that would
mean the metric is unusable.

**Finding 3 — it is depth, and depth alone.** Pooling every escalated-steer
event across the post-108 arms and bucketing by the iteration it fired on:

| iteration when the steer fired | prose |
|---|---|
| 9–16 | 1/26 = 4% |
| 17–24 | 9/28 = 32% |
| 25+ | 36/45 = 80% |

Median firing iteration: base 20, cand 31. The arm asymmetry is entirely a
population difference in *when* level 3 is reached — methodology 24 again, one
level down.

**Finding 4 — and the recipe is immune to it.** Cross-tabulating the same
events by steer SHAPE (the level-1 note carries the 5.32 recipe from build 108
on; the escalated pair never did) against depth:

| depth | has the 5.32 recipe | no recipe |
|---|---|---|
| 1–8 | 0/11 = 0% | 0/4 = 0% |
| 9–16 | 0/17 = 0% | 3/28 = 11% |
| 17–24 | 0/25 = 0% | 19/40 = 48% |
| 25+ | 0/23 = 0% | 36/48 = 75% |

**0 prose in 76, at every depth.** Depth predicts narration only for steers
that do not name a call, put it first, and forbid the explanation. That is the
cleanest evidence yet that 5.32 is a real mechanism and not a lucky sweep, and
it says the escalated steers — which fire at median iteration 31, the worst
bucket — are exactly where build 111 should pay the most.

**How this changes the b111 grading.** Do NOT read a cand-vs-base prose
difference directly: if build 111 shortens runs, depth alone moves it. Read
against the *level*, which the table above makes a sharp prediction about —
the recipe is 0% everywhere, so a cand rate meaningfully above zero means the
reshape failed to transfer, and a base rate near 50–75% is just build 110
behaving as measured. Cluster by run when quoting any n.

**Methodology 27: a per-nudge rate is a per-RUN draw, and it decays with
depth. Cluster the events by run before quoting n, and check the two arms'
firing depths before crediting the difference to the change.**

### 5.33 Build 108 converts — the first sweep to move a turn ENDING (2026-08-08)

`b108-callnotword` (r14, base build 107). The level-1 same-failure note stopped
asking for a sentence and started demanding the `read_file` call. Every channel
moved the same way:

| | base (14) | cand (14) |
|---|---|---|
| **prose-only after the level-1 note** | 12/21 = **57%** | **0/18 = 0%** |
| **VERIFIED** (ended on a green test run) | **0/14** | **7/14** |
| false-done | 5 | 1 |
| DONE | 5 | 8 |
| stopped | 9 | 6 |
| nudges/run | 9.4 | **3.8** |
| escalated same-failure/run | 1.2 | 0.4 |
| mean iterations | 32.5 | 25.4 |
| mean landed edits | 8.9 | 5.6 |
| score | 0.304 | 0.696 (+0.393, W10/L0/T4, p=0.002) |

Two of these are unprecedented. **VERIFIED has been 0 in every arm of every
sweep in this stretch** — b107 was 0/14 against 0/14 — and it is the only
metric that cannot be gamed by a model announcing success. And prose-only hit
**0 of 18**, the floor that `unverified edits` has always sat at, rather than
merely improving; the 5.32 A/A control put the noise band at ±20 points, so a
57 → 0 move is not a reading of noise.

**The mechanism is visible in the trajectory** (r1, candidate). Four edit /
pytest cycles fail; the nudge fires; the very next call is `read_file
test_textkit.py` — the test, not the source file it had been editing — followed
by one edit, then `13 passed`. The base arm at the same point reads the test
too, then spends 775 characters narrating, collects an `open plan tasks` nudge
for having done nothing, and its next edit dies on an ambiguous `old`.

**The secondary effect is the bigger one.** Nudges fell 9.4 → 3.8 per run.
Converting the *first* same-failure note stops the run from reaching the states
that generate the rest: escalated same-failure fell to a third, `open plan
tasks` from 23 to 3, `unverified edits` from 26 to 6. Fewer iterations and
fewer landed edits with more verified finishes is the shape of a model that
stopped thrashing.

**The score channel said INCONCLUSIVE** and was right to at the time: no A/A
had measured this setup at r14, and the one A/A on record (b106, r8) returned
+0.281 from identical code. The verdict above did not rest on the score — it
rested on VERIFIED, on the prose-only rate against its own base arm, and on a
read run.

**`aa14-calib` settles it, and corrects the headline.** Both arms build 109,
28 runs, `exec-bugfix|qwencoder14|r14` — the exact configuration b108 ran in.

| | A/A arm 1 | A/A arm 2 | b108 base (107) | b108 cand (108) |
|---|---|---|---|---|
| score | 0.500 | 0.643 | 0.304 | 0.696 |
| VERIFIED | 3/14 | 4/14 | 0/14 | 7/14 |
| DONE | 3 | 5 | 5 | 8 |
| stopped | 11 | 9 | 9 | 6 |
| iterations | 26.6 | 31.4 | 32.5 | 25.4 |
| landed edits | 6.6 | 5.6 | 8.9 | 5.6 |

Three readings, in descending order of how much they change:

1. **The score now corroborates.** Identical code produces +0.143 (W7/L1/T6, 8
   informative, p=0.141). Build 108 produced +0.393 (W10/L0/T4, p=0.002) —
   2.7× the floor, and the first change in this project to clear the score
   gate honestly rather than survive it. Recorded as the noise floor for this
   configuration; stop quoting the b106 r8 figure.
2. **The prose-only conversion is not a sample.** `same failure (2 runs in a
   row)` fired 45 times across both A/A arms and was answered with prose **0
   times**. Both arms contain build 108, so 0% is exactly what it predicts —
   and 45 events at 0% is a far stronger statement than the 18 that earned the
   verdict — 63 counting b108's own candidate arm.
3. **"VERIFIED 0/14 → 7/14" was optimistic.** Identical code reproduces that
   metric at 3/14 and 4/14, so it swings ~1 in 14 at this sample size and 7 is
   the high end of the post-108 range. The honest statement is the three-arm
   one: pre-108 code verified **0, 0, 0** out of 14; post-108 code verified
   **7, 3, 4**. The direction is not in doubt. The size is about a third of
   runs finishing verified, not half.

**Methodology 23: an A/A does not just size the noise — it re-reads your
headline number.** The A/A was launched to calibrate the *score*, and it did.
The thing it actually corrected was the mechanism metric the verdict was built
on, which nobody had thought to question because it moved so far.

**And it names the next prose lever** — but only once the arms are split.
Reading the A/A whole gave `same failure (3 runs in a row)` 65% prose-only and
`error unchanged across edits` 74%; splitting by arm gives 45%/89% and
55%/100% on n of 9 to 11. Methodology 21 exists for exactly this and I did not
apply it. Pooling instead over every arm running build ≥108 — the population
has to match, since level 1 converting is what changes who reaches level 3
(methodology 24) — gives the real figures, with pre-108 alongside:

| nudge | pre-108 | post-108 | what the post-108 acters called |
|---|---|---|---|
| `same failure (2 runs in a row)` | 12/21 = 57% | **0/63 = 0%** | 63× `read_file` |
| `same failure (3 runs in a row)` | 4/17 = 24% | 16/28 = **57%** | 11× `read_file` |
| `error unchanged across edits` | 1/13 = 8% | 17/26 = **65%** | 8× `read_file` |

Three things fall out of that table:

- **Build 108 is stronger than the verdict claimed.** 0 prose replies in 63
  events across three independent arms, every one of them answered with the
  named call. Not 0/18.
- **The escalated branches are the successors**, at 57% and 65% — worse than
  they read pre-108, which is selection rather than regression, but not the
  65%/74% the unsplit read gave.
- **They are already asking for the wrong tool.** `_nudge_stall` names
  `write_file`, and got 11 of them pre-108 — and **zero** post-108. Every
  post-108 response that acted at all, on either escalated nudge, called
  `read_file`: 19 for 19. What a working answer looks like is identical at
  every level; only the wording differs, and only the level-1 wording lands.

**Methodology 22: fix the first steer in a cascade, not the loudest one.** The
loudest nudges in the b107 census were symptoms of a run that had already gone
wrong two nudges earlier.

**The new bottleneck** (censusing the 7 candidate runs that still did not
finish): the top edit failure among them is *"This edit does NOTHING: `new` is
identical to `old`"*, 10 events. Across the whole sweep it runs 0.71/run in
both arms, and `old` appearing more than once runs another 0.64–1.14/run. This
becomes 5.34.

### 5.36 Build 110 lands nothing, and VERIFIED turns out not to be a level (2026-08-08)

`b110-alreadydone`, r14, base build 109. **NO DETECTABLE EFFECT.**

| | base (109) | cand (110) |
|---|---|---|
| score | 0.821 | 0.821 (+0.000, W3/L3/T8, p=1.0) |
| VERIFIED | 9/14 | 9/14 |
| DONE | 9/14 | 9/14 |
| stopped | 5 | 5 |
| mean iterations | 25.6 | 24.1 |
| mean landed edits | 5.1 | 5.1 |

The mechanism moved in the intended direction — the `old == new` result routed
to `replace_lines` 2 times in 10 in the base arm and **0 times in 4** in the
candidate — but 4 events is not a measurement, and nothing downstream of it
moved at all.

**Why it did nothing is legible, and it is my error, not the model's.** All
four candidate responses called `update_plan`. Not one re-ran the tests, which
is what the new message asks for. Set it against build 108, which converted 63
for 63:

> **build 108:** Call read_file on \`test_x.py\` **now** … Do not answer this
> with an explanation: the next thing you send must be that read_file call.

> **build 110:** This edit is ALREADY DONE … Nothing to change … Do NOT resend
> it, do NOT revert it, and do NOT switch to line-number edits to force it in.
> If something is still failing, the cause is on a DIFFERENT line: **run the
> tests again** …

Build 110 names no tool, puts its action seventh, spends three clauses on
prohibitions, and hedges it behind "if something is still failing". I wrote
5.32's recipe, then wrote a message that breaks every clause of it. "Run the
tests again" is not a call the model can emit; `bash` with the command in it
is. This is build 102's lesson again — told to do something it has no
identifier for, the model substitutes the nearest thing it does know how to do,
and `update_plan` is the cheapest such thing in the toolset.

So build 110 **stays** (its diagnosis is now true 90% of the time where the old
one was false, and it removes a route that provably led to churn), but it is
**not fixed**, and it joins 5.35's queue rather than closing.

**Defect found while reading it: a no-op edit is credited as a landed edit.**
`loop.py:1294` reads `if call.name in _MUTATING_EDIT_TOOLS and not res.is_error`
and then sets `self._landed_edit = True`, whose own comment says "the workspace
really did change". Build 110's already-done branch returns a NON-error
`no_change` — as the two "already applied" branches beside it have since build
55 — so the workspace demonstrably did not change and the loop is told it did.
`_landed_edit` gates the done-on-repeated-verify exit and the unverified-edit
accounting. Four events here, so it changed nothing measurable, but it is
wrong. One condition: also require `not res.no_change`.

**Methodology 26 — VERIFIED is a within-sweep DIFFERENCE, never a level.** Six
r14 arms on the same case, model, and sample size:

| build | 107 | 108 | 109 | 109 | 109 | 110 |
|---|---|---|---|---|---|---|
| VERIFIED /14 | 0 | 7 | 3 | 4 | **9** | **9** |

Build 109 produced **3, 4 and 9**. Identical code, a 21% → 64% swing. But
inside each sweep the arms track each other closely (3 vs 4; 9 vs 9), so the
drift is **between** sweeps, not between runs — some per-sweep condition moves
the whole level, and pairing is what cancels it. Consequences:

- Every "VERIFIED went from X to Y" claim is only meaningful within one sweep.
  Never compare an arm to an arm from a different sweep, which is exactly what
  the b108 write-up did when it read aa14's 3-and-4 as a noise band of one.
- The b108 verdict **survives**, but on the non-overlap rather than the size:
  pre-108 arms verified 0, 0, 0; post-108 arms verified 7, 3, 4, 9, 9. Three
  lowest of eight all landing in one group is p≈0.018. The size claim ("about
  a third of runs") is withdrawn — it is unmeasurable at this sample size.
- The A/A's real lesson was not "+0.143 is the floor". It was that **one A/A is
  one sample of a drifting quantity**, and I read it as a fixed constant within
  hours of measuring it.

### 5.35 The escalated nudges are the level-1 note before build 108 (2026-08-08)

Queued behind `b110-alreadydone`. The two worst-converting steers left in the
system, `same failure (3 runs in a row)` (57% prose-only) and `error unchanged
across edits` (65%), are not a new problem. They are the **same** problem build
108 solved, in the same two messages that were never updated with it.

Put the level-1 note and the level-3 one side by side:

> **level 1 (build 108, 0/63 prose):** …Call read_file on \`test_x.py\` **now**
> — the TEST, not the source file you have been editing, and read what it
> asserts. **Do not answer this with an explanation: the next thing you send
> must be that read_file call.** Then make your next edit follow from what the
> test asserts.

> **level 3 (untouched):** ⟳ SAME FAILURE — 4 test runs in a row with identical
> results. Nothing you have tried since the first one has changed anything.
> **Stop editing and open** \`test_x.py\`.

Level 3 leads with two sentences of diagnosis, never names a tool ("open" is
not a call), and has no clause forbidding an explanation. That is precisely the
pre-108 shape, which measured 57% prose-only — and level 3 measures 57% today.

`_nudge_stall` ("error unchanged") is worse, because it contains two explicit
invitations to write prose:

> …**reason about WHY the error happens**, then rewrite the entire function in
> one shot with write_file instead of another small edit_file swap. **If you
> genuinely cannot fix it, say so in plain text now.**

Methodology 19 says a steer that asks for narration is answered with narration.
This one asks twice, and buries its tool name behind five sentences. 65%.

**The census says what to name.** Of every post-108 response to either
escalated nudge that produced a call at all, **19 out of 19 were `read_file`** —
zero `write_file`, despite `_nudge_stall` explicitly asking for one (it got 11
pre-108, and none since). A working answer looks the same at every level. Only
the wording differs, and only build 108's wording lands.

**Build 111, when the sweep clears.** Apply the recipe to both, with one
deliberate difference: at level 3 the model has *already* read the test, so
re-issuing level 1 verbatim would order it to redo the thing that just failed.
Keep the imperative frame and the no-explanation clause, and move the target —
name the **source function under the failing assertion**, read whole, rather
than the test file again. Drop `write_file` from `_nudge_stall`, since nothing
has called it since build 108. Keep its "if you genuinely cannot fix it" escape
hatch (level 3 is where a genuinely stuck run should be allowed to stop) but
place it last and gate it on having made the call first — today it sits where a
struggling model reads it as the easier of two options.

Grade on prose-only per arm for both nudges against the post-108 baselines
above (57% and 65%, n=28 and n=26 — so a 14-run sweep sees ~9 and ~8 per arm,
which is thin; pool the two nudges when reading it), and on VERIFIED.

### 5.34 "This edit does NOTHING" is the wrong diagnosis 90% of the time (2026-08-08)

The no-op-edit message is the failure Victor reported hitting repeatedly in a
live session, and it is the top edit error in the runs that still stall. It is
**not** a prose-only failure — the model always acts on it (50% `read_file`,
40% `replace_lines`, 5% resend). It acts on a message that is telling it the
wrong thing.

Reconstructing each event against the edits that had already landed in the same
run:

| what the no-op edit actually was | n |
|---|---|
| **a change the model had ALREADY applied successfully, earlier in the run** | **18** |
| a genuine no-op with no prior landing | 2 |

The message leads with "put the corrected code there" and diagnoses "the
signature of drafting your intended replacement in `old` as well as `new`."
That diagnosis is right for the 2 and wrong for the 18. Worse, it appends
`_TRY_REPLACE_LINES`, which invites a line-numbered **re-apply of text the file
already contains** — and 40% of the time that is exactly what happens next,
after which nothing changes and the run dies on "edits kept hitting the same
error without making progress."

**Build 110 splits the message on a stateless check.** When `old == new`, look
for `old` in the file:

- **present** → the file already reads that way. Say so, name the line, and say
  the edit is redundant rather than broken: the change is done, so what is still
  failing is failing for a different reason — re-run the tests and read what
  they say now, or look at a different line. **No `_TRY_REPLACE_LINES`** —
  there is nothing to re-apply, and suggesting a mutation here is what starts
  the churn.
- **absent** → the model's `old` is not in the file at all, so it drafted its
  replacement into both fields. Keep today's message, trimmed, for this case.

**The neighbouring message shows what "fixed" looks like.** The other frequent
edit error — `old` appears more than once — names `replace_lines` and the line
numbers, and in b108 it is followed by a **successful** `replace_lines` 20
times out of 21. Same model, same runs, same tool: a message that names the
next call and the argument to put in it recovers essentially always. The no-op
message is the only edit error in the sweep whose follow-up action does not
resolve it, and it is the only one whose leading diagnosis is usually false.

Exposure 0.71/run in the current base arm (methodology 20 satisfied), so a
14-run sweep sees ~10 events per arm.

**Grading metric, corrected before the sweep landed.** The plan said "the share
followed by a `replace_lines` re-apply **of the same text**". Built as written
— next call's `new` byte-identical to the one that just failed — it fires
**once in b108's 20 events**, because the re-apply is normally a *variant* of
the same text, not a byte copy. That metric would have read ~0 in both arms and
graded nothing (methodology 12). The share that simply **routes to
`replace_lines`** is what reproduces: 5/10 base, 3/10 cand, 8/20 = 40% overall,
matching the census 5.34 was written from. That is the headline — build 110
deletes the `replace_lines` suffix from the already-done branch, so it should
collapse there — with `stopped (edits kept hitting the same error)` (3 of 14
b108 candidate runs) as the outcome channel. `$CLAUDE_JOB_DIR/tmp/after110.py`
prints both, and keeps the byte-identical count only as a labelled secondary.

**Methodology 25: build the grader against the OLD sweep before the new one
lands.** Run it on the archive the hypothesis came from and check it reproduces
the number in the write-up. Two of the last three metrics I specified in
advance did not survive that check — this one, and `_nudge_bucket`'s malformed
histogram (5.32) — and both would have been discovered only after burning
2.3 h of GPU, when the arms disagree by nothing and the reason is the ruler.

**Shipped as build 110** (`17c194f`), one departure from the design above: the
present-branch is returned as a **non-error** `no_change`, not a softened
error. Two paths a few lines below it already answer the same situation —
re-submitting a change that landed — with a non-error "already done", and they
exist for the same reason (build 55: a fixable-looking error drives the model
to revert its own working fix). Leaving the identical case an error would have
had one file answer the same question two ways. `no_change` still counts
toward the no-change streak, so three of these still end the turn; the loop's
error-stall signal already excludes `no_change` results, so nothing else
moves. Presence is checked exact-then-whitespace-tolerant, because the re-send
usually comes back dedented — an exact-only check would misroute the commonest
shape of the case the build exists for. 1144 passed; sweeping as
`b110-alreadydone` against build 109.

### 5.32 The nudge asked for a sentence and got a sentence (2026-08-08)

`b107-indent`'s real finding is not about indentation. Censusing **what the
next assistant turn did after every nudge** (`$CLAUDE_JOB_DIR/tmp/afterturn.py`):

| nudge | n | prose-only, no tool call | acted |
|---|---|---|---|
| **same failure (2 runs in a row)** | **50** | **33 (66%)** | 9 |
| open plan tasks | 47 | 12 (26%) | 35 |
| unverified edits | 38 | **0 (0%)** | 38 |
| same failure (3 runs in a row) | 33 | 6 (18%) | 27 |
| repeated call | 31 | 11 (35%) | 11 |
| error unchanged across edits | 27 | 1 (4%) | 26 |
| tests claimed passing, never seen green | 10 | 0 (0%) | 10 |

The highest-volume nudge in the system converts worst, and the median prose
reply is **246 characters — about one sentence**. Which is what it asked for.
Build 103's level-1 branch closed with:

> Then say in one sentence what it expects versus what the code actually
> produces, and make the next edit follow from that sentence.

The model said the sentence. The turn ended having called nothing, and the
next thing it saw was an "open plan tasks" nudge for having done nothing.

**It is the wording, not the population.** The *escalated* branch of the same
note ("Stop editing and open `X`") converts 82%; `unverified edits`, same
imperative shape, converts 100%; `error unchanged across edits` 96%. The
level-1 branch is the only one in the table that asks for words, and it is the
only one that gets them instead of an action.

**Build 108 (`adabb11`)** names the tool, puts the call first (methodology 9),
and says the next thing sent must BE that call. The one-sentence diagnosis
survives as something to do *after* reading, not as an alternative to it.
Swept as `b108-callnotword` against `--base a4ba147` (build 107). Target
metric: the prose-only share after the level-1 note, which is a per-event rate
on 1.8 events/run — not a score, and not subject to 5.27's sizing problem.

**Methodology 19: a steer that asks for narration will be answered with
narration.** Every nudge must name a tool and demand a call. Grade the whole
set on prose-only share; three of the eleven are above 25%.

**How big a move counts.** Splitting the same table by arm gives a free A/A
control, because 5.31 established that b107's two arms were *behaviourally
identical* — the build-107 rescue fired once in 14 runs:

| nudge | base (14 runs) | cand (14 runs) |
|---|---|---|
| same failure (2 runs in a row) | 14/25 = **56%** | 19/25 = **76%** |
| open plan tasks | 6/26 = 23% | 6/21 = 29% |
| repeated call | 5/15 = 33% | 6/16 = 38% |

Twenty points of spread on the headline nudge, from nothing at all. At ~25
events per arm the prose-only rate carries a **±20-point noise band**, so
build 108 has to land under roughly **40%** to be distinguishable from its own
base arm — and the base arm in *its own sweep* is the only fair comparator,
not b107's pooled 66%.

**The histogram lied about this for months.** `evals/harness.py:_nudge_bucket`
fell through to `"malformed"` for any reason not in a keyword list written
before most of these nudges existed, so `b108-callnotword`'s summary reported
**125 malformed tool calls in a sweep with zero of them** — really 77
same-failure, 32 unverified-edits, 7 never-seen-green, 5 repeated-edit, 3
context-compacted, 1 edit-changed-nothing. Fixed in build 109, with a test that
scrapes every literal nudge reason out of `loop.py` and fails if any of them
buckets as malformed. Checked: no conclusion in this file was drawn from that
histogram — every other "malformed" here is the parser's or the syntax guard's,
not the sweep summary's — so nothing above needs revising. It was one bad
number away from mattering.

**Methodology 21: split any per-event rate by arm on a sweep where the arms
were identical, and read the spread as the noise floor.** The archive hands
these A/A controls out for free every time a lever turns out not to fire
(5.30, 5.31); use them instead of guessing at significance.

### 5.31 The indentation lever is closed — its target event no longer exists (2026-08-08)

`b107-indent` (r14, base build 105): the rescue fired **once** in 14 candidate
runs, and the **base arm had zero syntax rejections in 14 runs**. Score
UNDERPOWERED again (W2/L1/T11, 3 informative — it wants 28 per arm). Endings
flat and tight: VERIFIED 0/14 both arms, DONE 4 vs 5, iters 31.2 vs 31.6,
landed edits 8.2 vs 8.0 — a much narrower spread than b106-indent's A/A, as
n=14 should give.

Syntax-guard rejections per run, across the archive:

| sweep | rejections/run | replace_lines/run |
|---|---|---|
| b97-ambig | 1.25 | 0.06 |
| b99-routeorder | 2.31 | 0.62 |
| b101-samefail | 1.19 | 2.62 |
| b102-floor | **0.00** | 1.25 |
| b106-indent | 0.31 | 1.81 |
| b107-indent | **0.04** | 1.82 |

The event the fix targets **collapsed between b101 and b102**, and
`replace_lines` usage rose 40x over the same stretch. Build 98's promotion of
`replace_lines` routed models away from stuffing a multi-line `new` into
`edit_file` at all — the bug was designed out from a different direction
before it was fixed.

**Keep build 107 anyway, and stop sweeping it.** It is a correctness fix with
1137 tests and 121 archive rescues behind it; it costs nothing and fires when
it fires. But it cannot be A/B'd against a base that produces zero of the
event, and pretending otherwise would be reading noise (methodology 12).

**The honest ledger for 5.29–5.31:** a real bug, correctly diagnosed, fixed
twice, worth keeping — and worth **~0.04 events per run** by the time it
landed. Three sweeps (~6h GPU) bought one number: the target was gone. The
cheap check that would have caught it on day one is the first line of the
grader now — count the target event in the CURRENT base arm before building
anything.

### 5.30 The exact tier splices mid-line — build 106 rescued nothing, and proved it (2026-08-08)

`b106-indent` ran 8 paired runs and the rescue fired **zero times**. The score
channel said UNDERPOWERED (W4/L1/T3, +0.094, 5 informative pairs — it needed
10 per arm) exactly as 5.27 predicts, and the mechanism channel said nothing
either: 3 base syntax rejections against 2 candidate, on 8 runs each.

**Then I printed the calls it should have caught** (methodology 14), and every
single one had a **single-line `old`**:

```
old: "return text[:cut] + suffix"
new: "if cut > 0:\n    return text[:cut] + suffix\nelse:\n    return suffix"
```

`old` written without the file's indentation is a **substring** of the indented
line. So `text.count(old)` finds it, the **exact** tier fires, and
`text.replace(old, new)` splices `new` into the middle of that line — the
line's own four spaces in front of `if cut > 0:`, and every later line left at
column 0. The identical 5.29 bug, in the one tier build 106 declared untouched
on the grounds that "an exact match means `old` was reproduced byte for byte,
indentation included". **That is false for a mid-line match**, and mid-line
matches were the entire population.

Why the archive replay missed it: all 87 of its rescues had a *multi-line*
`old`, which cannot match as a substring and so falls to the tolerant tier. The
replay was right about what it measured and silent about what it did not — the
population it drew from (b87–b99) is not the population today's sweeps produce.

**Build 107 (`a4ba147`)** routes the exact tier through `_pick_splice` too, with
`strip=False` (a byte-exact `old` carries the file's indentation, so `new` still
goes in verbatim) and a new requirement that the base column be non-zero — a
match that starts its own line has nothing to anchor onto. Replayed:

| | build 106 | build 107 |
|---|---|---|
| rescued | 87 | **121** |
| still rejected | 1 | 1 |
| unmeasurable (state drift) | 46 | 17 |

The 34 new rescues are **all single-line `old`** — including 17 of b101's 19
and 4 of b106-indent's own 5.

**`b106-indent` is an accidental A/A, and worth keeping as one.** With zero
rescues, build 106 is byte-identical in behaviour to build 105, so the two arms
ran the same code. They still came out:

| | base | cand |
|---|---|---|
| mean iterations | 35.0 | 25.8 |
| mean landed edits | 9.6 | 5.9 |
| DONE | 4/8 | 5/8 |
| syntax rejections | 3 | 2 |
| "unverified edits" nudges | 16 | 3 |
| score | 0.281 | 0.375 |

**That whole table is noise.** It is the strongest calibration datum the
mechanism channel has: at n=8 the arm spread swallows a 26% iteration gap and a
5x nudge-count gap between *identical builds*. Read the next sweep's mechanism
numbers against this, not against zero.

**Methodology 17: a lever that fires zero times has not been tested.** The
sweep reported a difference on every summary line and the difference was
entirely arm noise. Before grading any lever, count how many times the thing
being tested actually happened — which is why the rescue announces itself in
the result text.

### 5.29 We break the model's indentation and then tell it its text is malformed (2026-08-08)

Applying 5.28's methodology-15 ranking to every error message — *what does the
model do NEXT* — put the syntax guard at the top by a distance, and following
it led to a **bug in the edit splice**, not a wording problem.

| message | events | next action |
|---|---:|---|
| **syntax guard** | **56** | **38% another syntax rejection** |
| no-op | 19 | 21% another no-op |
| ambiguous | 42 | 76% a working `replace_lines` (5.28) |
| unread | 16 | **100%** `read_file` |
| not-found | 4 | — (exposure collapsed, 5.25) |

**Reproduced against the real code**, not inferred:

```
file:  def wrap(text, width):
           ...
           if current:
               lines.append(" ".join(current))
           return lines
old:   "if current:\n    lines.append(' '.join(current))\nreturn lines"
new:   the same, with two lines inserted — internally consistent, relative indent

result:    if current:
           lines.append(' '.join(current))     <-- LOST FOUR COLUMNS
       SyntaxError: expected an indented block after 'if' statement
```

`try_edit`'s tolerant and fuzzy tiers splice into a span that begins **after**
the matched line's own indentation, so they strip `new`'s first line:

```python
new_ins = new.lstrip(" \t")
```

That is all they do. Every **later** line of a multi-line `new` keeps whatever
column the model wrote it at — and a model writing a multi-line `new` writes it
*relative*, from column 0. Splice that into a block indented to 4 or 8 and the
second line is no longer deeper than the first.

The message then says **"Your `new` text is malformed"**. It was not; we broke
it. **18 of 27 consecutive rejections resend the byte-identical `new`** — which
is the *correct* response to being told to re-inspect text that is fine, and
the same failure the comment at `fs.py:645` already diagnosed for a different
sub-case. 26 of 27 consecutive pairs carry the identical error line.

**Archive evidence:**

- 203 stored syntax rejections; **169 (83%) carry an indentation-shaped
  SyntaxError** — "expected an indented block" 127, "unexpected indent" 20,
  "unindent does not match" 11, `for`/`try` variants 11.
- 134 whose originating call is recoverable; **129 (96%) have a multi-line
  `new` whose first line sits at column 0** — the exact fingerprint.

**Build 106 (shipped `f038a9e`)** re-anchors `new`: the first line is stripped
as before, and every later line keeps its indentation *relative to that first
line*, shifted onto the matched span's base column.

**Two things the draft got wrong, both caught by running it:**

1. *Re-anchoring unconditionally BREAKS edits that work today.* Four existing
   tests failed immediately. There is a second real `new` shape — first line
   dedented, later lines already carrying the file's absolute columns — and
   nothing in the text tells the two apart (both have a column-0 first line, so
   the 96% fingerprint above does not discriminate). So the anchor is not
   applied on its own judgement. `_pick_splice` keeps the strip-only result
   **unless it turns parseable Python into a SyntaxError and the anchored one
   parses**. That makes this strictly a rescue: every edit that lands today
   lands byte-identically, and only a file we were about to corrupt changes
   hands. It needs the path (`.py` only) so `try_edit` and the diff preview
   both take one now.

2. *A rescue can be worse than a rejection.* Run live, the repro above **landed**
   — with `return lines` moved from column 4 to column 8, i.e. inside the `for`
   loop it used to sit outside. Silent. The model's `old` had flattened a block
   spanning two depths, so its frame said nothing about where the last line
   belonged. `_frame_ok` now requires that `old` reproduced the matched
   region's *shape* (relative indents equal after dedent) before trusting it,
   and that repro is back to being rejected — correctly.

Still declines, as drafted, on tabs in the indentation, on a later line
shallower than the first, and when the span does not begin after pure
whitespace. The exact tier is untouched: a byte-exact `old` means `new` is
already in the file's coordinates. The result says `, re-indented onto the
matched block` when the rescue fires — the model is told we moved its lines,
and the eval archive can count the reach directly.

**Measured reach (replay, `$CLAUDE_JOB_DIR/tmp/replay106b.py`).** Every archived
`edit_file` syntax rejection, replayed against build 106 from the case seed
forward (the event log *clips* tool results, so read_file cannot reconstruct
file state — the first replay attempt read as 65 "unknown" and 19 "not found"
purely from that, and would have said the fix has no reach in the current
sweeps):

| | |
|---|---|
| rescued | **87** |
| still rejected | 1 |
| unmeasurable (replay state drifted) | 46 |

**87 of the 88 judgeable cases (99%)**, and the frame check costs almost
nothing on the real population — every rescue had a multi-line `old`. By sweep:
b99-routeorder 32, b90-editwindow 22, b97-ambig 16, b99-smoke 9, b87 6, b88 2.

**And unlike 5.25, this one can be swept.** 56 events across both arms of the
last two sweeps. `b106-indent` launched against `--base e95c979` (build 105),
`exec-bugfix`, r8 — a clean single-variable experiment. Target metric: syntax
rejections per run and the 38% repeat share. Endings must not fall (methodology
8). Watch the no-op rate — a `new` that now lands could expose an
already-applied state that used to read as a syntax error.

**Methodology 16: run the fix against the archive before spending GPU on it.**
The replay took ten minutes, found both defects' blast radius, and turned "56
events of exposure" into "87 rescues, 99% of what can be judged". It also
caught its own first version being wrong — a reconstruction that silently
degraded to nothing was reporting a *result*, not a failure (methodology 13
again, in a new place).

### 5.28 The ambiguous-match message is SOLVED — take it off the lever list (2026-08-08)

It was queued as "the largest remaining message target" (11 events in the 5.25
census, 20 by b101). That ranking counted **exposure and called it failure**.
Measured on what actually happens after one fires, across `b99-routeorder` and
`b101-samefail`, all arms — 42 ambiguous messages:

| what the model did next | n |
|---|---:|
| **called `replace_lines`** — and it succeeded, every time | **32 (76%)** |
| `write_file` (also succeeded) | 3 |
| ended the turn | 4 |
| `bash` / `ls` | 3 |
| **re-sent the same `old`** | **0** |
| **produced a syntax error** | **0** |

Zero re-sends, against the 43-of-125 that motivated 5.20 in the first place.
This is the best-performing error message in the system, and it earns its keep
by doing exactly what build 105 now does for not-found: name `replace_lines`
first, with the line numbers already in front of the model.

**And the b97 detour was more instructive than its verdict recorded.** b97 is
filed as "won its own metric and killed every run"; the mechanism was never
named. It is this — b97's message led with *"extend `old` with a distinguishing
line from just above or below the match you want (copied verbatim from what is
shown)"*, and `_match_locations` renders those lines **numbered and prefixed**:

```
  ── match at line 23 ──
    22 |          if not current:
    23 |>             current = [word]
```

The model did as it was told, stripped the `NN |` gutter, and could not tell
which of the remaining spaces were the gutter's padding and which were the
code's indentation. It sent `old` = `"if not current:\n    current = [word]"`
— dedented — the tolerant matcher accepted it, and `new` went in at the wrong
column. **SyntaxError.** Then it looped on that.

The counts, per arm:

| sweep / arm | ambiguous | syntax rejections | ambiguous → syntax |
|---|---:|---:|---:|
| b97 base | 17 | 1 | 1 |
| **b97 cand** | 12 | **20** | **8** |
| b99 cand | 11 | 4 | **0** |
| b101 base / cand | 11 / 20 | 16 / 3 | **0 / 0** |

Build 98's reorder did not merely change which route was taken — it **deleted a
downstream failure mode**, 8 → 0, and has kept it at zero across 31 further
ambiguous events. That is the strongest evidence 5.20b has.

Two things carried forward:

- **`_match_locations` still renders a numbered gutter**, and the message still
  offers "extend `old`" as its *last* route. That is survivable only because
  nothing takes that route any more. If the extend route is ever promoted
  again, the gutter has to go first — print verbatim and unnumbered, the way
  `_not_found_help` does, and state the line number in prose beside the block.
  Recorded here so the trap is not re-entered rather than fixed pre-emptively
  on a route with zero traffic (methodology 7).
- **Methodology 15: rank levers by what follows the message, not by how often
  it fires.** Exposure says where to look; the next action says whether there
  is anything to fix.

### 5.27 The score channel cannot grade these changes — a census of 22 sweeps (2026-08-08)

Every verdict since 5.24 has ended the same way: the mechanism moved a long
way and the score said INCONCLUSIVE. I had been reading that as bad luck. It
is not luck — it is the instrument. Measured over the whole archive
(`ab.json` for every sweep that banked at least one pair; script kept at
`$CLAUDE_JOB_DIR/tmp/power.py`):

| | |
|---|---|
| sweeps with pairs | **22** |
| sweeps that ever reached 6 informative pairs | **3** |
| sweeps that ever reached p < 0.05 | **1** |
| total pairs | 194 |
| pairs where the two arms **tied** | **132 (68%)** |

Six informative pairs is the *floor* for p<0.05 to be attainable at all under
the sign-flip test: at n=6 a perfect 6–0 gives p=0.031, and 5–1 gives 0.219.
So a sweep with five informative pairs cannot produce a significant result no
matter how large the effect is. Three sweeps ever cleared that bar, and one of
those (`b83-regression`, 7 informative) only did so by pooling 24 pairs across
eight different cases, which is not one hypothesis. Of the two single-case
sweeps that reached exactly 6, `b97-ambig` came back 1W/5L (p=0.219) and
`b94-infername` came back **6W/0L, p=0.0312, +0.375** — the single credible
score-channel result in the entire project, and it needed a clean sweep of
every informative pair to get there.

Per case, the informative rate and the runs-per-arm it implies:

| case | pairs | informative | rate | runs/arm for 6 informative |
|---|---|---|---|---|
| `exec-bugfix` | 112 | 48 | 43% | **14** |
| `exec-stall-trap` | 20 | 8 | 40% | 15 |
| `e2e-spec-to-code` | 13 | 1 | 8% | 78 |
| `exec-from-plan` | 4 | 2 | 50% | 12 |
| `plan-doc` | 4 | 2 | 50% | 12 |
| `design-doc` | 4 | 1 | 25% | 24 |
| `syntax-fix` | **33** | **0** | **0%** | ∞ |
| `diff-report` | 4 | 0 | 0% | ∞ |

`exec-bugfix` is the workhorse — 112 of the 194 pairs — and **every sweep of
it has run at `-r 8`, when the arithmetic says 14**. At r8 the expected
informative count is 3.4; the threshold is 6. We have been running a test that
usually *cannot* return an answer and then reporting the non-answer as
"inconclusive", as though the change had been weighed and found wanting.

`syntax-fix` is worse than underpowered: **0 of 33 pairs informative**, every
single score 1.0 on both arms. It is saturated and carries no signal at all.
`diff-report` likewise (0 of 4, all 1.0).

**Decisions, taken now:**

- `[x]` **The mechanism/endings channel is the PRIMARY instrument, not the
  fallback.** `evals/armstats.py` (turn endings, VERIFIED) and the arm-split
  mechanism grader are what a lever is judged on. The score becomes a
  guardrail: it can still veto a change by moving *against* it past the noise
  floor, but it can no longer be what earns a KEEP. This is what 5.24/5.24c
  were already doing in practice; it is now the stated method.
- `[x]` **Drop `syntax-fix` and `diff-report` from A/B sweeps.** 37 pairs of
  pure cost. Keep them as regression smoke tests, where a score of 1.0 is
  exactly the point.
- `[ ]` **When a sweep is meant to settle something on score, run
  `exec-bugfix` at `-r 14`, not `-r 8`.** At ~300 s/run that is ~2.3 h per
  arm. Budget it deliberately or don't claim the channel.
- `[ ]` **`ab.py` should say this itself.** It already reports `n_effective`;
  it should also refuse to print a verdict word when `n_effective < min_pairs`
  and instead say *"underpowered: N informative of M pairs; this design could
  not have detected any effect"*. "Inconclusive" reads as evidence about the
  change. It is evidence about the sample.

Methodology rule 13: **an underpowered test does not return "no effect", it
returns nothing — and the two must not be printed with the same word.**

**Correction to an earlier note in this file:** I had recorded that one of the
three threshold-reaching sweeps was an A/A. It was not; the A/A sweeps
(`b94-AA-noisefloor`, `b94-AA2`, `b99-floor`, `b102-floor`) all landed below
the bar, which is itself reassuring — the calibration runs behave like the
real ones.

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
