#!/usr/bin/env python3
"""Grade the arm-slot A/A (ROADMAP 5.104).

    .venv/bin/python evals/grade_slot.py aa16-slot

WRITTEN AND COMMITTED BEFORE THE SWEEP PRODUCED A NUMBER.

The question. Every paired A/B in this project materialises its baseline as a
fresh `git worktree` at a ref and its candidate as the LIVE working tree
(ab.py:25). b131 and b132 disagree about byte-identical code -- build 131 lands
0.00 edits/run in the cand slot and 1.88 in the base slot -- so the slot itself
is under suspicion. This runs both arms on the same commit and asks whether the
gap survives when there is no change to detect.

Metric, fixed in advance: the share of runs landing >=1 edit, per arm. Per-run
and therefore underpowered by construction (rule 61), but that is the channel
the anomaly appeared in, and grading a different one would be moving the target.
`landed` is armstats' definition -- a non-error result for an editing call.

Decision:
  p < 0.05  -> REAL SLOT EFFECT. Every A/B in the archive carries it. Fix the
    design (both arms as worktrees) BEFORE grading another lever.
  p >= 0.05 -> no slot effect at this size. The b131/b132 split stands as
    unexplained sampling variation, 5.103's table stays withdrawn rather than
    re-attributed, and no lever verdict is disturbed.

Power is stated rather than assumed: the gap that prompted this is ~56% vs ~6%,
which 16v16 detects at p~0.005. A null rules out an effect OF THAT SIZE -- the
one at issue -- and not a small one. The script says so in its own output so the
null cannot be over-read later.

Gate (rule 54, sys.exit not print): the arms must actually be identical. An A/A
whose arms differ is not an A/A, and `--allow-identical` is exactly the flag
that lets a non-identical pair through by mistake.
"""
import json
import os
import pathlib
import subprocess
import sys
from math import comb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armstats  # noqa: E402

ALPHA = 0.05


def fisher_2x2(a, n1, b, n2):
    """Two-sided Fisher exact on [[a, n1-a], [b, n2-b]]."""
    def p(x, y):
        return comb(n1, x) * comb(n2, y) / comb(n1 + n2, x + y)
    tot = a + b
    obs = p(a, b)
    return sum(p(x, tot - x) for x in range(0, n1 + 1)
               if 0 <= tot - x <= n2 and p(x, tot - x) <= obs + 1e-12)


def landing_runs(label):
    """Runs landing >=1 edit, per arm, straight from the event files."""
    root = pathlib.Path("evals/results") / label / "events"
    hit, tot = {"base": 0, "cand": 0}, {"base": 0, "cand": 0}
    for p in sorted(root.glob("*.jsonl")):
        arm = "cand" if "__cand" in p.name else "base"
        ev = []
        for line in open(p):
            line = line.strip()
            if line:
                try:
                    ev.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        tot[arm] += 1
        hit[arm] += armstats._landed_edits(ev) > 0
    return hit, tot


def main(label):
    meta = json.load(open(f"evals/results/{label}/ab.json"))
    stats = armstats.collect(label)
    base, cand = stats[("base",)], stats[("cand",)]
    hit, tot = landing_runs(label)

    print(f"label: {label}")
    print(f"base_ref: {meta.get('base_ref')} ({meta.get('base_sha')})   "
          f"cand: {meta.get('cand_desc')}")
    srv = meta.get("server") or {}
    print(f"server: pid {srv.get('pid')} up since {srv.get('started')}\n")

    print(f"{'':<30}{'base':>12}{'cand':>12}")
    print("-" * 54)
    print(f"{'runs':<30}{base['n']:>12}{cand['n']:>12}")
    print(f"{'runs landing >=1 edit [METRIC]':<30}"
          + f"{hit['base']}/{tot['base']}".rjust(12)
          + f"{hit['cand']}/{tot['cand']}".rjust(12))
    print(f"{'mean landed edits':<30}{base['landed']/base['n']:>12.2f}"
          f"{cand['landed']/cand['n']:>12.2f}")
    print(f"{'gave up':<30}{base['gaveup']:>12}{cand['gaveup']:>12}")
    # armstats has no 'stopped' key -- it counts stop REASONS in a Counter.
    print(f"{'stopped by a guard':<30}"
          f"{sum(base['stops'].values()):>12}{sum(cand['stops'].values()):>12}")
    print(f"{'clean finishes (done)':<30}{base['done']:>12}{cand['done']:>12}")

    print("\n=== gate (fixed in advance) ===")
    if not meta.get("is_calibration"):
        sys.exit("\n  VOID — ab.json is not marked is_calibration; this is not "
                 "an A/A and the slot question is not what it answers.")
    ref = meta.get("base_sha") or meta.get("base_ref")
    try:
        diff = subprocess.run(["git", "diff", "--stat", ref, "--", "locode/"],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"\n  VOID — could not verify arm identity: {exc}")
    if diff.strip():
        sys.exit("\n  VOID — the working tree differs from the base ref under "
                 f"locode/, so the arms were NOT identical:\n{diff}"
                 "  This is an A/B with an undeclared change, not an A/A.")
    print(f"  arms byte-identical under locode/ (vs {ref})  ✓")

    nb, nc = tot["base"], tot["cand"]
    p = fisher_2x2(hit["base"], nb, hit["cand"], nc)
    gap = 100 * (hit["base"] / nb - hit["cand"] / nc)
    print(f"\n=== decision ===")
    print(f"  base-minus-cand landing gap : {gap:+.1f}pp   "
          f"(Fisher two-sided p={p:.4f})")

    if p < ALPHA:
        print("\n  => REAL SLOT EFFECT. Byte-identical arms differ by "
              f"{gap:+.1f}pp at p={p:.4f}.\n"
              "     The worktree-vs-live-tree design biases the outcome, so "
              "EVERY paired A/B\n     in the archive carries it. Fix the rig — "
              "both arms as worktrees — before\n     grading another lever. "
              "Re-read any verdict that turned on a per-run metric.")
    else:
        print(f"\n  => NO SLOT EFFECT detectable at n={nb}v{nc} "
              f"(gap {gap:+.1f}pp, p={p:.4f}).\n"
              "     The b131/b132 split stays UNEXPLAINED sampling variation; "
              "5.103's table\n     remains withdrawn rather than re-attributed, "
              "and no lever verdict moves.\n"
              "     Note the limit: this size detects the ~50pp gap that "
              "prompted it at p~0.005,\n     so it rules out an effect of THAT "
              "size — not a small one.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "aa16-slot")
