# b141 — the document track, remeasured

qythos9, `design-doc` and `plan-doc`, n=6 each. The document track had not been
measured since ~b134, where qythos9's wallclock/budget stops clustered.

| case | n | score | wallclock stops |
|---|---|---|---|
| design-doc | 6 | 1.000 | 0 |
| plan-doc | 6 | 0.988 | 1 |

Healthy. The one blemish is `plan-doc` r3, which hit `max_wallclock_seconds`
(600) *while generating*, about 12,799 characters into its reply. It still
scored 1.000, because locode landed the partial file before stopping — the
graceful-degradation path doing exactly its job. Worth knowing that the score
hides it: reading scores alone, this sweep looks like a clean 12/12.

Timing margin is the thing to watch rather than correctness. `design-doc` runs
take 174-272s against the 600s budget; `plan-doc` is faster (113-173s) except
for the one run that ran away. Long-document generation is the only place on
this track where the budget is close.

## Note on provenance

This sweep was started against the live tree and build 142 was committed
underneath it mid-run, so it mixes two builds. It is usable anyway, and
checkably so: the b141->b142 delta is entirely inside the `edit_file` path,
gated on a test having failed. The document track calls neither — the whole
sweep's tool usage is `read_file` 25, `write_file` 12, `update_plan` 6, `ls` 2,
`append_file` 1, with zero `bash` and zero `edit_file` — and zero revert notes
fired across all 12 event logs. The contaminating change could not execute here.

The general lesson stands regardless: run A/B arms from frozen worktrees
(`--agent-root`), which is what the b142 sweep does.
