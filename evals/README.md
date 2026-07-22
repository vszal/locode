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

`compare` exits non-zero on a regression: any case whose mean score drops more
than 0.15, or an overall score drop over 0.05.

To read what actually happened inside a run:

```
.venv/bin/python evals/trace.py evals/results/<label>/events/
tail -f evals/results/<label>/stdout/<case>__<model>__r1.txt
```

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
