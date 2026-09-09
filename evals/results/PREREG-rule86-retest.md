# Pre-registration — the rule 86 re-test

Written 2026-09-08, before any run of `b160-rule86-retest` exists, while the
`b160-mdd-calib` sweep still has the GPU.

## What is being re-tested

§5.141 claimed a red test suite localises defects, so a case built on one
measures fixing rather than finding. Evidence: `multi-defect-suite` 4.67
iterations vs `multi-defect-blind` 9.50, a 2.04x gap with non-overlapping sets
and an exact permutation p = 0.0022. Rule 86 was coined from it.

§5.154 withdrew the numbers: the arms ran in two sweeps 26 minutes apart with
no bridging case, so arm and server invocation are perfectly aliased, and the
invocation effect measured on a fixed case+model is 2.00x — the same size as
the claimed effect. Rule 86 is suspended, not retracted, because the trajectory
evidence for the mechanism is direct and is not invocation-sensitive.

## The design

One sweep, one server invocation, both cases interleaved (`harness.py` now
interleaves by construction, 7bc39bc), `qwen38`, `--repeat 6`. Twelve runs.
Label `b160-rule86-retest`.

This is the comparison §5.141 should have been. The arms were split by
chronology, not by choice — `multi-defect-blind` was built after the first
sweep had already run — which is exactly how the confound got in.

## Predictions, registered now

**Primary.** If localisation is real, `multi-defect-suite` comes out
substantially below `multi-defect-blind` *within this single invocation*. I
register the direction and a threshold: rule 86 is supported only if the gap is
at least 1.5x AND the run-level distributions do not overlap. Anything less,
after a 2.00x nuisance has been demonstrated, is not worth a rule.

**Secondary, and the reason I am not confident.** `multi-defect-blind` has now
produced ~9.5 (b150, its own invocation) and 12 on the first run of the current
sweep — stable across two invocations. So the blind arm's number looks robust
and the whole question rests on whether `multi-defect-suite`'s 4.67 was real or
was that invocation running low. `cross-module-cause` sat at exactly 5,5,5,5,5,5
in the same sweep, which is consistent with either reading.

**What would refute rule 86 outright:** the two cases landing within noise of
each other here.

## What I will not do

- Not read the runs as they land and stop early at a congenial point. Twelve
  runs, then analyse.
- Not treat a confirming result as reinstating the original p = 0.0022. That
  number is gone regardless of how this comes out; a fresh test earns a fresh,
  smaller claim.
- Not report iterations without gating on score first (rule 89). If either case
  stops scoring 1.000, the iteration comparison is void.

## Note on sweep hygiene

The tree will report dirty: tracked `.venv` and `locode.egg-info` build
artifacts regenerate on import. No source file differs from `e8fdd70`. Not
reverting them mid-sweep, because the running sweep's spawned `locode`
processes use that editable-install finder.
