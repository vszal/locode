# Pre-registration — calibration of multi-defect-deep and contract-spread
Written 2026-09-08 20:07:46, after 1 of 18 runs, before any case's
distribution is visible. Run 1 of contract-spread scored 1.000 (9 iterations).

## Prediction, and the reason for it

**contract-spread will come out at or near ceiling, and the prompt is why.**
It names all three call sites ("report.py, cli.py and cache.py all call it").
The axis the case claims to measure is multi-file CONSISTENCY -- whether a
model finds and updates every site -- and naming them removes the finding.
What is left is three mechanical try/except edits against an explicit list,
which is well within qwen38.

I wrote the prompt that way to keep the task unambiguous. That was the wrong
trade for this case: the ambiguity I removed was the difficulty.

**multi-defect-deep is the genuinely uncertain one.** Its difficulty is in the
defects themselves, not in localisation, and the prompt gives no more away than
multi-defect-blind's does. No prediction registered.

## What I will NOT do
- Not touch either case mid-sweep; that invalidates the runs.
- Not report a flat contract-spread as a finding about qwen38. A case that
  cannot separate models is a broken instrument, not a measurement.

## The fix, if the prediction holds
Stop naming the callers; state the contract change only. Consider a fourth
caller reachable only by grep. Re-validate all three properties, re-calibrate.

---

## Outcome (2026-09-08, appended after the contract-spread block finished)

**Prediction confirmed.** contract-spread scored 1.000 on all six runs:

| run | score | iterations | nudges | wallclock |
|---|---|---|---|---|
| 1 | 1.000 | 9 | 1 | 160.5s |
| 2 | 1.000 | 12 | 2 | 181.0s |
| 3 | 1.000 | 12 | 2 | 183.5s |
| 4 | 1.000 | 9 | 1 | 150.1s |
| 5 | 1.000 | 10 | 1 | 208.4s |
| 6 | 1.000 | 10 | 1 | 189.8s |

Run 1 was visible when the prediction was written and is not evidence for it;
runs 2-6 are five out-of-sample confirmations.

Worth recording because it is not what a "too easy" case usually looks like:
the case is **long but not hard**. At 10.3 iterations mean it costs *more* steps
than `multi-defect-blind` (9.5) and still never drops a check. Iteration count
is measuring the number of mechanical edits the prompt enumerated, not the
difficulty of working out what they should be — which is rule 89 again, from
the other side: iterations do not rank difficulty any more than wallclock does.

Per the registered plan, and per rule 98 (do not touch a case while a sweep
holds it), the fix is drafted but NOT applied: the prompt stops naming callers,
and a fourth caller reachable only by `grep -rl store` — `audit.py`, which says
`from store import get` and so is invisible to the `grep "store.get"` a model
actually runs — is added. Re-validation of all four seed properties and a
re-calibration are owed before any of it counts.
