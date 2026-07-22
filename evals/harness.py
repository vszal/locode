#!/usr/bin/env python3
"""locode eval harness — measure whether the *harness* helps a weak local model
go spec -> design -> plan -> working code without stalling.

This is deliberately not a pytest suite: cases drive a real `locode -p` against
a real local model server, take minutes each, and are nondeterministic. It is a
benchmark with a regression gate, run by hand (or by an agent) between harness
changes.

Layout
------
  evals/cases/<case-id>/
      case.json     required. See CASE SCHEMA below.
      prompt.md     required. The user turn handed to `locode -p`.
      seed/         optional. Copied into the scratch workspace before the run.
      check.py      optional. `def check(ctx) -> dict[str, bool|float]` —
                    case-specific outcome checks (files written, tests green).

CASE SCHEMA (case.json)
-----------------------
  id            str    stable identifier (should match the directory name)
  track         str    "design" | "plan" | "execute" | "e2e"
  description   str    one line, for the report
  allow_tools   [str]  passed to --allow-tool
  timeout       int    hard subprocess kill, seconds (should exceed the
                       agent's own max_wallclock_seconds so we observe
                       locode's own budget stop rather than masking it)
  weight        float  optional, default 1.0 — relative importance in the score

Scoring
-------
Every case yields a `score` in [0,1] (the mean of its check results) plus
process metrics mined from the JSONL event log: iterations used, nudges by
reason, whether a stall/repeat detector fired, tool error rate, stop reason.

Score is *outcome*; the metrics are *how painfully it got there*. A harness
change that keeps score flat while cutting nudges and iterations is still a
win, so `compare` reports both.

Usage
-----
  python evals/harness.py run  [--case ID]... [--model ALIAS]... [--repeat N]
  python evals/harness.py report  RESULTS.json
  python evals/harness.py compare BASELINE.json CANDIDATE.json
  python evals/harness.py list
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
CASES_DIR = EVALS_DIR / "cases"
RESULTS_DIR = EVALS_DIR / "results"
REPO_ROOT = EVALS_DIR.parent
# Run the installed-in-place locode from the repo venv so we always measure the
# working tree, never a stale site-packages copy.
LOCODE_BIN = REPO_ROOT / ".venv" / "bin" / "locode"


# --------------------------------------------------------------------------
# case loading
# --------------------------------------------------------------------------
@dataclass
class Case:
    id: str
    track: str
    description: str
    path: Path
    prompt: str
    allow_tools: list[str] = field(default_factory=list)
    timeout: int = 900
    weight: float = 1.0
    # Extra `locode` flags for this case, e.g. a bigger budget for the
    # end-to-end case than a one-file bugfix needs.
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Case":
        meta = json.loads((path / "case.json").read_text())
        return cls(
            id=meta.get("id", path.name),
            track=meta.get("track", "execute"),
            description=meta.get("description", ""),
            path=path,
            prompt=(path / "prompt.md").read_text().strip(),
            allow_tools=meta.get("allow_tools", ["read_file", "write_file",
                                                 "edit_file", "bash", "ls",
                                                 "grep", "glob"]),
            timeout=meta.get("timeout", 900),
            weight=float(meta.get("weight", 1.0)),
            extra_args=[str(a) for a in meta.get("extra_args", [])],
        )


def discover_cases(only: list[str] | None = None) -> list[Case]:
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        if not (d / "case.json").is_file():
            continue
        if only and d.name not in only:
            continue
        cases.append(Case.load(d))
    if only:
        missing = set(only) - {c.id for c in cases}
        if missing:
            raise SystemExit(f"no such case(s): {', '.join(sorted(missing))}")
    return cases


# --------------------------------------------------------------------------
# event-log mining
# --------------------------------------------------------------------------
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
        except ValueError:
            continue  # a torn last line from a killed process
    return out


def metrics_from_events(events: list[dict]) -> dict:
    """Process metrics: how much friction the run hit, independent of outcome."""
    iters = [e for e in events if e.get("phase") == "iteration"]
    runs = [e for e in events if e.get("phase") == "run"]
    results = [e for e in events if e.get("phase") == "result"]
    nudges = [e for e in events if e.get("phase") == "nudge"]
    stopped = next((e for e in events if e.get("phase") == "stopped"), None)
    turn_end = next((e for e in events if e.get("phase") == "turn_end"), None)

    nudge_reasons = Counter(_nudge_bucket(e.get("reason", "")) for e in nudges)
    tool_calls = Counter(e.get("name", "?") for e in runs)
    errors = [e for e in results if e.get("error")]

    return {
        "iterations": len(iters),
        "tool_calls": sum(tool_calls.values()),
        "tool_calls_by_name": dict(tool_calls),
        "tool_errors": len(errors),
        "tool_error_rate": round(len(errors) / len(results), 3) if results else 0.0,
        "nudges": len(nudges),
        "nudges_by_reason": dict(nudge_reasons),
        "stop_reason": (stopped or {}).get("reason"),
        "clean_finish": stopped is None,
        "wallclock": round(_last_stamp(events), 1),
        "model_seconds": _model_seconds(events),
        # Did the model decompose the request at all, and did it stick with it?
        # Whether update_plan gets used WITHOUT being asked for is the whole
        # question for a tool that only helps if the model discovers it.
        "plan_updates": tool_calls.get("update_plan", 0),
        # Replies cut off at max_tokens. A run that finishes clean but truncated
        # repeatedly is telling us the cap is too tight for the task.
        "truncations": sum(1 for e in events if e.get("phase") == "truncated"),
    }


def _last_stamp(events: list[dict]) -> float:
    """Seconds from process start to the last event. Prefers `turn_end`, but
    falls back to whatever arrived last, because a killed run has no turn_end
    at all and its duration is exactly what we most want to see."""
    for e in reversed(events):
        if e.get("phase") == "turn_end":
            return float(e.get("t", 0.0))
    return float(events[-1].get("t", 0.0)) if events else 0.0


def _nudge_bucket(reason: str) -> str:
    """Collapse a nudge reason to a stable bucket (reasons embed details like
    the specific missing filename, which would fragment the histogram)."""
    r = reason.lower()
    for key in ("empty response", "truncated", "repeated call", "unchanged",
                "missing deliverable", "slow progress", "open plan tasks",
                "announced intent"):
        if key in r:
            return key
    return "malformed" if r else "other"


def _model_seconds(events: list[dict]) -> float:
    """Wallclock spent waiting on the model, i.e. total minus time in tools.
    Separates 'the model is slow' from 'the tools are slow'."""
    total = 0.0
    for e in events:
        if e.get("phase") == "turn_end":
            total = e.get("t", 0.0)
    tool_time = sum(e.get("seconds", 0.0) for e in events
                    if e.get("phase") == "result")
    return round(max(0.0, total - tool_time), 1)


# --------------------------------------------------------------------------
# running one case
# --------------------------------------------------------------------------
@dataclass
class RunResult:
    case: str
    track: str
    model: str
    repeat: int
    score: float
    checks: dict
    metrics: dict
    returncode: int
    timed_out: bool
    seconds: float
    workdir: str
    error: str = ""


def _load_checker(case: Case):
    checker = case.path / "check.py"
    if not checker.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"check_{case.id}", checker)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "check", None)


@dataclass
class CheckCtx:
    """Handed to a case's check() function."""
    workdir: Path
    events: list[dict]
    stdout: str
    case: Case

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


def run_case(case: Case, model: str, repeat: int, results_dir: Path,
             keep: bool = True) -> RunResult:
    stamp = f"{case.id}__{model}__r{repeat}"
    workdir = Path(tempfile.mkdtemp(prefix=f"locode-eval-{stamp}-"))
    seed = case.path / "seed"
    if seed.is_dir():
        shutil.copytree(seed, workdir, dirs_exist_ok=True)

    log_path = results_dir / "events" / f"{stamp}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # --log-events APPENDS (a user's session log must not be destroyed by
    # pointing at it twice). Re-running a label would then splice two runs into
    # one file and double-count every metric mined from it, so the harness owns
    # clearing the slot.
    log_path.unlink(missing_ok=True)
    out_path = results_dir / "stdout" / f"{stamp}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(LOCODE_BIN), "-p", case.prompt, "-m", model,
           "--log-events", str(log_path), "--no-markdown",
           "--allow-tool", ",".join(case.allow_tools)] + case.extra_args

    env = dict(os.environ)
    env["NO_COLOR"] = "1"

    t0 = time.monotonic()
    timed_out = False
    rc = -1
    try:
        # Stream straight to disk instead of capturing. A single case can run for
        # ten minutes; being able to `tail -f` the transcript is the only window
        # into what the model is doing while it is still doing it.
        with out_path.open("w") as out_fh:
            proc = subprocess.Popen(cmd, cwd=workdir, env=env, text=True,
                                    stdout=out_fh, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=case.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                out_fh.write("\n[TIMEOUT: harness killed the process]\n")
    except FileNotFoundError:
        return RunResult(case.id, case.track, model, repeat, 0.0, {}, {}, -1,
                         False, 0.0, str(workdir),
                         error=f"locode not found at {LOCODE_BIN}")
    seconds = round(time.monotonic() - t0, 1)
    stdout = out_path.read_text(errors="replace")

    events = parse_events(log_path)
    metrics = metrics_from_events(events)
    metrics["harness_timeout"] = timed_out

    checks: dict = {}
    err = ""
    checker = _load_checker(case)
    if checker:
        ctx = CheckCtx(workdir=workdir, events=events, stdout=stdout, case=case)
        try:
            checks = dict(checker(ctx))
        except Exception as e:  # a broken checker must not lose the whole run
            err = f"checker raised: {type(e).__name__}: {e}"
    score = _score(checks)

    if not keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return RunResult(case.id, case.track, model, repeat, score, checks, metrics,
                     rc, timed_out, seconds, str(workdir), error=err)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the timed-out run and anything it spawned.

    locode's bash tool starts child processes; killing only the parent would
    leave those holding the scratch dir (and the GPU) after the case is over.
    The run is in its own session, so one killpg reaches all of them.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def _score(checks: dict) -> float:
    if not checks:
        return 0.0
    vals = [1.0 if v is True else 0.0 if v is False else float(v)
            for v in checks.values()]
    return round(sum(vals) / len(vals), 3)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def summarize(runs: list[RunResult]) -> dict:
    by_case: dict[str, list[RunResult]] = {}
    for r in runs:
        by_case.setdefault(f"{r.case}::{r.model}", []).append(r)

    rows = {}
    for key, group in sorted(by_case.items()):
        scores = [r.score for r in group]
        rows[key] = {
            "case": group[0].case,
            "track": group[0].track,
            "model": group[0].model,
            "n": len(group),
            "score_mean": round(statistics.mean(scores), 3),
            "score_min": round(min(scores), 3),
            "iterations_mean": round(statistics.mean(
                [r.metrics.get("iterations", 0) for r in group]), 1),
            "nudges_mean": round(statistics.mean(
                [r.metrics.get("nudges", 0) for r in group]), 1),
            "clean_finish_rate": round(statistics.mean(
                [1.0 if r.metrics.get("clean_finish") else 0.0 for r in group]), 3),
            "seconds_mean": round(statistics.mean([r.seconds for r in group]), 1),
            "stop_reasons": [r.metrics.get("stop_reason") for r in group
                             if r.metrics.get("stop_reason")],
        }

    weights = {r.case: 1.0 for r in runs}
    overall = round(statistics.mean([r.score for r in runs]), 3) if runs else 0.0
    return {
        "overall_score": overall,
        "clean_finish_rate": round(statistics.mean(
            [1.0 if r.metrics.get("clean_finish") else 0.0 for r in runs]), 3)
        if runs else 0.0,
        "total_nudges": sum(r.metrics.get("nudges", 0) for r in runs),
        "total_iterations": sum(r.metrics.get("iterations", 0) for r in runs),
        "nudge_histogram": dict(sum(
            (Counter(r.metrics.get("nudges_by_reason", {})) for r in runs),
            Counter())),
        "rows": rows,
        "_weights": weights,
    }


def print_report(summary: dict, title: str = "") -> None:
    if title:
        print(f"\n=== {title} ===")
    print(f"overall score      : {summary['overall_score']:.3f}")
    print(f"clean-finish rate  : {summary['clean_finish_rate']:.3f}")
    print(f"total iterations   : {summary['total_iterations']}")
    print(f"total nudges       : {summary['total_nudges']}  "
          f"{summary['nudge_histogram'] or ''}")
    print()
    hdr = f"{'case':<26}{'model':<14}{'n':>2} {'score':>6} {'iter':>5} " \
          f"{'nudge':>6} {'clean':>6} {'secs':>7}"
    print(hdr)
    print("-" * len(hdr))
    for row in summary["rows"].values():
        print(f"{row['case']:<26}{row['model']:<14}{row['n']:>2} "
              f"{row['score_mean']:>6.2f} {row['iterations_mean']:>5.1f} "
              f"{row['nudges_mean']:>6.1f} {row['clean_finish_rate']:>6.2f} "
              f"{row['seconds_mean']:>7.1f}")
        for sr in dict.fromkeys(row["stop_reasons"]):
            print(f"    ⏹ {sr}")


def compare(baseline: dict, candidate: dict) -> int:
    """Regression gate. Returns a process exit code: 0 = pass, 1 = regression."""
    print_report(baseline, "BASELINE")
    print_report(candidate, "CANDIDATE")

    b, c = baseline["overall_score"], candidate["overall_score"]
    bc, cc = baseline["clean_finish_rate"], candidate["clean_finish_rate"]
    print("\n=== DELTA ===")
    print(f"overall score     : {b:.3f} -> {c:.3f}  ({c - b:+.3f})")
    print(f"clean-finish rate : {bc:.3f} -> {cc:.3f}  ({cc - bc:+.3f})")
    print(f"total nudges      : {baseline['total_nudges']} -> "
          f"{candidate['total_nudges']}")
    print(f"total iterations  : {baseline['total_iterations']} -> "
          f"{candidate['total_iterations']}")

    regressions = []
    for key, brow in baseline["rows"].items():
        crow = candidate["rows"].get(key)
        if crow is None:
            continue
        # Per-case tolerance: a single flaky repeat shouldn't fail the gate, but
        # a case that drops more than one full check is a real regression.
        if crow["score_mean"] < brow["score_mean"] - 0.15:
            regressions.append(
                f"{key}: score {brow['score_mean']:.2f} -> {crow['score_mean']:.2f}")
    if c < b - 0.05:
        regressions.append(f"overall score {b:.3f} -> {c:.3f}")

    if regressions:
        print("\n❌ REGRESSION GATE: FAIL")
        for r in regressions:
            print("   - " + r)
        return 1
    print("\n✅ REGRESSION GATE: PASS")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def cmd_run(args) -> int:
    cases = discover_cases(args.case or None)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = args.label or stamp
    results_dir = RESULTS_DIR / label
    results_dir.mkdir(parents=True, exist_ok=True)

    if _git_dirty():
        print(f"!! working tree is dirty at {_git_head()} — each case spawns a "
              "fresh locode and imports the tree AS IT IS THEN, so edits made "
              "during this sweep change what is being measured. Fine for a "
              "probe; do not use these numbers as a baseline.\n", flush=True)

    runs: list[RunResult] = []
    total = len(cases) * len(args.model) * args.repeat
    n = 0
    for model in args.model:
        for case in cases:
            for rep in range(1, args.repeat + 1):
                n += 1
                print(f"[{n}/{total}] {case.id} · {model} · run {rep}…",
                      flush=True)
                r = run_case(case, model, rep, results_dir, keep=not args.clean)
                runs.append(r)
                flag = "ok" if r.metrics.get("clean_finish") else "STOPPED"
                print(f"        score={r.score:.2f} iters={r.metrics.get('iterations')} "
                      f"nudges={r.metrics.get('nudges')} {r.seconds}s {flag}"
                      + (f"  [{r.error}]" if r.error else ""), flush=True)
                # Persist after every run: a long batch that dies partway is
                # still worth the runs it completed.
                _persist(results_dir, runs, label)
    summary = summarize(runs)
    print_report(summary, f"RESULTS · {label}")
    print(f"\nwrote {results_dir / 'results.json'}")
    return 0


def _persist(results_dir: Path, runs: list[RunResult], label: str,
             provenance: dict | None = None) -> None:
    """Write results.json. `provenance` carries forward the git head/created
    stamp of an ORIGINAL sweep when this is a rescore — the numbers describe the
    agent that produced those runs, not whatever is checked out at grading time,
    and stamping today's HEAD on them would quietly mislabel the baseline."""
    payload = {
        "label": label,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_head": _git_head(),
        "git_dirty": _git_dirty(),
        "runs": [asdict(r) for r in runs],
        "summary": summarize(runs),
    }
    if provenance:
        payload.update(provenance)
    (results_dir / "results.json").write_text(json.dumps(payload, indent=2))


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=REPO_ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return "?"


def _git_dirty() -> bool:
    """Is the working tree modified relative to HEAD?

    This matters more here than it looks. Every case spawns a FRESH `locode`
    process, which imports the working tree as it is *at that moment* — so
    editing the agent while a sweep runs silently changes the thing under test
    partway through, and the results file still claims a single clean git_head.
    (Lost a sweep to exactly this.) A sweep on a dirty tree is fine for probing;
    it is not a baseline, and the results must say so.
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                             cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return bool(out)
    except Exception:
        return False


def _load_results(path: str) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "results.json"
    return json.loads(p.read_text())


def cmd_report(args) -> int:
    data = _load_results(args.results)
    print_report(data["summary"], f"{data['label']} @ {data.get('git_head', '?')}")
    return 0


def cmd_compare(args) -> int:
    b = _load_results(args.baseline)
    c = _load_results(args.candidate)
    return compare(b["summary"], c["summary"])


def cmd_rescore(args) -> int:
    """Re-grade a finished sweep with the CURRENT checkers and event miners.

    Fixing a checker bug used to poison the whole comparison: the baseline kept
    the scores its old checker produced, the candidate got the new one, and the
    gate silently compared two different rulers. Re-running the baseline instead
    costs an hour of GPU and — because the model is sampled, not deterministic —
    would not reproduce the same runs anyway.

    Nothing about grading needs the model: the scratch workspace, the event log
    and the stdout of every run are all kept. So re-grade in place. Scores and
    metrics are recomputed; timings and return codes are left exactly as the
    original run recorded them.
    """
    path = Path(args.results)
    results_dir = path if path.is_dir() else path.parent
    data = _load_results(args.results)
    cases = {c.id: c for c in discover_cases()}

    runs: list[RunResult] = []
    changed = 0
    for raw in data["runs"]:
        old_score = raw.get("score", 0.0)
        case = cases.get(raw["case"])
        workdir = Path(raw.get("workdir", ""))
        if case is None or not workdir.is_dir():
            why = ("case no longer exists" if case is None
                   else "scratch workspace is gone (run with --clean?)")
            print(f"  !! {raw['case']} · {raw['model']} — {why}; kept as-is")
            runs.append(RunResult(**raw))
            continue

        stamp = f"{raw['case']}__{raw['model']}__r{raw['repeat']}"
        events = parse_events(results_dir / "events" / f"{stamp}.jsonl")
        out_path = results_dir / "stdout" / f"{stamp}.txt"
        stdout = out_path.read_text(errors="replace") if out_path.is_file() else ""

        metrics = metrics_from_events(events)
        metrics["harness_timeout"] = raw.get("timed_out", False)
        checks, err = {}, ""
        checker = _load_checker(case)
        if checker:
            ctx = CheckCtx(workdir=workdir, events=events, stdout=stdout,
                           case=case)
            try:
                checks = dict(checker(ctx))
            except Exception as e:
                err = f"checker raised: {type(e).__name__}: {e}"
        score = _score(checks)

        raw = dict(raw, score=score, checks=checks, metrics=metrics, error=err)
        runs.append(RunResult(**raw))
        if abs(score - old_score) > 1e-9:
            changed += 1
            print(f"  {raw['case']:<20} {raw['model']:<14} "
                  f"{old_score:.3f} -> {score:.3f}")

    label = data.get("label", results_dir.name)
    if args.dry_run:
        print(f"\n{changed} run(s) would change; --dry-run, nothing written")
    else:
        _persist(results_dir, runs, label, provenance={
            "created": data.get("created", "?"),
            "git_head": data.get("git_head", "?"),
            "git_dirty": data.get("git_dirty", False),
            "rescored": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        print(f"\n{changed} run(s) changed; rewrote "
              f"{results_dir / 'results.json'}")
    print_report(summarize(runs), f"RESCORED · {label}")
    return 0


def cmd_list(args) -> int:
    for case in discover_cases():
        print(f"{case.id:<26} [{case.track:<7}] {case.description}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evals/harness.py",
                                description="locode harness benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run cases against models")
    r.add_argument("--case", action="append", default=[],
                   help="case id (repeatable); default all")
    r.add_argument("--model", action="append", default=[],
                   help="model alias (repeatable); default qwencoder14+qythos9")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--label", help="results dir name (default: timestamp)")
    r.add_argument("--clean", action="store_true",
                   help="delete scratch workspaces after each run")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="print a saved results file")
    rep.add_argument("results")
    rep.set_defaults(func=cmd_report)

    cmp_ = sub.add_parser("compare", help="gate a candidate against a baseline")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")
    cmp_.set_defaults(func=cmd_compare)

    rs = sub.add_parser("rescore",
                        help="re-grade a saved sweep with the current checkers")
    rs.add_argument("results")
    rs.add_argument("--dry-run", action="store_true",
                    help="show what would change without rewriting results.json")
    rs.set_defaults(func=cmd_rescore)

    ls = sub.add_parser("list", help="list cases")
    ls.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    if getattr(args, "model", None) is not None and args.cmd == "run" and not args.model:
        args.model = ["qwencoder14", "qythos9"]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
