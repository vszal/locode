#!/usr/bin/env python3
"""Derive `metrics.json` for a sweep from its `ab.json` + `events/`.

Post-hoc and standalone on purpose: it does not touch `ab.py`, so it is safe to
run (and to edit) while a sweep is in flight, and it backfills every historical
sweep. `ab.py` records what happened; this file decides what is *readable*.

Usage:
    python evals/metrics.py <label> [<label> ...]
    python evals/metrics.py --all          # every sweep under evals/results
    python evals/metrics.py --all --print  # also dump a summary table

Writes `evals/results/<label>/metrics.json`.

WHY THIS EXISTS
Three graders (grade87, grade130, grade191) each re-implemented the same
aggregation, and grade130 shipped a verdict computed under a void precondition
partly because the boilerplate buried the decision. Derivation belongs in one
audited place; a grader should then be a short, readable decision rule.

THE SCHEMA ENCODES ROADMAP 5.92 — READ THIS BEFORE USING THE OUTPUT
Metrics are split into `per_run` and `per_call`, and each carries an explicit
`readable` field, because the two channels have very different power:

  * per_run  (fully_fixed, false_completions; n = runs, typically 8-24)
    Comparable ONLY between the base and cand arms of THIS sweep. Both cases
    measure 4-8x binomial overdispersion across sweeps, so a per-run number from
    one sweep must never be set against one from another sweep, however large
    the gap looks. The within-sweep A/A floor is +/-6 runs at n=24 (5.86b).

  * per_call (tool counts, error rates, occurrence uptake; n in the hundreds)
    The channel that can actually resolve a wording change (5.86b). Still
    preferred within-sweep, but robust enough to describe across sweeps when the
    quantity is an on/off (e.g. replace_lines 121 -> 0 after a description edit).

`fisher_p` is emitted for per-run scores as CONTEXT ONLY. It assumes independent
runs, which 5.92 showed is false across sweeps and questionable within them.
Do not gate on it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from math import comb

SCHEMA = 1
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

# Substrings of the tool results we count. Kept as literals matching the
# strings in locode/tools/fs.py; if those messages are reworded these go stale
# silently, so `errors_matched` in the output lets you notice.
NOT_FOUND = "not found in"            # fs.py:704  — `old` not found
AMBIG = "so it is not clear which"    # fs.py:1406 — ambiguous match


def fisher(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p. Context only — see module docstring."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    f = lambda a, b, c, d: comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
    obs, tot = f(a, b, c, d), 0.0
    for i in range(min(a + b, a + c) + 1):
        j, k = a + b - i, a + c - i
        l = c + d - k
        if j < 0 or k < 0 or l < 0:
            continue
        q = f(i, j, k, l)
        if q <= obs + 1e-12:
            tot += q
    return min(1.0, tot)


def _events(root: str, run: dict, arm: str) -> list[dict]:
    """Rule 43: the run key is `repeat`, not `rep`."""
    p = os.path.join(root, "events",
                     f"{run['case']}__{run['model']}__r{run['repeat']}__{arm}.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _arm(root: str, runs: list[dict], case: str, arm: str) -> dict:
    tools, nudges, stops = Counter(), Counter(), Counter()
    n = fixed = false_done = edits = occ = repl_all = nf = amb = 0
    rl_runs = errors_matched = 0
    iters: list[int] = []
    missing = 0

    for r in runs:
        if r["case"] != case or r["arm"] != arm or r.get("invalid"):
            continue
        n += 1
        ev = _events(root, r, arm)
        if not ev:
            missing += 1
        fx = bool(r.get("checks", {}).get("fully_fixed"))
        fixed += fx
        # A run that never emitted a `stopped` event yet did not fully fix the
        # task ended on the model's own say-so: a false completion.
        false_done += (not fx) and not any(e.get("phase") == "stopped" for e in ev)
        iters.append(sum(1 for e in ev if e.get("phase") == "iteration"))

        rl_here = 0
        for i, e in enumerate(ev):
            ph = e.get("phase")
            if ph == "nudge":
                nudges[str(e.get("reason", "?"))[:60]] += 1
            elif ph == "stopped":
                stops[str(e.get("reason", "?"))[:60]] += 1
            elif ph == "run":
                name = e.get("name", "?")
                tools[name] += 1
                rl_here += name == "replace_lines"
                if name != "edit_file":
                    continue
                edits += 1
                args = e.get("args") or {}
                occ += args.get("occurrence") is not None
                repl_all += bool(args.get("replace_all"))
                nxt = next((x for x in ev[i + 1:]
                            if x.get("phase") == "result"), None)
                txt = (nxt or {}).get("content") or ""
                hit_nf, hit_amb = NOT_FOUND in txt, AMBIG in txt
                nf += hit_nf
                amb += hit_amb
                errors_matched += hit_nf or hit_amb
        rl_runs += bool(rl_here)

    med = sorted(iters)[len(iters) // 2] if iters else 0
    return {
        "runs": n,
        "runs_missing_events": missing,
        "per_run": {
            "fully_fixed": fixed,
            "false_completions": false_done,
            "runs_touching_replace_lines": rl_runs,
        },
        "per_call": {
            "edit_file_calls": edits,
            "edits_with_occurrence": occ,
            "edits_with_replace_all": repl_all,
            "edits_old_not_found": nf,
            "edits_hit_ambiguity": amb,
            "tool_calls": dict(tools.most_common()),
        },
        "iterations": {"median": med, "total": sum(iters)},
        "nudges": dict(nudges.most_common()),
        "stops": dict(stops.most_common()),
        "errors_matched": errors_matched,
    }


def derive(label: str) -> dict | None:
    root = os.path.join(RESULTS, label)
    ab = os.path.join(root, "ab.json")
    if not os.path.exists(ab):
        return None
    with open(ab) as fh:
        d = json.load(fh)
    runs = d.get("runs", [])
    cases: dict[str, dict] = {}

    for case in sorted({r["case"] for r in runs}):
        b, c = (_arm(root, runs, case, a) for a in ("base", "cand"))
        bf, cf = b["per_run"]["fully_fixed"], c["per_run"]["fully_fixed"]
        pack = {"base": b, "cand": c}
        pack["comparisons"] = {
            "fully_fixed": {
                "base": [bf, b["runs"]],
                "cand": [cf, c["runs"]],
                "delta_runs": cf - bf,
                "fisher_p": round(fisher(bf, b["runs"] - bf,
                                         cf, c["runs"] - cf), 6),
                "readable": "WITHIN-SWEEP ONLY — never compare to another "
                            "sweep (5.92); A/A floor is +/-6 runs at n=24",
            },
        }
        for key in ("edits_with_occurrence", "edits_old_not_found",
                    "edits_hit_ambiguity"):
            bn, cn = b["per_call"]["edit_file_calls"], c["per_call"]["edit_file_calls"]
            pack["comparisons"][key] = {
                "base": [b["per_call"][key], bn],
                "cand": [c["per_call"][key], cn],
                "base_pct": round(100 * b["per_call"][key] / bn, 1) if bn else None,
                "cand_pct": round(100 * c["per_call"][key] / cn, 1) if cn else None,
                "readable": "per-call, n in the hundreds — the channel that "
                            "can resolve a wording change (5.86b)",
            }
        cases[case] = pack

    return {
        "schema": SCHEMA,
        "label": label,
        "base_ref": d.get("base_ref"),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_runs": len(runs),
        "invalid_runs": sum(1 for r in runs if r.get("invalid")),
        "models": sorted({r["model"] for r in runs}),
        "cases": cases,
        "READ_FIRST": [
            "per_run metrics compare base vs cand of THIS sweep ONLY. Both eval "
            "cases run at 4-8x binomial overdispersion across sweeps (5.92), so "
            "a per-run score from another sweep is not a valid comparator.",
            "per_call metrics (n in the hundreds) are the channel that can "
            "actually resolve a wording change (5.86b).",
            "fisher_p is context only; it assumes independent runs. Do not gate "
            "on it.",
        ],
    }


def main(argv: list[str]) -> int:
    show = "--print" in argv
    args = [a for a in argv if not a.startswith("--")]
    if "--all" in argv:
        args = sorted(d for d in os.listdir(RESULTS)
                      if os.path.isdir(os.path.join(RESULTS, d)))
    if not args:
        print(__doc__.strip().split("\n\n")[2])
        return 2

    rows, wrote = [], 0
    for label in args:
        m = derive(label)
        if m is None:
            print(f"skip {label}: no ab.json", file=sys.stderr)
            continue
        out = os.path.join(RESULTS, label, "metrics.json")
        with open(out, "w") as fh:
            json.dump(m, fh, indent=2, sort_keys=False)
            fh.write("\n")
        wrote += 1
        for case, pk in m["cases"].items():
            cm = pk["comparisons"]["fully_fixed"]
            rows.append((label, case, cm["base"], cm["cand"], cm["fisher_p"]))

    print(f"wrote {wrote} metrics.json")
    if show:
        print(f"\n{'label':32s}{'case':14s}{'base':>10s}{'cand':>10s}{'p':>9s}")
        for lab, case, b, c, p in rows:
            print(f"{lab:32s}{case:14s}{f'{b[0]}/{b[1]}':>10s}"
                  f"{f'{c[0]}/{c[1]}':>10s}{p:>9.3g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
