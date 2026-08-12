# Methodology rules — index

The rules cited by number throughout `ROADMAP.md`, in one place. They were
coined ad hoc across 7,500 lines and never indexed, and the cost of that came
due on 2026-08-11: I wrote §5.93 declaring a sweep-level effect absent while
§4.4 and §5.47 — both mine — had already measured it. An index would have caught
it in a grep.

**Complete for 1–68.** All 55 numbers actually in use are below. Thirteen numbers
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

⚠ = **reconstructed.** The number is cited but never stated outright anywhere in
`ROADMAP.md`; the wording is inferred from its use sites and is **not** a
quotation. Rules 3, 7, 8, 9, 12, 14, 20, 24, 31, 37, 51, 52 — twelve of fifty-two.
Do not quote them as canon. Two need care beyond that: rule 31 is cited at L4424
as "Rule 31, **sharper**", implying an earlier formulation that is not in the
file; rule 52 is cited "as amended" (L6588) with no pre-amendment text on record.
Rule 7's two citations (L2206, L6050) may not even be the same idea.

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
graders. Next free number: **69**.
