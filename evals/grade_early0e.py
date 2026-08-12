#!/usr/bin/env python3
"""Grade lever 0e v2 (build 132) against the ROADMAP 5.101 pre-registration.

    .venv/bin/python evals/grade_early0e.py b132-early0e

WRITTEN AND COMMITTED BEFORE THE SWEEP PRODUCED A NUMBER. That is the whole
point; a decision rule chosen after seeing the data is not a decision rule.

The change under test (5.101, amended by 5.101a): for batches where every call
is a mutating edit, emit `_nudge_repeat_edit` one occurrence before the repeat
threshold WITHOUT suppressing the call. Suppression and the `_stop` stay where
they are. It is a BUNDLED intervention -- both the timing and the message move
-- and a win does not attribute between the two halves.

Two gates, both `sys.exit`, per rule 54. A precondition that only print()s is
how b130 announced a verdict computed under a void premise.

  GATE 1 (arm identity). The base arm predates the lever, so it MUST show zero
    early nudges. Any `early` event in base means the base ref is wrong and
    nothing below grades.

  GATE 2 (the lever fired, rule 17). The candidate must show an early nudge in
    >= 60% of runs. The archive simulation predicts 73% overall and 100% on
    exec-bugfix; below 60% the lever did not fire and the sweep grades nothing.

Decision (5.101):
  GATING metric  = share of runs ending in the repeat `_stop`. This is the
    outcome the lever is designed to prevent and it is defined for every run
    (rule 48).
  SHIP  requires the drop to clear BOTH a two-sided Fisher band AND 7pp, the
    calibrated A/A floor (rule 57).
  REJECT if clean finishes (`done`: no stop AND >=1 landed edit) fall at all
    -- rule 8, endings must not fall whatever else moves.

`done`, not `clean`: a run that self-terminates having changed nothing did not
finish, it surrendered (armstats). Per-run and therefore underpowered by
construction (rule 61) -- it is reported and it can veto, but it cannot ship.
"""
import collections
import json
import os
import pathlib
import sys
from math import comb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armstats  # noqa: E402

REPEAT_STOP = "the model repeated the same tool call"
MIN_FIRE_RATE = 0.60
MIN_DROP_PP = 7.0


def fisher_2x2(a, n1, b, n2):
    """Two-sided Fisher exact on [[a, n1-a], [b, n2-b]]."""
    def p(x, y):
        return (comb(n1, x) * comb(n2, y) / comb(n1 + n2, x + y))
    tot = a + b
    obs = p(a, b)
    return sum(p(x, tot - x) for x in range(0, n1 + 1)
               if 0 <= tot - x <= n2 and p(x, tot - x) <= obs + 1e-12)


def early_fires(label):
    """Runs carrying >=1 early nudge, per arm. Reads the `early` event flag the
    patch adds -- the reason string is byte-identical to the late nudge, so the
    flag is the only thing that separates them."""
    root = pathlib.Path("evals/results") / label / "events"
    out = collections.Counter()
    tot = collections.Counter()
    for p in sorted(root.glob("*.jsonl")):
        arm = "cand" if "__cand" in p.name else "base"
        tot[arm] += 1
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("phase") == "nudge" and e.get("early"):
                out[arm] += 1
                break
    return out, tot


def main(label):
    stats = armstats.collect(label)
    base, cand = stats[("base",)], stats[("cand",)]
    nb, nc = base["n"], cand["n"]
    fires, tot = early_fires(label)

    def repeat_stops(a):
        return sum(v for k, v in a["stops"].items() if k.startswith(REPEAT_STOP))

    rb, rc = repeat_stops(base), repeat_stops(cand)
    db, dc = base["done"], cand["done"]

    print(f"label: {label}\n")
    print(f"{'':<34}{'base':>12}{'cand':>12}")
    print("-" * 58)
    print(f"{'runs':<34}{nb:>12}{nc:>12}")
    print(f"{'runs with an EARLY nudge':<34}"
          f"{fires['base']}/{tot['base']:<10}{fires['cand']}/{tot['cand']:<10}")
    print(f"{'repeat-stops  [GATING]':<34}"
          f"{rb}/{nb} ({100*rb/nb:.0f}%)".rjust(12)
          + f"{rc}/{nc} ({100*rc/nc:.0f}%)".rjust(12))
    print(f"{'clean finishes (done)':<34}"
          f"{db}/{nb} ({100*db/nb:.0f}%)".rjust(12)
          + f"{dc}/{nc} ({100*dc/nc:.0f}%)".rjust(12))
    print(f"{'mean landed edits':<34}{base['landed']/nb:>12.2f}"
          f"{cand['landed']/nc:>12.2f}")

    print("\n=== gates (fixed in advance, ROADMAP 5.101) ===")
    if fires["base"]:
        sys.exit(f"\n  [gate 1] VOID — base shows {fires['base']} early nudge(s); "
                 "the base ref predates the lever and must show zero. "
                 "Nothing grades.")
    print(f"  [gate 1] base early nudges = 0  ✓")

    rate = fires["cand"] / tot["cand"] if tot["cand"] else 0.0
    if rate < MIN_FIRE_RATE:
        sys.exit(f"\n  [gate 2] VOID — the lever fired in only {100*rate:.0f}% "
                 f"of candidate runs (needs >={100*MIN_FIRE_RATE:.0f}%). "
                 "The sweep grades nothing (rule 17).")
    print(f"  [gate 2] lever fired in {100*rate:.0f}% of cand runs "
          f"(>={100*MIN_FIRE_RATE:.0f}%)  ✓")

    drop_pp = 100 * (rb / nb - rc / nc)
    p = fisher_2x2(rb, nb, rc, nc)
    print(f"\n=== decision ===")
    print(f"  repeat-stop drop : {drop_pp:+.1f}pp   (Fisher two-sided p={p:.4f})")
    print(f"  clean finishes   : {db}/{nb} -> {dc}/{nc}")

    if dc < db:
        print("\n  => REJECT. Clean finishes fell "
              f"({db} -> {dc}); rule 8 vetoes regardless of the gating metric.")
    elif drop_pp >= MIN_DROP_PP and p < 0.05:
        print(f"\n  => SHIP. The repeat-stop share fell {drop_pp:.1f}pp "
              f"(>= {MIN_DROP_PP}pp, the A/A floor) at p={p:.4f}, "
              "and clean finishes did not fall.")
    else:
        why = []
        if drop_pp < MIN_DROP_PP:
            why.append(f"the drop is {drop_pp:+.1f}pp, inside the "
                       f"{MIN_DROP_PP}pp A/A floor (rule 57)")
        if p >= 0.05:
            why.append(f"p={p:.4f} does not clear the band")
        print("\n  => NO SHIP (keep the lever OFF by default): " + "; ".join(why)
              + ".\n     The lever fired as designed, so this is a real "
                "negative, not a void sweep.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "b132-early0e")
