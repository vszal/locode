#!/usr/bin/env python3
"""Grade an EXTERNAL tool's workspace with a locode case's own `check.py`.

Exists so a competitor (aider, goose, ...) is scored by exactly the rubric
locode is scored by, rather than by a bespoke per-tool checker that would let
the comparison drift. Takes a finished workspace directory and a case id, runs
that case's `check()` against it, and prints the same
`{criterion: bool} + score` shape the harness records.

`CheckCtx.events` is empty here: an external tool emits no locode events. Any
case whose check reads `ctx.events` cannot be graded this way and is reported
as unsupported rather than silently scored on a partial context.

    evals/grade_external.py --case exec-ambig --workdir /path/to/ws [--json]
"""
import argparse, inspect, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402


def grade(case_id: str, workdir: Path) -> dict:
    cases = harness.discover_cases([case_id])
    if not cases:
        return {"error": f"no such case: {case_id}"}
    case = cases[0]
    checker = harness._load_checker(case)
    if checker is None:
        return {"error": f"case {case_id} ships no check.py"}
    try:
        src = inspect.getsource(checker)
    except OSError:
        src = ""
    if "ctx.events" in src:
        return {"error": f"case {case_id} grades on ctx.events; "
                         "an external tool emits none — unsupported"}
    ctx = harness.CheckCtx(workdir=workdir, events=[], stdout="", case=case)
    results = checker(ctx)
    return {"case": case_id, "workdir": str(workdir),
            "checks": results, "score": harness._score(results)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = grade(a.case, a.workdir)
    print(json.dumps(out) if a.json else json.dumps(out, indent=2))
    return 0 if "error" not in out else 2


if __name__ == "__main__":
    raise SystemExit(main())
