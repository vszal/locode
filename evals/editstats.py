#!/usr/bin/env python3
"""Classify edit_file outcomes from a sweep's event logs.

Pairs each run(edit_file) with its following result(edit_file) and buckets the
result by outcome, so we can measure the edit failure rate, the no-op rate, and
whether the new build-27 'noop' status and build-28 success echo are firing.

Usage:  python evals/editstats.py evals/results/<label>[/events]
        python evals/editstats.py <label_a> <label_b>   # side-by-side
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _events_dir(arg: str) -> Path:
    p = Path(arg)
    if p.name == "events":
        return p
    if (p / "events").is_dir():
        return p / "events"
    # bare label under evals/results
    cand = Path("evals/results") / arg / "events"
    if cand.is_dir():
        return cand
    raise SystemExit(f"no events dir for {arg!r}")


def classify(content: str, is_error: bool) -> str:
    c = content.lower()
    if not is_error:
        if "file now reads" in c:  # build-28 echo present
            return "ok+echo"
        return "ok"
    if "changed nothing" in c or "byte-for-byte identical" in c:
        return "noop-status"            # build 27: tolerant match, zero delta
    if "must differ" in c or ("`new`" in content and "`old`" in content and "identical" in c):
        return "identical"              # build 21: old==new pre-guard
    if "identical" in c:
        return "identical"
    if "ambiguous" in c or "matches" in c and "line" in c:
        return "ambiguous"
    if "not find" in c or "not found" in c or "no match" in c:
        return "not_found"
    if "empty" in c:
        return "empty_old"
    return "other_error"


def analyze(events_dir: Path) -> dict:
    per_model: dict[str, Counter] = defaultdict(Counter)
    per_model_files: dict[str, int] = defaultdict(int)  # total edit calls
    stop_reasons: dict[str, Counter] = defaultdict(Counter)
    # unrecovered no-op: a failing edit to a path with no *later* successful
    # edit to that same path in the same run.
    unrecovered: dict[str, int] = defaultdict(int)

    for f in sorted(events_dir.glob("*.jsonl")):
        model = f.stem.split("__")[1] if "__" in f.stem else "?"
        lines = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        # collect edit run/result pairs in order, with path
        pending = None
        seq_calls = []  # (path, cls, is_error)
        stop = None
        for e in lines:
            ph = e.get("phase")
            if ph == "run" and e.get("name") == "edit_file":
                pending = (e.get("args") or {}).get("path", "?")
            elif ph == "result" and e.get("name") == "edit_file" and pending is not None:
                cls = classify(e.get("content", ""), bool(e.get("error")))
                seq_calls.append((pending, cls, bool(e.get("error"))))
                per_model[model][cls] += 1
                per_model_files[model] += 1
                pending = None
            elif ph == "stopped":
                stop = e.get("reason", "?")
        if stop:
            stop_reasons[model][stop] += 1
        # recovery: for each failing call, is there a later ok on same path?
        for i, (path, cls, err) in enumerate(seq_calls):
            if not err:
                continue
            later_ok = any(
                p == path and not e2
                for (p, _c, e2) in seq_calls[i + 1:]
            )
            if not later_ok:
                unrecovered[model] += 1

    return {
        "per_model": per_model,
        "totals": per_model_files,
        "stops": stop_reasons,
        "unrecovered": unrecovered,
    }


def _print(label: str, res: dict) -> None:
    print(f"\n=== {label} ===")
    for model in sorted(res["per_model"]):
        c = res["per_model"][model]
        total = res["totals"][model]
        fails = sum(v for k, v in c.items() if k not in ("ok", "ok+echo"))
        oks = c["ok"] + c["ok+echo"]
        fail_pct = (100 * fails / total) if total else 0
        echo_pct = (100 * c["ok+echo"] / oks) if oks else 0
        print(f"\n {model}: {total} edit calls, {fails} fail ({fail_pct:.0f}%), "
              f"{res['unrecovered'][model]} unrecovered")
        print(f"   echo on successes: {c['ok+echo']}/{oks} ({echo_pct:.0f}%)")
        for k in ("ok", "ok+echo", "noop-status", "identical", "ambiguous",
                  "not_found", "empty_old", "other_error"):
            if c[k]:
                print(f"   {k:<14} {c[k]}")
        if res["stops"][model]:
            print("   stops:")
            for r, n in res["stops"][model].most_common():
                print(f"     {n}x {r[:70]}")


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    for arg in argv:
        d = _events_dir(arg)
        _print(arg, analyze(d))


if __name__ == "__main__":
    main(sys.argv[1:])
