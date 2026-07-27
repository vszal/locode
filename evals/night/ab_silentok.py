#!/usr/bin/env python3
"""Paired A/B for the silent-success bash signal (build 49).

Controls for model non-stationarity (D75/D80) by toggling the ONE change via
`git stash` and running control (stashed = build 48 "(no output)") and treatment
(applied = build 49 "exit 0 — command succeeded") back-to-back per rep, so the
two arms are adjacent in time. Reuses the battery's cases + scoring.

    python evals/night/ab_silentok.py --cases indent-bug,add-test \
        --models gemmacoder12,qythos9 --reps 3

The fix is expected to reduce repeat-stops / repeats on tasks whose verify step
is a silent success (py_compile, quiet test) without regressing done-rate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "evals" / "night"))
sys.path.insert(0, str(ROOT / "evals"))
import run_battery as B  # noqa: E402

FIX_FILE = "locode/tools/shell.py"


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()


def _stash_has_fix() -> bool:
    # The treatment string must be present in the working tree.
    return "command succeeded" in (ROOT / FIX_FILE).read_text()


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
            print(f"  [{arm}] {case:<12} {model:<13} done={'Y' if r['done'] else 'N'} "
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
    ap.add_argument("--cases", default="indent-bug,add-test")
    ap.add_argument("--models", default="gemmacoder12,qythos9")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--outdir", default=str(ROOT / "evals/night/results/ab_silentok"))
    ap.add_argument("--max-iter", type=int, default=18)
    ap.add_argument("--max-wall", type=int, default=180)
    a = ap.parse_args(argv)

    if not _stash_has_fix():
        print("ERROR: working tree does not contain the fix — apply it first.")
        return 2

    cases = [c for c in a.cases.split(",") if c in B.CASES]
    models = [m for m in a.models.split(",") if m]
    outdir = Path(a.outdir).resolve()
    if outdir.exists():
        import shutil
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    all_rows: list[dict] = []
    for rep in range(1, a.reps + 1):
        print(f"\n=== rep {rep}/{a.reps} ===")
        # CONTROL first: stash the fix (working tree -> build 48 behavior).
        _git("stash", "push", "-q", "--", FIX_FILE)
        stashed = not _stash_has_fix()
        if not stashed:
            print("WARN: stash did not remove the fix; skipping to keep arms honest")
            _git("stash", "pop", "-q") if _git("stash", "list") else None
            continue
        try:
            all_rows += run_arm("C", cases, models, rep, outdir,
                                max_iter=a.max_iter, max_wall=a.max_wall)
        finally:
            _git("stash", "pop", "-q")  # restore the fix
        assert _stash_has_fix(), "fix not restored after control arm!"
        # TREATMENT: fix present.
        all_rows += run_arm("T", cases, models, rep, outdir,
                            max_iter=a.max_iter, max_wall=a.max_wall)

    c, t = summarize(all_rows, "C"), summarize(all_rows, "T")
    print("\n================ A/B RESULT ================")
    print(f"                 CONTROL(48)   TREATMENT(49)")
    print(f"  runs           {c['n']:>7}   {t['n']:>11}")
    print(f"  done           {c['done']:>7}   {t['done']:>11}")
    print(f"  repeat-stops   {c['repeat_stops']:>7}   {t['repeat_stops']:>11}")
    print(f"  total repeats  {c['repeats']:>7}   {t['repeats']:>11}")
    print(f"  avg iters      {c['iters']:>7.1f}   {t['iters']:>11.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
