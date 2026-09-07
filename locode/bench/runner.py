"""Run the shipped bench cases against one or more model aliases.

Why this measures *time* and not tokens/sec
-------------------------------------------
Generation rate is the number a local-model user reaches for first, and it is
close to useless for picking a model: it says how fast the model types, not how
long the job takes. On `repro-only` (n=20 per model, ROADMAP §5.138) `qwen38`
generates at ~22 chars/s against `qwythos9`'s ~76 — nearly four times slower
per character — and still finishes in 95s against 116s, needing 5.5 steps to 8.7.

Iterations-to-done is a better proxy but still the wrong headline. Of the 50
archived model-pair comparisons with at least two runs per arm, 13 rank them
*differently* depending on whether you sort by iterations or by wall-clock,
and the disagreement is systematic rather than noisy: a model that emits one
enormous reply spends exactly one iteration doing it, so counting steps
flatters the long-single-reply behaviour badly. On `design-doc`, `qwythos9`
used 6.0 iterations to `qwencoder14`'s 15.3 and took 591s to its 274s.

So the report leads with **time-to-done**, which is what the user actually
waits through and which no reply-shape can game, and carries iterations
alongside as the *explanation* for a result rather than the verdict.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).resolve().parent / "cases"

# Ordered easy -> hard, which is also the order they run in: a model that
# cannot do `exec-pinpoint` will not do `multi-defect-blind`, and seeing the
# floor fail early is worth more than a complete table twenty minutes later.
LADDER: list[tuple[str, str]] = [
    ("exec-pinpoint", "warm-up"),
    ("exec-bugfix", "easy"),
    ("repro-only", "medium"),
    ("multi-defect-blind", "hard"),
]

# A case is "solved" only at a full score. Every case on the ladder is one that
# a capable model takes to 1.000, so partial credit here means something was
# left broken -- reporting that as a pass would be the diagnostic lying.
SOLVED = 0.999


@dataclass
class Case:
    id: str
    difficulty: str
    description: str
    path: Path
    prompt: str
    allow_tools: list[str]
    timeout: int
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, difficulty: str = "") -> "Case":
        meta = json.loads((path / "case.json").read_text())
        return cls(
            id=meta.get("id", path.name),
            difficulty=difficulty,
            description=meta.get("description", ""),
            path=path,
            prompt=(path / "prompt.md").read_text().strip(),
            allow_tools=meta.get("allow_tools", [
                "read_file", "write_file", "append_file", "edit_file",
                "replace_lines", "bash", "ls", "grep", "glob",
            ]),
            timeout=int(meta.get("timeout", 900)),
            extra_args=[str(a) for a in meta.get("extra_args", [])],
        )


@dataclass
class CheckCtx:
    """Handed to a case's `check()` function.

    This is the grader contract, and it is defined here rather than in the eval
    harness because the cases under `bench/cases/` are graded by BOTH -- by
    `locode bench` on a user's machine and by `evals/harness.py` during
    development. Two copies of this class would let the same case grade
    differently in each, so the harness imports this one.
    """

    workdir: Path
    events: list[dict]
    stdout: str
    case: Any

    def read(self, name: str) -> str:
        """Case-insensitive read of a file the model was asked to produce.
        Models routinely write DESIGN.md when told design.md (and vice versa),
        which is a naming nit, not a failure — resolve it here so checks test
        content, not casing."""
        p = self.workdir / name
        if p.is_file():
            return p.read_text(errors="replace")
        want = name.lower()
        for cand in self.workdir.rglob("*"):
            if cand.is_file() and cand.name.lower() == want:
                return cand.read_text(errors="replace")
        return ""

    def exists(self, name: str) -> bool:
        return bool(self.read(name).strip())

    def bash(self, cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, shell=True, cwd=self.workdir, timeout=timeout,
                              capture_output=True, text=True)


@dataclass
class BenchResult:
    case: str
    difficulty: str
    model: str
    repeat: int
    score: float
    seconds: float
    iterations: int
    nudges: int
    checks: dict[str, Any] = field(default_factory=dict)
    # Set when the run never measured the model at all -- server down, alias
    # unknown, agent died on startup. Distinct from a low score, and it must
    # stay distinct: "your server is not running" reported as "0/4 solved"
    # would send a user off tuning a model that never loaded.
    infra_error: str | None = None
    timed_out: bool = False

    @property
    def solved(self) -> bool:
        return self.infra_error is None and self.score >= SOLVED


def load_cases(only: list[str] | None = None) -> list[Case]:
    """The ladder, in difficulty order, optionally filtered to `only`."""
    cases = []
    for case_id, difficulty in LADDER:
        d = CASES_DIR / case_id
        if not (d / "case.json").is_file():
            continue
        if only and case_id not in only:
            continue
        cases.append(Case.load(d, difficulty))
    if only:
        missing = set(only) - {c.id for c in cases}
        if missing:
            known = ", ".join(c for c, _ in LADDER)
            raise SystemExit(
                f"no such case(s): {', '.join(sorted(missing))}\navailable: {known}")
    return cases


def parse_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_grader(case: Case):
    """Import a case's `check.py`. Returns `(check_fn, guards, derived)`.

    `guards` and `derived` come from optional module-level `GUARDS` / `DERIVED`
    sets; see `_score` for what they mean. A case with no grader yields
    `(None, (), ())`.
    """
    checker = case.path / "check.py"
    if not checker.is_file():
        return None, frozenset(), frozenset()
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_bench_check_{case.id}", checker)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: a grader that defines a dataclass resolves its
    # annotations through sys.modules[cls.__module__], and a module loaded by
    # path alone is not there.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        return (getattr(mod, "check", None),
                frozenset(getattr(mod, "GUARDS", ())),
                frozenset(getattr(mod, "DERIVED", ())))
    finally:
        sys.modules.pop(spec.name, None)


def _grade(case: Case, ctx: CheckCtx) -> tuple[dict[str, Any], float]:
    """Run the case's `check()` and score it. No grader scores nothing."""
    check, guards, derived = load_grader(case)
    if check is None:
        return {}, 0.0
    checks = dict(check(ctx))
    return checks, _score(checks, guards, derived)


def _score(checks: dict[str, Any], guards=(), derived=()) -> float:
    """Mean of the OUTCOME checks, vetoed to zero by any failed guard.

    Checks are not all the same kind of claim, and averaging them flat made
    partial credit meaningless. A grader asserts three things at once:

    * **outcomes** — true only if the model actually did the work. These, and
      only these, are averaged.
    * **guards** (module-level `GUARDS`) — true of the *untouched seed*, and
      false only if the model cheated or regressed something: it edited the
      tests, rewrote the fixture data, broke a total that already worked. A
      guard can never earn credit, so it is not in the mean; failing one
      vetoes the whole run to 0.0.
    * **derived** (module-level `DERIVED`) — aggregates of the other keys, kept
      for the report. Scoring them double-weights whatever they summarize.

    Flat averaging let a model that changed NOTHING score 0.500 on three of
    the four shipped cases, because every guard is trivially true for a model
    that did nothing (ROADMAP 5.142). Under this split the untouched seed
    scores 0.000 everywhere, which is what it earned. The `solved` verdict is
    unaffected either way: 1.0 still requires every outcome true and every
    guard held, so archived pass/fail stands.
    """
    if not checks:
        return 0.0
    guards, derived = frozenset(guards), frozenset(derived)
    if any(not checks[g] for g in guards if g in checks):
        return 0.0
    vals = [float(bool(v)) if isinstance(v, bool) else float(v)
            for k, v in checks.items() if k not in guards and k not in derived]
    if not vals:  # a grader that declared away every outcome is a grader bug
        return 0.0
    return round(statistics.fmean(vals), 3)


def run_case(case: Case, model: str, repeat: int = 1, keep: bool = False,
             on_start=None, server_args: list[str] | None = None) -> BenchResult:
    """Run one case once against one model, in a throwaway workspace.

    `server_args` are endpoint flags (`--base-url`, `--host`, `--port`) passed
    straight through to the child, so a bench can measure a model served from
    another machine rather than only a local one.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"locode-bench-{case.id}-"))
    log_path = workdir.parent / f"{workdir.name}.events.jsonl"
    if on_start:
        on_start(case, model, repeat)
    try:
        seed = case.path / "seed"
        if seed.is_dir():
            shutil.copytree(seed, workdir, dirs_exist_ok=True)
        setup = case.path / "setup.sh"
        if setup.is_file():
            proc = subprocess.run(["bash", str(setup)], cwd=workdir, text=True,
                                  capture_output=True, timeout=300)
            if proc.returncode != 0:
                return BenchResult(case.id, case.difficulty, model, repeat, 0.0,
                                   0.0, 0, 0,
                                   infra_error=f"setup.sh failed: {proc.stderr[-400:]}")

        # Run the same interpreter's locode, so a bench invoked from a venv
        # measures that venv's build rather than whatever is first on PATH.
        cmd = [sys.executable, "-m", "locode", "-p", case.prompt, "-m", model,
               "--log-events", str(log_path), "--no-markdown", "--no-splash",
               "--allow-tool", ",".join(case.allow_tools)]
        cmd += list(server_args or [])
        cmd += case.extra_args
        # [rule 91] LAST, so it beats anything a case put in extra_args. A
        # graded run gets a flat, non-extendable wallclock: an extendable one
        # makes time-to-done incomparable with every archived sweep and removes
        # the bound that stops one degenerate model running a sweep overnight.
        # Headless already defaults to this; appending it makes the invariant
        # unoverridable rather than merely documented. See ROADMAP 5.144.
        cmd += ["--progress-grant", "0"]

        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(cmd, cwd=workdir, text=True, capture_output=True,
                                  timeout=case.timeout)
            rc, stdout = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            timed_out = True
            rc = -1
            stdout = (e.stdout or b"").decode(errors="replace") if isinstance(
                e.stdout, bytes) else (e.stdout or "")
        seconds = round(time.monotonic() - t0, 1)

        events = parse_events(log_path)
        iterations = sum(1 for e in events if e.get("phase") == "iteration")
        nudges = sum(1 for e in events if e.get("phase") == "nudge")

        # No events at all means the agent never got as far as its first turn.
        # That is an infrastructure failure -- almost always the model server
        # being down or the alias not resolving -- not a model that scored zero.
        if not events:
            tail = "\n".join(stdout.strip().splitlines()[-6:]) or "(no output)"
            return BenchResult(case.id, case.difficulty, model, repeat, 0.0,
                               seconds, 0, 0, timed_out=timed_out,
                               infra_error=f"agent produced no events (rc={rc}):\n{tail}")

        ctx = CheckCtx(workdir=workdir, events=events, stdout=stdout, case=case)
        try:
            checks, score = _grade(case, ctx)
        except Exception as e:  # a broken grader must not abandon the ladder
            return BenchResult(case.id, case.difficulty, model, repeat, 0.0,
                               seconds, iterations, nudges, timed_out=timed_out,
                               infra_error=f"grader raised: {type(e).__name__}: {e}")
        return BenchResult(case.id, case.difficulty, model, repeat,
                           score, seconds, iterations, nudges,
                           checks=checks, timed_out=timed_out)
    finally:
        if keep:
            # The events log is the record of what the model actually did; it
            # is the reason to keep a workspace at all.
            if log_path.exists():
                shutil.move(str(log_path), str(workdir / "events.jsonl"))
            print(f"    workspace kept: {workdir}")
        else:
            log_path.unlink(missing_ok=True)
            shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _cell(rows: list[BenchResult]) -> str:
    """One model's outcome on one case, across its repeats."""
    if any(r.infra_error for r in rows):
        return "ERROR"
    secs = statistics.fmean(r.seconds for r in rows)
    solved = sum(1 for r in rows if r.solved)
    if solved == len(rows):
        mark = "ok"
    elif solved:
        mark = f"{solved}/{len(rows)}"
    else:
        mark = "FAIL" if statistics.fmean(r.score for r in rows) < SOLVED else "ok"
    return f"{mark:<5} {secs:>5.0f}s"


def format_report(results: list[BenchResult], models: list[str],
                  cases: list[Case], repeat: int) -> str:
    by: dict[tuple[str, str], list[BenchResult]] = {}
    for r in results:
        by.setdefault((r.case, r.model), []).append(r)

    w = max([14] + [len(m) for m in models])
    # The case column must fit its longest id with a gap; `multi-defect-blind`
    # is 18 characters and ran straight into the next column at a fixed 18.
    cw = max(18, max((len(c.id) for c in cases), default=0) + 2)
    head = (f"{'case':<{cw}}" + "".join(f"{m:<{w + 4}}" for m in models)).rstrip()
    # Dashes stop two columns short of each field so the rule shows the column
    # boundaries instead of running through them.
    rule = (f"{'-' * (cw - 2):<{cw}}" + "".join(f"{'-' * (w + 2):<{w + 4}}"
                                                for _ in models)).rstrip()
    lines = ["", head, rule]

    for c in cases:
        row = f"{c.id:<{cw}}"
        for m in models:
            row += f"{_cell(by.get((c.id, m), [])):<{w + 4}}" if by.get((c.id, m)) else " " * (w + 4)
        lines.append(row.rstrip())
    lines.append(rule)

    # Totals are per PASS through the ladder, not per run: at --repeat 3 a
    # summed time would report three ladders as one, and the headline number
    # is meant to be "how long do I wait for this work to get done".
    totals = {}
    iters = {}
    for m in models:
        rows = [r for r in results if r.model == m]
        errs = [r for r in rows if r.infra_error]
        solved = sum(1 for r in rows if r.solved)
        secs = sum(r.seconds for r in rows) / repeat
        totals[m] = (solved, len(rows), secs, len(errs))
        iters[m] = sum(r.iterations for r in rows) / repeat

    # `solved` stays a count of RUNS, because at repeat > 1 that count is the
    # reliability number the repeats were run to get: 11/12 says something
    # 4/4 cannot.
    t_label = "time-to-done" if repeat == 1 else "time-to-done/pass"
    i_label = "iterations" if repeat == 1 else "iterations/pass"
    lines.append(f"{'solved':<{cw}}" + "".join(
        f"{f'{totals[m][0]}/{totals[m][1]}':<{w + 4}}" for m in models).rstrip())
    lines.append(f"{t_label:<{cw}}" + "".join(
        f"{f'{totals[m][2]:.0f}s':<{w + 4}}" for m in models).rstrip())
    lines.append(f"{i_label:<{cw}}" + "".join(
        f"{f'{iters[m]:.0f}':<{w + 4}}" for m in models).rstrip())
    lines.append("")

    lines.extend(_verdict(models, totals, repeat))
    return "\n".join(lines)


def _verdict(models: list[str], totals: dict, repeat: int) -> list[str]:
    """The one-line recommendation, hedged honestly when the data can't carry it."""
    broken = [m for m in models if totals[m][3]]
    if broken:
        return [f"! {', '.join(broken)}: runs failed before the model was measured "
                f"(see errors above) — this is not a score."]
    live = [m for m in models if totals[m][1]]
    if not live:
        return ["no runs completed."]
    if len(live) == 1:
        m = live[0]
        solved, n, secs, _ = totals[m]
        return [f"{m}: solved {solved}/{n} in {secs:.0f}s."]

    # More solved wins outright; time only breaks a tie on solved.
    best = sorted(live, key=lambda m: (-totals[m][0], totals[m][2]))
    win, runner = best[0], best[1]
    ws, _, wt, _ = totals[win]
    rs, _, rt, _ = totals[runner]
    if ws > rs:
        why = f"solves more ({ws} vs {rs})"
    elif wt < rt:
        why = f"same {ws} solved, finishes sooner ({wt:.0f}s vs {rt:.0f}s)"
    else:
        return [f"{win} and {runner} are tied on this ladder."]
    out = [f"{win} recommended — {why}."]
    if repeat == 1:
        out.append("  (one run per case; local models vary run to run — "
                   "`--repeat 3` for a firmer answer.)")
    return out
