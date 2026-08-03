#!/usr/bin/env python3
"""Per-arm clean-finish / iteration / nudge stats for an ab.py sweep.

Why this exists as a separate tool: **ab.py's paired test cannot see a
turn-ENDING effect.** Its delta is computed on the case SCORE, so when a change
alters how a turn is reported rather than what the model produces, both arms
score identically, every pair ties, and the sign-flip test correctly reports
INCONCLUSIVE — a true statement about the score and a useless one about the
change. Both times this bit (verifyok-msg, doneverify) the real effect was
sitting in the event logs at full strength: 0/10 vs 10/10 clean finishes behind
a "+0.000, p=1.0" summary.

So: when the hypothesis is about how a turn ends — clean finish vs repeat-stop,
nudge mix, iteration count — read it here, and treat ab.py's INCONCLUSIVE as
"the score didn't move", which is often the intended result.

Usage:  python evals/armstats.py <label>            # e.g. doneverify
        python evals/armstats.py <label> --by-case  # split per case
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

MUTATING = {"write_file", "append_file", "edit_file", "replace_lines",
            "bash", "move_file"}


def _blank() -> dict:
    return {"n": 0, "clean": 0, "iters": 0, "mut": 0,
            "nudges": collections.Counter(), "stops": collections.Counter()}


def collect(label: str, by_case: bool = False) -> dict:
    root = pathlib.Path("evals/results") / label / "events"
    if not root.is_dir():
        raise SystemExit(f"no events dir: {root}")
    out: dict = collections.defaultdict(_blank)
    for p in sorted(root.glob("*.jsonl")):
        arm = "cand" if "__cand" in p.name else "base"
        key = (p.name.split("__")[0], arm) if by_case else (arm,)
        ev = [json.loads(line) for line in open(p) if line.strip()]
        a = out[key]
        a["n"] += 1
        a["iters"] += sum(e.get("phase") == "iteration" for e in ev)
        a["mut"] += sum(e.get("phase") == "run" and e.get("name") in MUTATING
                        for e in ev)
        stops = [e.get("reason") for e in ev if e.get("phase") == "stopped"]
        if not stops:
            a["clean"] += 1          # self-terminated: the model chose to finish
        for s in stops:
            a["stops"][str(s)[:44]] += 1
        for e in ev:
            if e.get("phase") == "nudge":
                a["nudges"][e.get("reason")] += 1
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    label = sys.argv[1]
    by_case = "--by-case" in sys.argv
    rows = collect(label, by_case)
    for key in sorted(rows):
        a = rows[key]
        if not a["n"]:
            continue
        name = " · ".join(key)
        print(f"{name}: n={a['n']}  clean-finish={a['clean']}/{a['n']}"
              f" ({a['clean'] / a['n']:.0%})  mean-iters={a['iters'] / a['n']:.1f}"
              f"  mean-mutations={a['mut'] / a['n']:.1f}")
        if a["nudges"]:
            print(f"     nudges={dict(a['nudges'])}")
        if a["stops"]:
            print(f"     stops ={dict(a['stops'])}")


if __name__ == "__main__":
    main()
