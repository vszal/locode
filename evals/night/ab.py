#!/usr/bin/env python3
"""Generic paired A/B for a single-file behavior change (D75/D80).

Isolates ONE uncommitted change by toggling it with `git stash push -- <file>`:
runs control (change stashed) and treatment (change present) back-to-back per
rep so the two arms are adjacent in time and model non-stationarity cancels.
The change must be UNCOMMITTED in the working tree when this starts.

    python evals/night/ab.py --fix-file locode/tools/fs.py \
        --marker "parses cleanly" --cases indent-bug,undefined-vars,new-module \
        --models gemmacoder12,qythos9 --reps 3

--marker is a substring that is present ONLY in the treatment (working-tree)
version of --fix-file; it's how each arm self-verifies it's testing what it
thinks. Reuses run_battery's cases + scoring; reports done-rate, repeat-stops,
total repeats, and avg iterations per arm.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "evals" / "night"))
sys.path.insert(0, str(ROOT / "evals"))
import run_battery as B  # noqa: E402


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()


def run_arm(arm: str, cases: list[str], models: list[str], rep: int,
            outdir: Path, *, max_iter: int, max_wall: int) -> list[dict]:
    rows = []
    for case in cases:
        for model in models:
            r = B.run_one(case, model, f"{arm}{rep}", outdir,
                          max_iter=max_iter, max_wall=max_wall)
            r["arm"] = arm
            rows.append(r)
            s = r["s"]
            print(f"  [{arm}] {case:<15} {model:<13} done={'Y' if r['done'] else 'N'} "
                  f"{s['iterations']:>2}it f{s['fails']} n{s['noops']} r{s['repeats']} "
                  f"{'green' if s['saw_green'] else '     '} "
                  f"{(s['stop_reason'] or 'answered')[:30]}", flush=True)
    return rows


def summarize(rows: list[dict], arm: str) -> dict:
    a = [r for r in rows if r["arm"] == arm]
    n = len(a) or 1
    return {
        "n": len(a),
        "done": sum(r["done"] for r in a),
        "repeats": sum(r["s"]["repeats"] for r in a),
        "repeat_stops": sum(1 for r in a
                            if "repeat" in (r["s"]["stop_reason"] or "").lower()),
        "iters": sum(r["s"]["iterations"] for r in a) / n,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-file", required=True,
                    help="repo-relative path(s) to toggle; comma-separated for a "
                         "multi-file change")
    ap.add_argument("--marker", required=True,
                    help="substring present only in the treatment version")
    ap.add_argument("--cases", default="indent-bug,undefined-vars,new-module")
    ap.add_argument("--models", default="gemmacoder12,qythos9")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--outdir", default=str(ROOT / "evals/night/results/ab"))
    ap.add_argument("--max-iter", type=int, default=18)
    ap.add_argument("--max-wall", type=int, default=180)
    a = ap.parse_args(argv)

    fix_files = [f.strip() for f in a.fix_file.split(",") if f.strip()]
    fixes = [ROOT / f for f in fix_files]
    # The marker lives in exactly one of the files; the change is "present" when
    # any toggled file still carries it, "removed" when none do.
    has_marker = lambda: any(a.marker in f.read_text() for f in fixes)
    if not has_marker():
        print(f"ERROR: --marker not in any working-tree {fix_files}; apply the change first.")
        return 2

    cases = [c for c in a.cases.split(",") if c in B.CASES]
    models = [m for m in a.models.split(",") if m]
    outdir = Path(a.outdir).resolve()
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    all_rows: list[dict] = []
    for rep in range(1, a.reps + 1):
        print(f"\n=== rep {rep}/{a.reps} ===")
        _git("stash", "push", "-q", "--", *fix_files)  # CONTROL: change removed
        if has_marker():
            print("WARN: stash did not remove the change; skipping rep to stay honest")
            if _git("stash", "list"):
                _git("stash", "pop", "-q")
            continue
        try:
            all_rows += run_arm("C", cases, models, rep, outdir,
                                max_iter=a.max_iter, max_wall=a.max_wall)
        finally:
            _git("stash", "pop", "-q")  # restore the change
        assert has_marker(), "change not restored after control arm!"
        all_rows += run_arm("T", cases, models, rep, outdir,  # TREATMENT
                            max_iter=a.max_iter, max_wall=a.max_wall)

    c, t = summarize(all_rows, "C"), summarize(all_rows, "T")
    print("\n================ A/B RESULT ================")
    print(f"                 CONTROL      TREATMENT")
    print(f"  runs           {c['n']:>7}   {t['n']:>11}")
    print(f"  done           {c['done']:>7}   {t['done']:>11}")
    print(f"  repeat-stops   {c['repeat_stops']:>7}   {t['repeat_stops']:>11}")
    print(f"  total repeats  {c['repeats']:>7}   {t['repeats']:>11}")
    print(f"  avg iters      {c['iters']:>7.1f}   {t['iters']:>11.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
