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

---

## Amendment, 2026-09-06 03:10 — written after ARM D, before ARM E finished

ARM D's counterfactual firing rate falsifies the exposure prediction above:
exec-bugfix 13% (4/30 runs, 7 notes) against the predicted 23%, and exec-ambig
and e2e-spec-to-code fire **zero** times against predicted 23% and 14%.

Cause: the archive estimate pooled across models, and the corpus is 72%
qwencoder14. Broken out, the revert is almost entirely that model's pathology —
qwencoder14 22% of 1672 runs, qythos9 2% of 597, gemmacoder12_4bit 0% of 110.
I measured exposure on the pooled corpus and then aimed the A/B at the model
with the lowest exposure in it.

The score effect still holds within qwencoder14 alone (0.825 vs 0.392 at five
landed edits; 0.587 vs 0.487 at 7+), so the lever is aimed at a real pathology —
just tested on the wrong model.

**ARM F/G (added):** the same three cases on qwencoder14, where per-case firing
is exec-bugfix 25%, exec-ambig 24%, e2e-spec-to-code 24%. Expected ~15 firings
in ARM G against ARM E's ~4.

- **P1' supersedes P1.** ARM G fires >= 10 notes across the three cases.
- **P2 is unchanged and remains the point**, now judged on ARM G's firings by
  reading every one of them (rule 3), not on the score table.
- **P5 (new).** ARM E vs ARM D is now a SAFETY result only: with ~4 firings on
  exec-bugfix and zero on the other two cases, it can show harm but cannot show
  benefit. Reporting it as evidence the fix works would be rule 78 again, in
  the other direction.

## Amendment 3 — build 143 lands mid-sweep (rig only)

Build 143 (workspace snapshotting in `evals/harness.py`) was committed while
ARM F was running. The harness is re-invoked per case, so some ARM F/G cases
will carry a `workspaces/` directory and earlier ones will not.

This does not threaten the comparison. Both arms run their *agent* from frozen
worktrees (`frozen-b141`, `frozen-b142`) via `--agent-root`, which build 143
does not touch. The change is a file copy performed after the checker has run
and the score is fixed; it cannot reach the agent, the events, or any metric.
Recorded here rather than left implicit, because §5.128's doc-track probe had to
be rescued from exactly this shape of surprise.
