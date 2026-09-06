# Aider vs locode on `exec-bugfix` — same model, same box, same day

**Date:** 2026-09-05 · **Model:** `sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx`
(qythos9) served by `mlx_lm.server` on `:8081` with locode's own launch args
(`enable_thinking=false`, `--max-tokens 8192`, `--prompt-cache-bytes 1610612736`).
**Competitor:** aider-chat 0.86.2 in an isolated `~/.aider-venv` (not locode's `.venv`).

The question this answers: *"At every turn we've hit failures with these local
LLMs using tools, stalling out, looping, or otherwise going astray. Compare
against an OSS competitor."*

## Numbers

| | locode b139 | aider `whole` | aider `diff` |
|---|---|---|---|
| runs | 12 | 6 | 6 |
| fully fixed | 11/12 (score 0.958) | 6/6 | 6/6 |
| tests file left intact | 12/12 | 6/6 | 6/6 |
| mean wallclock | 57.6s | 45.7s | 34.0s |
| mean model round trips | **15.2 iterations** | **1.0** | **1.17** |
| edit-apply failures | **0 / 48 `edit_file`** | 0 / 6 | **0 / 7 search-replace** |
| loops / stalls observed | 0 | 0 | 0 |

locode per-run wallclock: 50.4–93.9s. aider `whole`: 32–53s. aider `diff`: 27–61s.

## The premise did not reproduce

**Neither harness had a single edit failure, stall, or loop on this case.**
locode's `edit_file` applied 48/48. Aider's search/replace applied 7/7. Same
model, same task, two completely different edit protocols, zero failures on
both. Whatever drives the tool-failure experience, it is not visible here.

The failure-detection regex was validated against aider's actual source strings
(`SearchReplaceNoExactMatch` in `editblock_coder.py:92`, `Failed to apply edit
to` in `editblock_func_coder.py:133`) — the zero is a live null, not a dead
pattern. One earlier tally reported `malformed: 1`; that was this file's own
regex matching "un**expected**ly" in an unrelated aider warning, and was fixed
before it reached a number.

## The real difference is round trips, not reliability

locode takes **13× the model round trips** for the same outcome at comparable
wallclock. A representative locode trajectory (r1) is disciplined, not flailing:

```
test(red) → read → plan → edit → test(red) → plan → edit → test(red)
  → plan → edit → test(GREEN) → test(GREEN, redundant) → plan(4/4) → done
```

Three surgical edits, each verified against the suite before the next. The 44
"tool errors" in the b139 metrics are the *failing pytest runs* — correct
observations of a red suite, not malfunctions.

About **5 of 16 iterations are post-green tail**: a redundant second green test
run plus plan-closing turns. That matches the nudge histogram exactly
(`open plan tasks: 14`, `verify task credited: 4`). This is the one measured
inefficiency, and it is bookkeeping, not error recovery.

## Why aider needs one round trip — and what it gives up

Aider is not doing the same job. The files were named on its command line, so
there is no discovery phase; with `--no-git` its repo-map is disabled, so it
**cannot** find a file it was not handed. locode discovers, plans, edits,
verifies, and closes a plan. Aider's loop is: put the files in the prompt, ask
for one edit payload, apply it deterministically, run `--test-cmd`, re-prompt
only on failure. `diff`-r1 is the only run that used that repair round — the
first pass fixed 1 of 3 bugs, `--auto-test` caught the other two, the second
pass closed them. That is aider's design working, not an edit failure.

## A trap worth flagging: aider silently chose `whole`

Because this model is not in aider's metadata DB, aider defaulted to the
**`whole` edit format** — the model rewrites the entire file, which removes the
edit-match failure class *by construction* and costs ~40% more output tokens
(922 vs 656 received). Comparing locode's search/replace against that would
have manufactured a reliability gap that does not exist. Hence the second arm
with `--edit-format diff`, which is the true analogue of `edit_file` — and
which also scored zero failures.

Related, and available to locode already: `write_file` is in `exec-bugfix`'s
`allow_tools`, and across all 12 locode runs the model chose `edit_file` 48
times and `write_file` **0** times. The whole-file strategy aider fell into was
on locode's menu and never selected. Here it cost nothing.

## Limitation — read before quoting this

`exec-bugfix` is an easy case: one 83-line file, three bugs all visible in a
single read, a green/red oracle on every turn. It does **not** exercise
stalling, looping, or going astray, and it did not elicit them from either
tool. This comparison therefore shows that *on an easy single-file case the two
harnesses are equivalent in reliability and differ 13× in round trips*. It is
not evidence about the hard cases where the reported failures actually live.
Reproducing those needs a harder or multi-file case; until one is run, no claim
about locode-vs-aider robustness under stress is supported.
