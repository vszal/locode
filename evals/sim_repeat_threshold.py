#!/usr/bin/env python3
"""Rule 16: run lever 0e against the archive before spending GPU on it.

Current trigger (loop.py:997): `seen_streak >= max_repeat_calls - 1`, i.e. 2.
Streaks grow only when the result repeats -- EXCEPT for batches where every
call is a mutating edit, which grow regardless (loop.py:1122). Every batch in
the archive is one call, so batch_sig is that call's (name, args).

So today the nudge fires when the model attempts the THIRD identical call.
Lever 0e: for mutating batches, fire at the SECOND.

What this can and cannot tell us: suppressing a call changes everything
downstream, so this bounds the TIMING only -- when the nudge would have
arrived, versus when it actually did, versus when the run died. It says
nothing about whether the earlier nudge would have worked.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ambig_next import events, runs_for  # noqa: E402

RESULTS = "evals/results"
MUTATING = {"edit_file", "write_file", "replace_lines"}


def sim(root, runs):
    rows = []
    for arm in ("base", "cand"):
        # runs_for, not `for run in runs` -- ab.json holds one entry per
        # arm, so the unfiltered loop reads every file twice (rule 66).
        for run in runs_for(runs, arm):
            ev = events(root, run, arm)
            it = 0
            seen = {}          # sig -> occurrences
            sim_at = None      # iteration a threshold-1 mutating nudge would fire
            real_at = None     # iteration the real `repeated call` nudge fired
            stop_at = None
            for e in ev:
                ph = e.get("phase")
                if ph == "iteration":
                    it += 1
                elif ph == "nudge" and real_at is None and \
                        str(e.get("reason", "")).startswith("repeated call"):
                    real_at = it
                elif ph == "stopped" and stop_at is None:
                    stop_at = it
                elif ph == "run":
                    name = e.get("name", "?")
                    sig = name + "\0" + json.dumps(e.get("args") or {},
                                                   sort_keys=True)
                    seen[sig] = seen.get(sig, 0) + 1
                    if sim_at is None and seen[sig] >= 2 and name in MUTATING:
                        sim_at = it
            if sim_at or real_at or stop_at:
                rows.append((sim_at, real_at, stop_at))
    return rows


def med(v):
    return statistics.median(v) if v else None


def main(labels):
    rows = []
    for label in labels:
        ab = os.path.join(RESULTS, label, "ab.json")
        if os.path.exists(ab):
            rows += sim(os.path.join(RESULTS, label),
                        json.load(open(ab))["runs"])
    n = len(rows)
    sim_only = [r for r in rows if r[0] and not r[1]]
    both = [r for r in rows if r[0] and r[1]]
    earlier = [r[1] - r[0] for r in both]
    stopped = [r for r in rows if r[2]]
    print(f"runs examined: {n}")
    print(f"  would fire at occurrence 2 (mutating): {sum(1 for r in rows if r[0])} "
          f"({100*sum(1 for r in rows if r[0])/n:.0f}%)")
    print(f"  real `repeated call` nudge fired:      {sum(1 for r in rows if r[1])} "
          f"({100*sum(1 for r in rows if r[1])/n:.0f}%)")
    print(f"  NEW coverage (sim fires, real never):  {len(sim_only)} "
          f"({100*len(sim_only)/n:.0f}%)")
    print(f"\nwhere both fire (n={len(both)}): sim is earlier by a median of "
          f"{med(earlier)} iterations (mean {statistics.mean(earlier):.1f})"
          if both else "")
    for name, idx in (("sim", 0), ("real", 1)):
        v = [r[2] - r[idx] for r in stopped if r[idx] and r[2] >= r[idx]]
        if v:
            print(f"  runs that stopped: {name} nudge lands a median of "
                  f"{med(v)} iterations before the stop (n={len(v)}, "
                  f"<=1 iteration: {100*sum(1 for x in v if x<=1)/len(v):.0f}%)")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(os.listdir(RESULTS)))
