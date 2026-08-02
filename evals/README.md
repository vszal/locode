# locode harness evals

A benchmark for the **harness**, not the model. Every case drives a real
`locode -p` against a real local model in a scratch workspace, then scores both
what it produced and how much friction it hit getting there.

Run it between harness changes; gate the change on `compare`.

## Quick start

```
.venv/bin/python evals/harness.py list
.venv/bin/python evals/harness.py run --model qythos9 --label before
# ... make a harness change ...
.venv/bin/python evals/harness.py run --model qythos9 --label after
.venv/bin/python evals/harness.py compare evals/results/before evals/results/after
```

## What `compare` decides, and how

`compare` takes two positional paths — **baseline first, candidate second** —
each either a results dir or its `results.json`.

### Exit codes

| code | verdict | meaning |
|-----:|---------|---------|
| `0` | PASS | no regression the gate is willing to act on. May still print advisory **REVIEW** rows — read them, but they do not block. |
| `1` | FAIL | at least one regression that is both statistically clean and materially large. |
| `2` | INCONCLUSIVE | the two sweeps are **not comparable**, so no verdict was formed. Not a pass and not a failure — re-run, don't interpret. |

Exit `2` is the one to handle deliberately in any script: under `set -e` it
aborts like a failure, but it means "this measurement is void", which usually
calls for re-running rather than reverting.

### When a row counts as a regression

A row must clear *all* of these to hard-FAIL. Each threshold is a module
constant in `harness.py` with the evidence for its value in the comment above it.

| gate | constant | value | why |
|------|----------|------:|-----|
| mean drop | `_GATE_ROW_FLOOR` | `0.10` | below this, never worth flagging |
| separated intervals | — | — | the candidate's CI upper bound must sit below the baseline's lower bound |
| both sweeps consistent | `_GATE_STABLE_STD` | `0.10` | a noisy sweep can only REVIEW; same-code drift reaches ~0.4 at n=6 |
| enough runs | `_GATE_MIN_N` | `4` | fewer cannot revert a change on their own |
| comparable sizes | `_GATE_MAX_N_RATIO` | `2.0` | n=3 against n=8 is not like-for-like |

Intervals are bootstrap CIs widened by a small-n floor (`_GATE_MIN_SE / n`), so
a sample that came back k-for-k identical reads as *weak evidence* rather than
as certainty — the r12 false positive, where a 3/3-identical baseline produced a
zero-width interval and failed a candidate that was merely a little lower.

There is also a **pooled overall backstop**: if at least two trusted rows slide
together and the pooled permutation test clears `_GATE_OVERALL_FLOOR` (`0.05`),
the sweep fails even though no single row crossed its own floor. It catches a
change that mildly hurts everything.

The reported **p-value is advisory**. It fires on same-code sweeps too — it
correctly detects that two draws differ, but cannot know whether the code is
why — so it is never the sole basis for a FAIL.

### When the gate refuses to judge (exit 2)

- the candidate is **missing rows** the baseline has (an interrupted sweep
  averages a different set of cases)
- **throughput collapsed** — under 70% of the baseline's chars/s, or under the
  `MIN_GEN_RATE` floor. Every budget in the loop is a wallclock budget, so a
  slower box reads as a quality regression
- **too many runs could not be graded** — over `_MAX_INVALID_RATE` (20%), or any
  row where no run was graded at all (see below)

### Graded vs ungraded runs

A run that produced no verdict — the checker raised, the case ships no
`check.py`, the turn died on a transport error — is recorded as **ungraded** and
**excluded from every score**, rather than scored `0.0`. Scoring it zero is
indistinguishable from the model having failed the case, which is how a flaky
box turns into an apparent code regression. Reports print an `⚠️ ungraded` line
whenever any run was excluded; silence means everything was graded.

A **harness timeout is deliberately still scored** — the model really did grind
past the case's limit, and that is agent behaviour, not infrastructure.

### Baselines are session-bound

Do not compare against a results.json from another session. These models are
non-stationary: build 30 scored 0.62 on one case in one session and 0.92 on the
same code in another. A saved historical baseline cannot distinguish a real
regression from session drift — run baseline and candidate in the same session,
against the same loaded server.

To read what actually happened inside a run:

```
.venv/bin/python evals/trace.py evals/results/<label>/events/
tail -f evals/results/<label>/stdout/<case>__<model>__r1.txt
```

### Seeing a session as the user sees it

Headless `locode -p` only *streams the model's prose* to stdout — every tool
call, result, and nudge goes to the `--log-events` JSONL and is otherwise
invisible. Scraping that log into "compile=PASS" throws away the turn-by-turn
detail where repeats and failed edits live. `replay.py` closes that gap: it
feeds a recorded event log back through the *same* `locode.ui.render` formatters
the interactive REPL uses, so you read what the user read on screen — then
overlays pathology flags (🔁 repeat call, ∅ no-op, ✗ failed edit, 🛡 syntax-guard
save) and a loud VERDICT header.

```
.venv/bin/python evals/replay.py <events.jsonl | dir>   # verdict + transcript
.venv/bin/python evals/replay.py <events.jsonl> --quiet # verdict only
```

`watch.sh` is the one-command "observe a fresh session": run a task headless,
then replay it.

```
evals/watch.sh "<task prompt>" [model] [workdir]
```

(The model's own narration is streamed, never logged, so it isn't reconstructed —
but every tool call with its args, result, and nudge is.)

## Comparing two versions of the harness honestly

Every case spawns a **fresh `locode` process**, which imports the working tree
as it is *at that moment*. Editing the agent while a sweep runs therefore
changes the thing being measured partway through — silently, since the results
file still records one `git_head`. A sweep now records `git_dirty` and prints a
warning when the tree is modified.

The clean way to A/B two versions of the agent while holding the *measurement*
code constant:

```
git checkout <old-commit> -- locode/     # agent at the old version
.venv/bin/python evals/harness.py run --label old
git checkout HEAD -- locode/             # agent back to current
.venv/bin/python evals/harness.py run --label new
.venv/bin/python evals/harness.py compare evals/results/old evals/results/new
```

This deliberately leaves the tree dirty during the first sweep — that is the
one case where the warning is expected rather than a mistake.

## When you fix a checker

Changing a `check.py` silently breaks every comparison against an older sweep:
the baseline keeps the scores its old checker produced, the candidate gets the
new one, and the gate compares two different rulers. Re-running the baseline is
the wrong fix — it costs an hour of GPU and, since the model is sampled, would
not reproduce those runs anyway.

Re-grade instead. The scratch workspace, event log and stdout of every run are
kept, and grading needs nothing else:

```
.venv/bin/python evals/harness.py rescore evals/results/<label> --dry-run
.venv/bin/python evals/harness.py rescore evals/results/<label>
```

It prints every run whose score moved and rewrites `results.json` in place,
carrying the original `git_head` and `created` stamp forward (the numbers
describe the agent that produced those runs, not whatever is checked out now)
and adding a `rescored` timestamp. Rescoring with unchanged checkers must report
`0 run(s) changed` — if it doesn't, a checker is non-deterministic, which is a
bug in the checker.

## Why two numbers

- **score** — outcome. The fraction of a case's checks that passed. Did it
  produce the design, the plan, the working code?
- **metrics** — friction. Iterations burned, nudges fired (by reason), whether
  a stall or repeat detector tripped, tool error rate, whether the turn ended
  cleanly or hit a budget.

A change that leaves score flat but cuts nudges and iterations is a real
improvement — the model reached the same place with less fighting. One blended
number would hide that, so they are reported side by side.

## Target models

`qwencoder14` and `qythos9`, chosen because their strengths are opposite:
qwencoder14 is the strongest executor and the weakest planner of the models
benchmarked here; qythos9 is the second-best planner and nearly as strong an
executor, at 2–4× the speed. A harness change that only helps one of them is
not a general improvement.

## Adding a case

```
evals/cases/<id>/
    case.json    id, track, description, allow_tools, timeout, weight,
                 optional extra_args (extra locode flags)
    prompt.md    the user turn
    seed/        optional, copied into the scratch workspace
    check.py     optional, `def check(ctx) -> dict[str, bool | float]`
```

`ctx` gives you `workdir`, `events` (parsed JSONL), `stdout`, plus helpers:
`ctx.read(name)` (case-insensitive — models write `DESIGN.md` when told
`design.md`), `ctx.exists(name)`, and `ctx.bash(cmd)` scoped to the workspace.

Two rules learned the hard way:

- **Match with word-boundary regexes over synonym sets**, never loose
  substrings. An earlier benchmark scored a false positive because it grepped
  for `not found` and matched the model's own narration.
- **Verify independently where you can.** A model that writes weak tests can
  make `pytest -q` green without implementing the spec, so the e2e case runs
  its own spec-conformance script against the model's module.

## Layout

```
evals/
    harness.py       runner, event mining, scoring, regression gate
    LOG.md           the improvement loop's running log — rounds, decisions,
                     obstacles, measured deltas
    cases/           the benchmark
    results/<label>/ results.json + per-run events/ and stdout/
```

Scratch workspaces are kept by default (their paths are in `results.json`) so a
failed run can be inspected; pass `--clean` to delete them.
