#!/usr/bin/env python3
"""Mine the whole results archive for a behaviour, before spending any GPU.

Why this exists: §5.121. The error-path lever was scoped, argued for on three
premises, and killed by 2,233 archived event logs in about twenty minutes —
no code written, no sweep run. Every premise was wrong, and one of them was
wrong in the flattering direction. The archive is the cheapest instrument in
the rig and nothing is cheaper than a question already answered, so run this
BEFORE building a lever, not after.

Two things it does that an ad-hoc script gets wrong:

**Exposure first (rule 52).** `--nudge REASON` counts how many archived runs
ever reached the machinery you want to change. A lever aimed at a path that
fires 0/12 times is not a lever; that is how the git-URL lever died (§5.119).

**Centring, always (rule 75).** The buckets here are NOT randomised — the model
chooses which one it lands in, and that choice correlates with the case, so a
pooled mean measures case mix. On the §5.121 data the pooled numbers showed
"gave final answer" beating "different tool" 0.57 to 0.48; centred within
(case, model) every bucket sat within 0.015 of the case mean and the entire
effect vanished. Both columns are printed and the centred one is the answer.
If they disagree, the pooled one is mix — that is not a tie-break, it is a
known-bad statistic.

Usage:
    python3 evals/mine.py --nudge "repeated call"
    python3 evals/mine.py --list-nudges
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
from dataclasses import dataclass
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


@dataclass
class Run:
    """One archived run: its identity, its score if the sweep recorded one."""
    sweep: str
    case: str
    model: str
    repeat: int
    arm: str
    score: float | None
    events: list[dict]

    @property
    def stratum(self) -> tuple[str, str]:
        """The unit a score is only comparable within. See rule 75."""
        return (self.case, self.model)


def _load_events(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # a truncated tail is normal on an interrupted run
    return out


def _score_index(root: Path) -> dict[tuple, float]:
    """(sweep, case, model, repeat, arm) -> score, from every ab.json."""
    idx: dict[tuple, float] = {}
    for ab in root.glob("*/ab.json"):
        try:
            data = json.loads(ab.read_text())
        except (ValueError, OSError):
            continue
        for pair in data.get("pairs", []):
            for arm in ("base", "cand"):
                if pair.get(arm) is not None:
                    key = (ab.parent.name, pair.get("case"), pair.get("model"),
                           pair.get("repeat"), arm)
                    idx[key] = pair[arm]
    return idx


def iter_runs(root: Path = RESULTS):
    """Every archived run, newest sweeps included, score attached when known.

    Event files are named `<case>__<model>__r<N>__<arm>.jsonl`; anything that
    does not parse that way is skipped rather than guessed at.
    """
    scores = _score_index(root)
    for path in sorted(root.glob("*/events/*.jsonl")):
        parts = path.name[:-len(".jsonl")].split("__")
        if len(parts) != 4:
            continue
        case, model, rr, arm = parts
        try:
            repeat = int(rr.lstrip("r"))
        except ValueError:
            continue
        sweep = path.parent.parent.name
        yield Run(sweep, case, model, repeat, arm,
                  scores.get((sweep, case, model, repeat, arm)),
                  _load_events(path))


def call_sig(event: dict) -> tuple:
    """Identity of a tool call: name plus normalised args."""
    return (event.get("name"), json.dumps(event.get("args"), sort_keys=True))


def next_action(events: list[dict], at: int) -> str | None:
    """What the model did first after `events[at]`, relative to what it repeated.

    None when nothing followed. The interesting bucket is `SAME call again` —
    that, and only that, is the nudge being ignored outright.
    """
    before = [e for e in events[:at] if e.get("phase") == "run"]
    if not before:
        return None
    repeated = call_sig(before[-1])
    after = [e for e in events[at + 1:] if e.get("phase") in ("run", "turn_end")]
    if not after:
        return None
    nxt = after[0]
    if nxt.get("phase") == "turn_end":
        return "gave final answer"
    if call_sig(nxt) == repeated:
        return "SAME call again"
    if nxt.get("name") == repeated[0]:
        return "same tool, diff args"
    return "different tool"


def centred(rows: list[tuple[tuple, str, float]]) -> dict[str, tuple[int, float, float]]:
    """bucket -> (n, pooled mean, mean centred within stratum). Rule 75.

    `rows` is (stratum, bucket, score). The centred figure is the only one that
    survives the model choosing its own bucket.
    """
    per_stratum: dict[tuple, list[float]] = collections.defaultdict(list)
    for stratum, _, score in rows:
        per_stratum[stratum].append(score)
    means = {k: statistics.mean(v) for k, v in per_stratum.items()}

    pooled: dict[str, list[float]] = collections.defaultdict(list)
    deltas: dict[str, list[float]] = collections.defaultdict(list)
    for stratum, bucket, score in rows:
        pooled[bucket].append(score)
        deltas[bucket].append(score - means[stratum])
    return {b: (len(pooled[b]), statistics.mean(pooled[b]), statistics.mean(deltas[b]))
            for b in pooled}


def _report_nudge(reason: str, root: Path) -> None:
    total = fired = 0
    failing = 0
    rows: list[tuple[tuple, str, float]] = []
    buckets: collections.Counter = collections.Counter()

    for run in iter_runs(root):
        total += 1
        hits = [i for i, e in enumerate(run.events)
                if e.get("phase") == "nudge" and e.get("reason") == reason]
        if not hits:
            continue
        fired += 1
        at = hits[0]
        prior = [e for e in run.events[:at] if e.get("phase") == "result"]
        if prior and prior[-1].get("error"):
            failing += 1
        action = next_action(run.events, at)
        if action is None:
            continue
        buckets[action] += 1
        if run.score is not None:
            rows.append((run.stratum, action, run.score))

    print(f"nudge {reason!r}: fired in {fired}/{total} archived runs "
          f"({fired / total:.0%} exposure)" if total else "no runs found")
    if not fired:
        print("\nZero exposure. A lever aimed here cannot be measured (rule 52).")
        return
    print(f"  of those, {failing} ({failing / fired:.0%}) followed a FAILING call")

    stats = centred(rows)
    print(f"\n{'first action after the nudge':28s} {'n':>5s} {'pooled':>8s} {'centred':>9s}")
    for bucket, count in buckets.most_common():
        n, pool, cent = stats.get(bucket, (0, float('nan'), float('nan')))
        share = f"({count / fired:.0%})"
        if n:
            print(f"{bucket:28s} {count:5d} {share:>6s} {pool:8.2f} {cent:+9.3f}")
        else:
            print(f"{bucket:28s} {count:5d} {share:>6s} {'—':>8s} {'—':>9s}")
    print("\nThe centred column is the answer (rule 75); pooled is shown only so a "
          "\ngap between them is visible as case mix rather than mistaken for effect.")


def _list_nudges(root: Path) -> None:
    counts: collections.Counter = collections.Counter()
    for run in iter_runs(root):
        for e in run.events:
            if e.get("phase") == "nudge" and e.get("reason"):
                counts[e["reason"]] += 1
    for reason, n in counts.most_common():
        print(f"{n:6d}  {reason}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nudge", metavar="REASON",
                    help="report exposure + centred outcome for this nudge reason")
    ap.add_argument("--list-nudges", action="store_true",
                    help="every nudge reason in the archive, by frequency")
    ap.add_argument("--results", type=Path, default=RESULTS)
    args = ap.parse_args()

    if args.list_nudges:
        _list_nudges(args.results)
    elif args.nudge:
        _report_nudge(args.nudge, args.results)
    else:
        ap.error("pass --nudge REASON or --list-nudges")


if __name__ == "__main__":
    main()
