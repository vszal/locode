# Methodology rules — index

The rules cited by number throughout `ROADMAP.md`, in one place. They were
coined ad hoc across 7,500 lines and never indexed, and the cost of that came
due on 2026-08-11: I wrote §5.93 declaring a sweep-level effect absent while
§4.4 and §5.47 — both mine — had already measured it. An index would have caught
it in a grep.

**Complete for 1–91.** All 78 numbers actually in use are below. Thirteen numbers
were never coined at all: **1, 4, 5, 6, 10, 11, 18, 32, 33, 34, 39, 44, 45** —
verified absent under both `rule N` and `methodology N`, single-line and
line-wrapped. The scheme has gaps; it is not a corrupted sequence.

`rule N` and `methodology N` are the same scheme, used interchangeably.
`ROADMAP.md` is append-only in practice, so the line numbers stay valid; the §
anchor is authoritative if one ever drifts.

## Operative

| # | Rule | Defined at |
|---|---|---|
| 2 | A lever you cannot see is a lever you cannot grade. | L2735 · §5.24d |
| 3 | Read the actual trajectory, not just the aggregate score. ⚠ | L2020 · §5.17 |
| 7 | Don't credit or fix on the strength of your own justification — measure it. ⚠ | L2206 · §5.20 |
| 8 | Ending rates (VERIFIED/DONE) must not fall, whatever else moves. ⚠ | L5979 · §5.29 |
| 9 | Lead a nudge by naming the required tool and putting its call first, before any explanation. ⚠ | L2649 · §5.24b |
| 12 | Don't grade a lever against an event too rare in the current base arm to be anything but noise. ⚠ | L5661 · §5.34 |
| 13 | An underpowered test returns *nothing*, not "no effect". Never print them with the same word. | L6125 · §5.27 |
| 14 | When a lever's exposure can't be explained, print and read the actual calls it should have caught. ⚠ | L5810 · §5.30 |
| 15 | Rank levers by what follows the message, not by how often it fires. Exposure says where to look; the next action says whether there is anything to fix. | L6051 · §5.28 |
| 16 | Run the fix against the archive before spending GPU on it. | L5983 · §5.29 |
| 17 | A lever that fires zero times has not been tested. | L5864 · §5.30 |
| 19 | A steer that asks for narration will be answered with narration. Every nudge must name a tool and demand a call. | L5728 · §5.32 |
| 20 | A lever's per-run exposure must clear a minimum before a given sweep size can gather enough events to grade it. ⚠ | L5653 · §5.34 |
| 21 | Split any per-event rate by arm on a sweep where the arms were identical, and read the spread as the noise floor. | L5760 · §5.32 |
| 22 | Fix the first steer in a cascade, not the loudest one. | L5469 · §5.33 |
| 23 | An A/A does not just size the noise — it re-reads your headline number. | L5436 · §5.33 |
| 24 | A changed population between arms or sweeps is a hidden variable, not noise. ⚠ | L4698 · §5.47 |
| 25 | Build the grader against the OLD sweep before the new one lands. | L5669 · §5.34 |
| 26 | VERIFIED is a within-sweep *difference*, never a level. Never set an arm against an arm from another sweep. | L5532 · §5.36 |
| 27 | A per-nudge rate is a per-RUN draw and decays with depth. Cluster events by run before quoting n. | L5355 · §5.37 |
| 28 | Never bundle a behaviour change into a sweep testing wording. Ship and measure them separately. | L5148 · §5.40 |
| 29 | Before "fixing" a metric in the product, check where the metric is actually computed. | L5153 · §5.40 |
| 30 | A mechanism check must pin every dimension the claim names. | L5015 · §5.42 |
| 31 | A steer can convert perfectly to the losing strategy and still be worse than neutral. ⚠ | L4424 · §5.52 |
| 35 | An A/B pins the code; only a paired design pins the machine. Prove two sweeps shared a server before comparing absolute rates. | L4696 · §5.47 |
| 36 | An A/B cannot grade a lever whose population is itself the dominant source of outcome variance. | L4229 · §5.55 |
| 37 | "No detectable difference" on some metrics is the expected outcome, not a finding. ⚠ | L3780 · §5.64 |
| 38 | Grade a steer by what it converts **to**, not by whether it converts. | L4073 · §5.57 |
| 40 | Pilot a new instrument for whether the lever *fires*, not for whether the metric moves. | L4156 · §5.56 |
| 41 | A metric read from the model's own self-check measures when the arm last looked, not what it left. Check the read point is arm-independent before differencing. | L4094 · §5.57 |
| 42 | An A/A can strengthen a result, not only kill one. | L4010 · §5.59 |
| 43 | A grader's numerator *and* denominator must come from the grader itself, run against an already-closed sweep. | L3575 · §5.67 |
| 46 | Don't trust a prose argument as settled until you have counted. | L3540 · §5.68 |
| 47 | Tool descriptions are read before the model aims; error messages only after it is stuck. Fix descriptions first. | L3324 · §5.71 |
| 48 | Make the primary metric population-independent — defined for every run regardless of which failure it hits. | L3225 · §5.72 |
| 49 | Rule out arm-slot bias before crediting a result. | L3140 · §5.74 |
| 50 | A metric that can only fire once a run reaches a late stage is a survivor marker. Early deaths cannot appear in it. | L6577 · §5.84 |
| 51 | Verify code identity by diffing the actual runtime strings, not the source files. ⚠ | L6299 · §5.78 |
| 52 | Rank candidate defects by measured exposure before treating them as real; correcting a widely-read string is itself a behavioural bet needing its own A/B. ⚠ | L6474 · §5.82 |
| 53 | A guard that voids an experiment must be arm-directional — check which arm triggered it before blaming the candidate. | L6400 · §5.80 |
| 54 | Never let a verdict compute downstream of a pre-registered branch whose precondition failed to fire. A precondition that only `print()`s is not a gate. | L6700 · §5.86 |
| 55 | Every string this harness writes into the ASSISTANT role is a potential echo. Check any new one before it ships. | L6479 · §5.82 |
| 56 | A per-run statistic expressed as a fraction of the run is retrospective and cannot drive a runtime decision. Restate it in absolute iterations, and bin by opportunity, before designing against it. | L6676 · §5.85 |
| 57 | Calibrate before you threshold. A decision rule set against an unmeasured A/A spread is numerology, not pre-registration. | L6794 · §5.86 |
| 58 | Never gate on cross-sweep reproduction of a per-run metric. Sanity-check within-sweep, or on a per-call rate with n in the hundreds. | L6798 · §5.86 |
| 59 | An unread result is worse than no result. | L6802 · §5.86 |
| 60 | Don't rewrite redundant nudge text for aesthetics. Change it only with evidence it costs behaviour. | L7014 · §5.89 |
| 61 | Compute statistical power *before* running a sweep. If the hoped-for effect is smaller than the noise band, don't run it on a per-run threshold. | L6891 · §5.86b |
| 62 | Before attributing any cross-sweep difference, BOTH must hold: (1) diff the two arms' code and state the delta; (2) confirm the same server process ran both. Check (2) with `python evals/metrics.py --servers`. | L7378 · §5.94 |
| 63 | A "did the model comply" classifier must be derived from what the instruction asked for, not from generic novelty — for any nudge that says "go back and look again", the two are opposites. | L7599 · §5.97 |
| 64 | The archive spans your own fixes — never rank a lever on a rate pooled across it. Split the rate by sweep and look for the cliff before the number reaches a recommendation. | L7776 · §5.99 |
| 65 | Mutation-testing a grader must clear `__pycache__` between mutations. A same-size edit restored within the same mtime second leaves stale bytecode that importlib accepts, so the mutated code keeps running against the restored source. | L8024 · §5.102 |
| 66 | Before reporting any absolute `n` from an event-log instrument, check the denominator against the file count. `ab.json` carries one run entry per *arm*, so `for run in runs` with the arm passed separately reads every event file twice. Shares survive a uniform miscount; counts do not. | §5.103 |
| 67 | An A/A calibration expires with its era. Before using one to dismiss an asymmetry, check it against the *current* server process and build — a stale A/A describes a noise floor that has since moved. Re-run it instead. | §5.104 |
| 68 | The treatment label must never reach the subject. No arm name, build number, or condition tag may appear in anything the model can read — the prompt, the cwd, filenames, or tool output — and a rig change that touches any of those channels is not done until an A/A says the arms are indistinguishable. | §5.105 |
| 69 | A defect that was always present cannot, by itself, explain an effect that appeared once. Before crediting a long-standing bug with a new anomaly, find the calibrations that already ran through it — if they are clean, either something changed alongside it or the bug is not the cause. | §5.105 |
| 70 | An A/B arm must never live in a mutable location the running system reads. Swapping files in the working tree to switch arms makes the assignment a race, survives no interruption, and silently reverts the change under test — freeze each arm as its own tree and select it read-only; a runner that mutates shared state must also refuse to start twice. | §5.109 |
| 71 | A case earns its place by discriminating, not by reproducing. Before a case is used as evidence — as a control, a regression guard, or a baseline — run it against a build that predates the behaviour it grades and confirm it scores *differently*. A case that scores the same on both sides of its own fix reports a null that will be read as a pass. | §5.113 |
| 72 | A cost clause in a ship criterion must be denominated per successful outcome, not per run. An absolute cap on tool calls, iterations or wallclock will veto any lever whose mechanism *is* the extra work, and will pass a lever that stays cheap by continuing to fail. State the cap as cost per success, or as a floor on an efficiency the change must not wreck. | §5.116 |
| 73 | A case does not test a failure mode until the workspace forecloses every route that avoids it. Enumerate the escape hatches first — a local copy that can be read, a URL that doubles as a filesystem path, a test suite that names the bug — and close them. An open hatch does not make the case noisy; it makes it measure the model's best path instead of its worst, and report a pass. | §5.119 |
| 74 | A pre-registered clause may be waived only by an objection already on record before the results were seen. Check every clause against the lever's own mechanism when you write it — *could a working version of this lever satisfy this?* — and fix the ones it would have to breach, then. Afterwards a clause with no prior objection stands; failing it is a NO SHIP, and the remedy is a corrected criterion and a re-run, not an argument. | §5.120 |
| 75 | A comparison between buckets of pooled runs means nothing until each score is centred within its (case, model) stratum. Buckets are not randomised — the model chooses which one it lands in, and that choice correlates with the case, so pooled means measure case mix. Report the centred number or none. | §5.121 |
| 76 | A fallback path's event count is not its exposure. Exposure on a fallback is downstream of the primary path's failure rate for the model and build you would ship, so an upstream fix drains the fallback's population without leaving a trace in the fallback's own numbers — count the upstream failures that route into it before scoping a fix to it. | §5.124 |
| 77 | A competitor's default configuration silently redefines the task, so before comparing, determine which mode it actually chose and whether that mode still exercises the failure class you are measuring. | §5.126 |
| 78 | A change can only be blamed for outcomes in runs where its code path actually executed, so before attributing a delta to it, confirm the branch fired in the runs that moved. | §5.127 |
| 79 | A competitor decoding greedily returns the identical run every time, so its repeats are not samples — report it as n=1 and do not quote a spread. | §5.127 |
| 80 | A check that a correct answer can fail on formatting alone measures style, not the requirement; before reading a check's failure as the model's, confirm a right answer in a different format would pass it. | §5.131 |
| 81 | Checks that never vary set a floor and a dead band inside a case's score, so subtract them before reading a delta — a case with an always-passing check compresses every change by that check's weight. | §5.132 |
| 82 | A check that has never failed may be inert or may only be untested by the models in the archive; before calling it a fixed floor, confirm a weaker model also cannot trip it. | §5.133 |
| 83 | Weight substantive checks against scaffolding ones, don't just count them: when "a file exists and contains a keyword" checks outnumber "the work runs and is correct" checks, a model that emits plausible non-working artifacts collects most of the score and the case reports a success it did not earn. | §5.135 |
| 84 | Two sweeps of an identical configuration can disagree at p<0.05, so test homogeneity before pooling them and treat one sweep's mean on a high-variance case as a draw, not the case's value; when pooling is unsafe, grade the claim on the framing most favourable to the arm you are arguing against. | §5.138 |

⚠ = **reconstructed.** The number is cited but never stated outright anywhere in
`ROADMAP.md`; the wording is inferred from its use sites and is **not** a
quotation. Rules 3, 7, 8, 9, 12, 14, 20, 24, 31, 37, 51, 52 — twelve of fifty-two.
Do not quote them as canon. Two need care beyond that: rule 31 is cited at L4424
as "Rule 31, **sharper**", implying an earlier formulation that is not in the
file; rule 52 is cited "as amended" (L6588) with no pre-amendment text on record.
Rule 7's two citations (L2206, L6050) may not even be the same idea.

- **Rule 91: a graded run gets a flat, non-extendable wallclock — the
  progress-extended turn budget is an interactive feature and must never reach
  a bench case.** From build 154 a turn's `max_wallclock_seconds` is a floor,
  not a ceiling: real progress (a tool-call batch new to the turn, a bash that
  exited 0, a plan task completed) pushes the deadline out by
  `progress_grant_seconds`, with **no absolute time ceiling** — a ceiling would
  cap how long an agentic loop may run no matter how well it is going, which
  forecloses the long-running-agent use case. That is the right trade for a
  human at a terminal who can watch and interrupt; it is the wrong one for a
  sweep, twice over. It makes time-to-done incomparable with every archived
  result (the same break rule 90 made to scores, now to the metric rules 88 and
  89 are built on), and it removes the bound that stops one degenerate model
  from running a sweep overnight. Headless `-p` therefore defaults
  `progress_grant_seconds` to 0, which is exactly the pre-154 behaviour, and
  `locode bench` and `evals/harness.py` both drive locode through `-p`. **A new
  case — ours or contributed — must run under a fixed ceiling.** A case that
  sets `--progress-grant` to anything but 0 is not a benchmark. *Enforced, not
  merely documented (build 155):* both runners append `--progress-grant 0`
  **after** `case.extra_args`, so a case cannot buy itself an extendable budget
  — argparse takes the last occurrence. A rule a config file can override is
  not a rule. The same reasoning pins the iteration ceiling
  (`GRADED_MAX_ITERATIONS = 50`, imported by both runners): a graded run must
  not inherit an interactive default that is expected to move, or a re-run
  stops measuring what the archive measured. That pin sits *before*
  `extra_args`, so a harder case may still raise it — a ceiling the case fixes
  is fixed for every model that runs it, which is all comparability requires.
  §5.144, §5.144.1.

## Superseded

| # | Superseded text | Where |
|---|---|---|
| 62 (v1) | "Compute a case's overdispersion before using it in any argument." Withdrawn — the statistic behind it was confounded. | L7221 · §5.92, retracted by §5.93 |
| 62 (v2) | "Diff the two arms' code and state the delta." Correct but insufficient; a server restart alone has moved results as far as a code change. | L7291 · §5.93, extended by §5.94 |

Rule 17 is worded three ways across its three appearances ("a lever that cannot
fire is not a lever" L4504; "fires zero times is untested" L4775; the form above,
L5864). Same rule, refined in place, no amendment.

## Cross-checks worth knowing

- **Rule 19 was coined from reading trajectories and confirmed by measurement later.** §5.97
  measured every nudge: those naming one tool and demanding a call are obeyed
  ~100%; the one offering a menu with an exit ends the turn 61% of the time.
- **Rules 26, 35, 58 and 62 are the same instinct at four depths** — don't read
  an absolute rate across sweeps. 62 is the operative form; it is the only one
  that names the server process.
- **Rules 31 and 38 are near-duplicates** ("grade by what it converts to").
  38 is the quotable one.
- **Rule 65 is rule 51 one layer down.** 51 says verify code identity from the
  runtime strings, not the source file. 65 is the same trap in CPython's import
  cache: the source file can be correct and the running bytecode still not be.
- **Rule 64 is the one 24/26/35/58/62 do not cover.** They all govern *comparing*
  sweeps; 64 governs *pooling* them, which hides the comparison rather than
  making a bad one. That is how §5.98 got through with no comparison in it.

- **Rule 85: validate a grader against a synthetic correct answer and the
  untouched seed before any model runs against it** — a rubric that a correct
  answer cannot score 1.000 on, or that the seed already scores well on, is
  measuring the grader rather than the model. Two instances: the
  `plan_has_tasks` regex accepted two of the four ways a plan numbers its tasks
  and cost eleven of twelve b142 runs a point for formatting (§5.135), and a
  wrong expected total in `cross-module-cause`'s probe made a genuinely correct
  fix score 0.667 (§5.140). Both were invisible without a synthetic correct
  answer to grade.
- **Rule 86 — SUSPENDED 2026-09-08, pending re-test (§5.154): a red test suite
  localises every defect it covers, so a case built on one measures fixing
  rather than finding** — N failing tests is N pointed-at lines, not N units of
  difficulty. The quantitative support is withdrawn: the 4.67 vs 9.50 iteration
  gap (2.04x, "permutation p = 0.0022") had its two arms in two sweeps 26
  minutes apart with no bridging case, so arm and server invocation are
  perfectly aliased and the nuisance effect measured at §5.152 is 2.00x — the
  same size as the signal (rule 98). What stands is the trajectory evidence,
  which is not invocation-sensitive: with the suite the model ran `pytest -q`
  once, was handed five tracebacks naming five functions and made five minimal
  edits; without it, it built its own probe and iterated probe -> fix -> probe.
  Suspended rather than retracted for that reason. Do not cite the numbers.
  §5.140, §5.152, §5.154.
- **Rule 87: read the losing runs before crediting a case with range** — if
  they fail a check for a reason the seed's own documentation licenses, the
  range is measuring the case's prose and will vanish once the contract is
  stated once. `regression-trap` scored 0.853 with two of six perfect; all four
  losses were one guard, failed because the module docstring and the method
  docstring disagreed about what `labels()` returns. Made explicit, it went 8/8
  at 1.000. §5.140.

- **Rule 88: report time-to-done as the user-facing verdict and iterations as
  the cross-session regression metric, never the reverse.** Wall-clock is what
  the user waits through and no reply shape can game it; iterations survives a
  degraded box but is inflated by exactly the single-huge-reply failure mode the
  harness exists to suppress. 13 of 50 archived model-pair comparisons rank the
  two models differently depending on which is used. §5.141.
  *Amended §5.141 (n=3 re-run):* the clause "no reply shape can game it" stands
  as written about reply shape, but must not be read as "wall-clock is
  ungameable" — a model that gives up early is fast for the worst possible
  reason. The rule now governs only the choice *between* time and iterations;
  whether either may decide a comparison at all is rule 89. Original text
  retained above, unamended, per the note on rule 52.

- **Rule 89: no speed metric decides a comparison until correctness has gated
  it — rank on what was solved, and use time or iterations only to break a tie
  among equals.** Both speed metrics are biased, in opposite directions, toward
  a distinct failure mode: iterations flatters the model that dumps one enormous
  reply, and wall-clock flatters the model that quits early. Measured directly
  (§5.141, `--repeat 3`, 24 runs): `qwythos9` finished a ladder pass 35% faster
  than `qwen38` (355s vs 544s) while solving 5/12 runs against 12/12, because
  its three `repro-only` failures took 40s each — quicker than any `qwen38`
  success on the ladder — by editing something plausible and never running the
  code. A time-ranked leaderboard does not merely mis-rank there, it rewards the
  give-up behaviour the nudge machinery exists to suppress. §5.141.
  *Replicated §5.143:* a second n=3 sweep of the identical build put `qwythos9`
  at 9/12 rather than 5/12, and its `repro-only` failures took 349s and 49s —
  so **"giving up is fast" is the weaker half of the finding and did not
  replicate**. The ranking inversion did, both times: faster per pass, fewer
  solved. The rule rests on that, not on the 40-second anecdote.

- **Rule 90: score only the checks a model can earn — a check that is already
  true of the untouched seed is a veto, not a component of the mean.** A
  grader asserts two different kinds of thing and averaging them flat conflates
  them. *Outcomes* ("the tests pass", "the ranking is right") are false at the
  start and true only if the work was done. *Guards* ("the suite is intact",
  "the fixture data is unedited", "the totals still add up") are true at the
  start and can only be lost, by cheating or regressing. Flat averaging paid
  the guards as credit: measured across the four shipped cases, an untouched
  seed scored **0.500, 0.500, 0.500 and 0.000** for changing nothing at all,
  and a run that neutered every assert in the suite also scored 0.500. Declare
  guards (`GUARDS`) and derived aggregates (`DERIVED`) in the grader; the score
  is the mean of what remains, zeroed outright by any lost guard. The `solved`
  verdict is invariant under the change — 1.000 still means every outcome true
  and every guard held — so archived pass/fail carries across the boundary and
  only sub-1.0 magnitudes are on a new scale. §5.142.

- **Rule 92: a negative cache/reuse result from an N-way interleaved probe does
  not transfer to a 1-way workload.** Interleaving is itself an eviction
  pressure: with N conversations sharing one budget, each is evicted by the
  other N−1 before its own next turn arrives, so the probe measures the
  budget's concurrency headroom, not whether reuse works. A three-conversation
  probe reported a **100% cache miss** at both 1.0× and 1.5× and was read as
  proof that prompt-cache reuse was architecturally impossible for a hybrid
  model; the degenerate N=1 case reuses at **1–2% of cold** (0.4 s exact,
  1.1 s prefix-extension, against 70.5 s). Run N=1 before concluding reuse is
  broken — and when the claim is about the *mechanism* rather than the budget,
  test the mechanism directly, without the client-side layers (chat template,
  tokenizer round-trip) that can independently break a prefix. §5.146.

- **Rule 93: On a Metal box, gate co-residency on the *wired* cap, never on
  physical footprint.** They differ by gigabytes and disagree about whether a
  pair fits: two co-resident servers reported a combined physical footprint of
  **20.1 GB** — over the 18.0 GB `iogpu.wired_limit_mb` ceiling — while running
  30 minutes of alternating 41k-context load without a failure, because only
  **15.53 GB** of that was wired. Footprint counts mapped-but-evictable pages;
  the cap counts wired GPU buffers, and the panic fires on the latter. Budget
  `weights x overhead + live KV` against the wired cap and keep footprint as a
  diagnostic. This does not soften §5.146's rule about `ps -o rss` (still
  worthless for Metal, off by 13 GB): `vmmap` footprint is the right way to see
  what a process holds, and the wrong ceiling to admit a second one. §5.147.

- **Rule 94: A git worktree does not change what `locode bench` measures — the
  agent child imports the editable install, so bisect with a venv per build or
  not at all.** `run_case` spawns `python -m locode` with `cwd=<bench
  workspace>` (`locode/bench/runner.py:309`). The worktree is nowhere on that
  child's path, and the editable install's meta-path finder outranks
  `PYTHONPATH` anyway (§5.146's install note), so the child resolves to
  whatever `locode/` currently sits in the main tree. Verified directly: parent
  at `cwd=<worktree b153>` imports build 153, child at `cwd=<tempdir>` imports
  build 160. A five-rung worktree bisect (153/154/155/156/158) therefore varied
  exactly one thing — the **runner's argv** — and produced a clean, plausible,
  entirely spurious step. To bisect the agent, `pip install -e` each worktree
  into its own venv and run that venv's interpreter; to check whether you are
  being fooled, have the case print `locode.__build__` from *inside* the child.
  §5.148. **Rider retracted 2026-09-08:** this rule first blamed the step on the
  `--max-iterations` denominator (150 vs 50 flipping the slow-progress nudge).
  That is false. The 60 s grace means the nudge can only fire at i=2, where
  `2/50 = 0.040` and `2/150 = 0.013` both clear `(64.8/600) x 0.5 = 0.054`, and
  ten runs pinned at 50 nudged — every one. The step was noise (rule 95). The
  rule's *core* — worktrees measure HEAD — was verified directly and stands.
  §5.149.

- **Rule 95: Establish the noise band before attributing a difference to a
  cause — `repro-only` at `temperature = 0.3` spans 5-8 iterations, so compare
  configurations at `temperature = 0.0`, where the harness is exactly
  reproducible.** Measured 2026-09-08: 20 runs across 10 alternating,
  server-restarted invocations put the pooled range at 5-8 with SD 1.05, and a
  deliberately-varied server flag moved the invocation mean by 0.30 iterations
  at exact permutation p = 0.651. Three separate "findings" — a five-rung build
  ladder, a warm/cold server split, and a `--prompt-cache-bytes` split — were
  all smaller than that band and all died at once. Two design corollaries.
  **Power first:** a 3-vs-3 rank test cannot return an exact p below 0.10, so
  three repeats per cell can never reach significance no matter how cleanly the
  cells separate; if the design cannot produce a significant result, do not run
  it as though it can. **Greedy for mechanism:** set `temperature = 0.0` via a
  config copy under `XDG_CONFIG_HOME` (never edit the user's config) and the
  trajectory reproduces byte-for-byte — four such runs settled a question that
  twenty stochastic runs could only bound. Ask for the mechanism *before*
  announcing the result, not after. §5.149.

- **Rule 96: a stored score is a snapshot of the rubric current when it was
  written; when the rubric changes, re-derive every score from its stored
  per-check booleans rather than comparing numbers minted under different
  rules.** This holds for *your own* archive too, and the first attempt to
  apply it got that backwards: §5.150 cleared locode's columns on the strength
  of `harness rescore --dry-run` reporting 0 changes, when what that tool was
  reporting is that it could not re-run the checkers — the scratch workspaces
  were long gone. Re-deriving from the stored booleans instead moved **105
  cells, all downward** (§5.151). So: **a tool that reports "nothing changed"
  has to be asked which question it answered**, and re-derivation must never
  depend on state that expires. Anything graded *outside* the harness — a
  competitor through `grade_external.py`, a hand-graded arm — is doubly exposed,
  keeping a number frozen at the rubric of its day. Store the per-check
  booleans, which survive a rubric change; treat the score as derived. What
  survives a rubric change untouched is the *pass/fail* verdict, because full
  score demands every outcome and every guard either way — measured across the
  whole archive at §5.151, zero solved-counts moved. The §5.136 aider table
  stood wrong for two milestones because an archived 0.500 that actually meant
  "changed nothing, collected the guard floor" was read as partial credit — the
  same misreading §5.142 had already corrected once, for a different tool.
  Corollary: a case's untouched seed must score 0.000, and
  `tests/test_case_seeds.py` enforces it. §5.150, §5.151.

- **Rule 97: Score only what discriminates — a check that never varies across
  the runs that were not all-or-nothing is scaffolding, and paying it into the
  mean is padding, not measurement.** Rule 90 is necessary and not sufficient:
  it declares a guard by asking whether the check is true of the *untouched
  seed*, which is answerable by inspection before any run exists. It cannot
  catch a check that is false on the seed and true of every run that got far
  enough to be partial — that property lives across a sweep, not in one run.
  Measure it with `evals/checkdeps.py`, which restricts to MIXED runs (outcome
  checks not unanimous — the only runs that can separate two checks), then
  calls a check constant-true there `SCAFFOLD`, constant-false `WALL`, and
  identically-moving checks `TWIN`s. Two caveats it exists to enforce: pooling
  all-or-nothing runs manufactures agreement, and perfectly *anti*-correlated
  checks are a trade-off worth keeping, not a duplicate worth merging. Type
  specimen: `exec-stall-trap`'s `escaped_without_grinding` is implied by
  `tests_pass` with zero exceptions in 129 runs, fires 126/129, and pays a third
  of the outcome mean of the case named for it. The fix is to make such a check
  veto as a guard rather than pay. **Applicability, amended at §5.156: this is a
  sweep-accumulation rule, not a calibration gate.** Mixed runs require models
  that get *partway*, so repeats of one model on a new case typically produce
  none — `b160-mdd-calib` gave 0/6, 0/6 and 1/6, and `checkdeps` declined all
  three. Never convert a check on a handful of mixed runs; below `MIN_MIXED` the
  refusal *is* the answer, and it doubles as a headroom reading, since "0/6
  mixed" says the case does not discriminate within that model. §5.153, §5.156.

- **Rule 98: Interleave the arms of a within-model comparison inside one server
  invocation — arms split across sweeps are confounded with a nuisance effect as
  large as anything the suite measures.** §5.152 measured the invocation effect
  on a fixed case and model: `qwen38` on `repro-only` has archived invocation
  means of 5.75, 5.25, 8.25, 6.00, 10.50 — a 2.00x swing with nothing varying
  but the server restart. §5.141's localisation result (`multi-defect-suite`
  4.67 vs `multi-defect-blind` 9.50, a 2.04x gap at "p = 0.0022") put its two
  arms in two sweeps 26 minutes apart with no case bridging them, so arm and
  invocation are perfectly aliased and the nuisance is the same size as the
  signal; the permutation test's twelve exchangeable runs are really one per
  arm. Model-vs-model comparisons cannot obey this rule — two models cannot
  share an invocation — which is a further reason not to decide them on time
  (rule 89). Corollary on how it gets in: the arms there were split by
  chronology, because the second case was built after the first sweep ran.
  §5.152, §5.154.

## Not a rule

- **"the run key is `repeat`, not `rep`"** — a recurring typo, not a rule. Every
  grader from `grade126` onward headed this warning "Rule 43", which is a
  mislabel; `grade125` used the number correctly. Same for "Rule 37: `cmd`, not
  `command`" in `grade126`. Argument-key typos are bugs; they do not get numbers.

## Adding a rule

State it in full at the point of coining, in its own sentence, with the number —
`**Rule N: <one sentence>.**` — and add the row here. A number attached to a
parenthetical is how twelve of the fifty-two entries above ended up reconstructed
rather than quoted, and how one ended up attached to the wrong idea in four
graders. Next free number: **99**.
