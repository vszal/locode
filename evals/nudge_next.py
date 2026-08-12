#!/usr/bin/env python3
"""What happens after a nudge fires — split three ways, per sweep.

    python evals/nudge_next.py "repeated call" [sweep ...]

Defaults to every sweep in evals/results.

The three-way split is the whole point of this script. §5.97 classified the
outcome as "next tool" versus "the turn ended", and that second bucket silently
merged two opposite events: the model choosing to stop calling tools, and the
harness's own repeat/stall cap killing the run. For the `repeated call` nudge
the merged number read 61% "the turn simply ended" when the model-chose share
is 3.8% and the rest is the guard executing. See §5.100.

    TOOL:<name>   the model's next tool call
    HARNESS_STOP  a `stopped` event — the cap fired, the model did not choose
    MODEL_ENDED   no further tool call and no stop; the model really did stop

Also reports the iteration gap from the nudge to the stop, which is what says
whether a nudge had room to work before the cap reached it.
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ambig_next import events  # noqa: E402

RESULTS = "evals/results"


def scan(root, runs, prefix):
    """Outcome counter, stop-reason counter, and nudge->stop iteration gaps."""
    out = collections.Counter()
    reasons = collections.Counter()
    gaps = []
    for run in runs:
        for arm in ("base", "cand"):
            ev = events(root, run, arm)
            # iteration index per event, so the gap is in iterations not events
            it = 0
            at = []
            for e in ev:
                if e.get("phase") == "iteration":
                    it += 1
                at.append(it)
            first_nudge = None
            for i, e in enumerate(ev):
                if e.get("phase") != "nudge":
                    continue
                if not str(e.get("reason", "")).startswith(prefix):
                    continue
                if first_nudge is None:
                    first_nudge = at[i]
                bucket = "MODEL_ENDED"
                for j in range(i + 1, len(ev)):
                    ph = ev[j].get("phase")
                    if ph == "run":
                        bucket = "TOOL:" + str(ev[j].get("name"))
                        break
                    if ph == "stopped":
                        bucket = "HARNESS_STOP"
                        reasons[str(ev[j].get("reason"))[:55]] += 1
                        break
                out[bucket] += 1
            # The gap is measured from the run's FIRST matching nudge to
            # wherever that run stopped, intervening tool calls included --
            # "how much room did the nudge have", not "did the stop come
            # immediately after it". The narrower reading undercounts by a
            # third and reads a misleadingly tight p90. See §5.100.
            if first_nudge is not None:
                for j, e in enumerate(ev):
                    if e.get("phase") == "stopped" and at[j] >= first_nudge:
                        gaps.append(at[j] - first_nudge)
                        break
    return out, reasons, gaps


def main(prefix, labels):
    print(f"nudge reason prefix: {prefix!r}\n")
    print(f"{'sweep':<32}{'fires':>6}{'tool':>8}{'stop':>8}{'ended':>8}")
    print("-" * 62)
    tot = collections.Counter()
    tot_reasons = collections.Counter()
    tot_gaps = []
    for label in labels:
        root = os.path.join(RESULTS, label)
        if not os.path.exists(os.path.join(root, "ab.json")):
            continue
        runs = json.load(open(os.path.join(root, "ab.json")))["runs"]
        out, reasons, gaps = scan(root, runs, prefix)
        n = sum(out.values())
        if not n:
            continue
        tot.update(out)
        tot_reasons.update(reasons)
        tot_gaps += gaps
        tl = sum(v for k, v in out.items() if k.startswith("TOOL"))
        print(f"{label:<32}{n:>6}{100*tl/n:>7.1f}%"
              f"{100*out['HARNESS_STOP']/n:>7.1f}%{100*out['MODEL_ENDED']/n:>7.1f}%")
    n = sum(tot.values())
    if not n:
        print("(no matching nudges)")
        return
    print("-" * 62)
    tl = sum(v for k, v in tot.items() if k.startswith("TOOL"))
    print(f"{'ALL':<32}{n:>6}{100*tl/n:>7.1f}%"
          f"{100*tot['HARNESS_STOP']/n:>7.1f}%{100*tot['MODEL_ENDED']/n:>7.1f}%")
    print("\nnext tool:")
    for k, v in tot.most_common():
        if k.startswith("TOOL"):
            print(f"  {v:>5}  {100*v/n:5.1f}%  {k[5:]}")
    print("\nharness stop reasons:")
    for k, v in tot_reasons.most_common(8):
        print(f"  {v:>5}  {k}")
    if tot_gaps:
        g = sorted(tot_gaps)
        le1 = 100 * sum(1 for x in g if x <= 1) / len(g)
        print(f"\niterations from first nudge to stop: n={len(g)} "
              f"median={g[len(g)//2]} p90={g[int(len(g)*.9)]} "
              f"<=1 iteration: {le1:.0f}%")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:] or sorted(os.listdir(RESULTS)))
