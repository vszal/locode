#!/usr/bin/env python3
"""Lever 0g calibration: the occurrence-uptake rate, and its A/A noise floor.

Primary metric for 0g: among edit_file calls that got the ambiguous-match
error, what fraction of the NEXT tool call is an edit_file carrying
`occurrence`. Per-call, n in the hundreds -> rule 58 satisfied.

Secondary: the no-op rate -- fraction of those next calls that are an
edit_file which comes back ALREADY DONE (`old` == `new`). That is the
397-call pathology 5.98 sourced.

Rule 57: the SHIP threshold comes from the A/A spread measured here, not
from a number I liked the look of.
"""
import json
import math
import os
import sys

RESULTS = "evals/results"
AMBIG = "so it is not clear which"
ALREADY = "ALREADY DONE"


def events(root, run, arm):
    # the run key is `repeat`, not `rep` -- a typo, not a rule
    n = run.get("repeat", run.get("rep"))
    p = os.path.join(
        root, "events", f"{run['case']}__{run['model']}__r{n}__{arm}.jsonl"
    )
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


BUCKETS = ("occurrence", "noop", "otheredit", "nonedit", "noneext")


def arm_rates(root, runs, arm):
    """Bucket every ambiguity error by what the model did next.

    Buckets are exhaustive and mutually exclusive, so the columns sum to
    n_ambig -- no silent denominator drift.
    """
    n_ambig = 0
    b = dict.fromkeys(BUCKETS, 0)
    for run in runs:
        ev = events(root, run, arm)
        # flatten to the (run, result) call sequence; every batch is one call
        calls = []
        pending = None
        for e in ev:
            ph = e.get("phase")
            if ph == "run":
                pending = {"name": e.get("name"), "args": e.get("args") or {}}
            elif ph == "result" and pending is not None:
                pending["result"] = str(e.get("content") or "")
                calls.append(pending)
                pending = None
        for i, c in enumerate(calls):
            if c["name"] != "edit_file" or AMBIG not in c["result"]:
                continue
            n_ambig += 1
            if i + 1 >= len(calls):
                b["noneext"] += 1
            elif calls[i + 1]["name"] != "edit_file":
                b["nonedit"] += 1
            elif "occurrence" in calls[i + 1]["args"]:
                b["occurrence"] += 1
            elif ALREADY in calls[i + 1].get("result", ""):
                b["noop"] += 1
            else:
                b["otheredit"] += 1
    return n_ambig, b


def band(n1, n2, p):
    """95% band on a difference of two proportions at pooled rate p."""
    if not n1 or not n2:
        return None
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return 1.96 * se * 100


def main(labels):
    hdr = f"{'sweep':<30} {'arm':<5} {'ambig':>6}"
    for k in BUCKETS:
        hdr += f" {k:>10}"
    print(hdr)
    print("-" * len(hdr))
    for label in labels:
        root = os.path.join(RESULTS, label)
        ab = os.path.join(root, "ab.json")
        if not os.path.exists(ab):
            print(f"{label:<30} (no ab.json)")
            continue
        runs = json.load(open(ab))["runs"]
        row = {}
        for arm in ("base", "cand"):
            n, b = arm_rates(root, runs, arm)
            row[arm] = (n, b)
            line = f"{label:<30} {arm:<5} {n:>6}"
            for k in BUCKETS:
                line += f" {(100*b[k]/n):>9.1f}%" if n else f" {'-':>10}"
            print(line)
        (nb, bb), (nc, bc) = row["base"], row["cand"]
        if nb and nc:
            line = f"{'':<30} {'Δ':<5} {'':>6}"
            for k in BUCKETS:
                d = 100 * (bc[k] / nc - bb[k] / nb)
                w = band(nb, nc, (bb[k] + bc[k]) / (nb + nc))
                line += f" {d:>+6.1f}±{w:>2.0f}"
            print(line)
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
