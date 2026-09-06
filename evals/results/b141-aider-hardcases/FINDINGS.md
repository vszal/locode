# b141 — aider on the hard cases, and the build-141 A/B

Full reasoning in `ROADMAP.md` §5.127. Rules coined: 78, 79.

## Build 141 (post-green nudge) — A/B, qythos9, n=6/case

Verdict: **KEEP.** The first read of the score table said "regression"; it was
the wrong statistic.

Decomposing `exec-bugfix` runs at the point the suite first goes green:

| arm | reached green | pre-green iters | post-green tail | `open plan tasks` nudges |
|---|---|---|---|---|
| b140 | 5/6 | 10,10,10,10,(6) | 5,6,5,6,(1) mean 4.6 | 2 per green run |
| b141 | 4/6 | 10,10,10,10 | 3,3,3,3 mean 3.0 | 1 per green run |

Pre-green is identical, which is what an inert-until-green change should do.
The tail falls 2.6 iterations and its variance goes to zero.

Pooled score fell 0.911 → 0.880 because b141 had one extra run die on
`test_truncate_shortens_and_appends_suffix_at_exact_limit`. All three failing
runs across both arms emit **zero** `open plan tasks` nudges — they never
reached green, so the patched branch never executed. The delta is on a path the
change does not touch (rule 78).

## Aider on the hard cases — aider 0.86.2, `--edit-format diff`, `--auto-test`

`grade_external.py` scores aider's workspace with the case's own `check.py`.
It refused `exec-stall-trap` (grades on `ctx.events`, which an external tool
cannot emit) — correct behaviour, 3 gradeable cases remain.

| case | locode b140 | aider | aider edits applied |
|---|---|---|---|
| exec-ambig | 1.000 | 0.500 | 0/6 |
| exec-pinpoint | 1.000 | 0.500 | 0/6 |
| repro-only | 0.833 | 0.500 | 2 per run |

The flags were fair: `--test-cmd "python3 -m pytest -q" --auto-test` was passed.
Aider never got to use the repair loop. On `exec-ambig`/`exec-pinpoint` the
model's whole output is 26 tokens — *"I'll run the tests to see which ones are
failing"* — `--message` mode ends after one exchange, and aider exits having
written nothing.

**Mechanism: aider's repair loop is edit-triggered; locode's is turn-triggered.**
Auto-test only engages once an edit lands, so a model that opens by
investigating gets no second turn. These are cases where investigating first is
the correct opening.

This is why §5.126's null was a null: on `exec-bugfix` the model happens to open
with an edit, so the architectural difference cannot show.

Aider decodes greedily — byte-identical token counts on all six runs of each
case. Its column is n=1 reported six times (rule 79); all the variance in the
table above is locode's.

## Still open

qythos9 grinds on `truncate`'s exact-limit case, emits `same failure (3 runs in
a row)`, and gets stopped after 8 unproductive iterations. Survives build 141.
Next target.
