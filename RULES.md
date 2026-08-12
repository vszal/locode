# Methodology rules — index

The rules cited by number throughout `ROADMAP.md`, in one place. They were
coined ad hoc across 7,400 lines and never indexed, and the cost of that came
due on 2026-08-11: I wrote §5.93 declaring a sweep-level effect absent while
§4.4 and §5.47 — both mine — had already measured it. An index would have caught
it in a grep.

**This index is partial and says so.** It covers 27 numbers. At least 23 more
are cited in `ROADMAP.md` (2, 9, 12, 14–17, 19–23, 25–27, 29–31, 38, 46, 55) and
are not yet extracted. Absence from this table means unindexed, not nonexistent.

`ROADMAP.md` is append-only in practice, so the line numbers below stay valid;
the § anchor is authoritative if one ever drifts.

## Operative

| # | Rule | Defined at |
|---|---|---|
| 3 | Read the actual trajectory, not just the aggregate score. ⚠ | L2020 · §5.17 |
| 7 | Don't credit or fix on the strength of your own justification — measure it. ⚠ | L2206 · §5.20 |
| 13 | An underpowered test returns *nothing*, not "no effect". Never print them with the same word. | L6125 · §5.27 |
| 24 | A changed population between arms or sweeps is a hidden variable, not noise. ⚠ | L4698 · §5.47 |
| 28 | Never bundle a behaviour change into a sweep testing wording. Ship and measure them separately. | L5148 · §5.40 |
| 35 | An A/B pins the code; only a paired design pins the machine. Prove two sweeps shared a server before comparing absolute rates. | L4696 · §5.47 |
| 36 | An A/B cannot grade a lever whose population is itself the dominant source of outcome variance. | L4229 · §5.55 |
| 37 | "No detectable difference" on some metrics is the expected outcome, not a finding. ⚠ | L3780 · §5.64 |
| 40 | Pilot a new instrument for whether the lever *fires*, not for whether the metric moves. | L4156 · §5.56 |
| 41 | A metric read from the model's own self-check measures when the arm last looked, not what it left. Check the read point is arm-independent before differencing. | L4094 · §5.57 |
| 42 | An A/A can strengthen a result, not only kill one. | L4010 · §5.59 |
| 43 | A grader's numerator *and* denominator must come from the grader itself, run against an already-closed sweep. | L3575 · §5.67 |
| 47 | Tool descriptions are read before the model aims; error messages only after it is stuck. Fix descriptions first. | L3324 · §5.71 |
| 48 | Make the primary metric population-independent — defined for every run regardless of which failure it hits. | L3225 · §5.72 |
| 49 | Rule out arm-slot bias before crediting a result. | L3140 · §5.74 |
| 50 | A metric that can only fire once a run reaches a late stage is a survivor marker. Early deaths cannot appear in it. | L6577 · §5.84 |
| 51 | Verify code identity by diffing the actual runtime strings, not the source files. ⚠ | L6299 · §5.78 |
| 52 | Rank candidate defects by measured exposure before treating them as real; correcting a widely-read string is itself a behavioural bet needing its own A/B. ⚠ | L6474 · §5.82 |
| 53 | A guard that voids an experiment must be arm-directional — check which arm triggered it before blaming the candidate. | L6400 · §5.80 |
| 54 | Never let a verdict compute downstream of a pre-registered branch whose precondition failed to fire. A precondition that only `print()`s is not a gate. | L6700 · §5.86 |
| 56 | A per-run statistic expressed as a fraction of the run is retrospective and cannot drive a runtime decision. Restate it in absolute iterations, and bin by opportunity, before designing against it. | L6676 · §5.85 |
| 57 | Calibrate before you threshold. A decision rule set against an unmeasured A/A spread is numerology, not pre-registration. | L6794 · §5.86 |
| 58 | Never gate on cross-sweep reproduction of a per-run metric. Sanity-check within-sweep, or on a per-call rate with n in the hundreds. | L6798 · §5.86 |
| 59 | An unread result is worse than no result. | L6802 · §5.86 |
| 60 | Don't rewrite redundant nudge text for aesthetics. Change it only with evidence it costs behaviour. | L7014 · §5.89 |
| 61 | Compute statistical power *before* running a sweep. If the hoped-for effect is smaller than the noise band, don't run it on a per-run threshold. | L6891 · §5.86b |
| 62 | Before attributing any cross-sweep difference, BOTH must hold: (1) diff the two arms' code and state the delta; (2) confirm the same server process ran both. Check (2) with `python evals/metrics.py --servers`. | L7378 · §5.94 |

⚠ = **reconstructed.** The number is cited but never stated outright anywhere in
`ROADMAP.md`; the wording above is inferred from its use sites and is not a
quotation. Rules 3, 7, 24, 37, 51, 52. Rule 52 is additionally cited "as
amended" (L6588) with no pre-amendment text on record. Rule 7's two citations
(L2206, L6050) may not even be the same idea.

## Superseded

| # | Superseded text | Where |
|---|---|---|
| 62 (v1) | "Compute a case's overdispersion before using it in any argument." Withdrawn — the statistic behind it was confounded. | L7221 · §5.92, retracted by §5.93 |
| 62 (v2) | "Diff the two arms' code and state the delta." Correct but insufficient; a server restart alone has moved results as far as a code change. | L7291 · §5.93, extended by §5.94 |

## Not a rule

- **"the run key is `repeat`, not `rep`"** — a recurring typo, not a rule. Every
  grader from `grade126` onward headed this warning "Rule 43", which is a
  mislabel; `grade125` used the number correctly. Same for "Rule 37: `cmd`, not
  `command`" in `grade126`. Argument-key typos are bugs; they do not get numbers.

## Adding a rule

State it in full at the point of coining, in its own sentence, with the number.
A number attached to a parenthetical is how six of the entries above ended up
reconstructed rather than quoted — and how one of them ended up attached to the
wrong idea in four graders.
