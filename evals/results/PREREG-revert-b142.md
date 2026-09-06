# Pre-registration — build 142 revert note (ARM D vs ARM E)

Written before ARM D started. qythos9, exec-bugfix n=30, exec-ambig n=20,
e2e-spec-to-code n=12 (control).

## Predicted exposure (from the archive, landed edits only, while red)
- exec-bugfix: note fires in ~23% of runs → ~7 of 30 in ARM E
- exec-ambig: ~23% → ~4.6 of 20
- e2e-spec-to-code: ~14% → ~1.7 of 12
- ARM D must fire ZERO times (the code is not there). Any firing = broken arm.

## Predictions
- **P1 (mechanism, rule 17).** In ARM E the `edit reverted to a tested state`
  nudge fires at least 4 times on exec-bugfix. If it fires 0-1 times the sweep
  says nothing and must be re-run larger, whatever the scores do.
- **P2 (the point).** Among ARM E runs where the note fires, the model does NOT
  go on to land a third edit that re-enters the same two-version cycle on that
  file. Measured directly from the event stream, not from score.
- **P3 (safety).** Pooled score on e2e-spec-to-code does not fall by more than
  the noise floor. This is the case where reverts are benign, so the note is a
  pure false positive there and must cost nothing.
- **P4 (score).** exec-bugfix pooled score rises. This is the weakest
  prediction — with ~7 firings per arm it is underpowered, and P1/P2 are what
  the sweep is actually for.

## Stop rule
If P1 fails, report the null and re-run with more repeats rather than reading
the score table (the b141 lesson: do not attribute a delta to a branch that did
not execute — rule 78).
