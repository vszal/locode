#!/usr/bin/env python3
"""Count revert-cycle exposure in an arm's event logs, offline.

`loop.py`'s `_revert_note` fires when an edit puts a path back to content the
tests have already rejected. A *control* arm runs a build without that note, so
the events record no firing -- but the underlying cycle still happens, and the
number of times it happens is exactly the exposure the lever would have had.
This mirrors the loop's detection rule against a finished arm so a control can
be scored for exposure and a treatment arm's firings can be verified against an
independent implementation.

Validated against b142, where the live note fired: this reports 2 firings in 2
runs on `exec-bugfix` and 0 elsewhere, matching the `nudge` events exactly.

    python3 evals/revert_exposure.py evals/results/b141q-revert-eb [...]
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

# A result that shows a test failing. Mirrors the loop's condition that the
# note stays quiet until a run has actually gone red.
_FAILED = re.compile(r"\b\d+ (failed|error)")


def firings(path: str) -> int:
    """How many edits in this run restored an already-tested version."""
    pairs: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    seen_failure = False
    count = 0
    for line in open(path):
        event = json.loads(line)
        phase = event.get("phase")
        if phase == "result":
            content = event.get("content") or ""
            if _FAILED.search(content) or "FAILURES" in content:
                seen_failure = True
            continue
        if phase != "run" or event.get("name") != "edit_file":
            continue
        args = event.get("args") or {}
        target, old, new = args.get("path"), args.get("old"), args.get("new")
        if not (isinstance(target, str) and isinstance(old, str)
                and isinstance(new, str) and old != new):
            continue
        if (new, old) in pairs[target] and seen_failure:
            count += 1
        pairs[target].append((old, new))
    return count


def report(results_dir: str) -> tuple[int, int, int]:
    runs = sorted(glob.glob(os.path.join(results_dir, "events", "*.jsonl")))
    per_run = [firings(f) for f in runs]
    exposed = sum(1 for n in per_run if n)
    return len(runs), exposed, sum(per_run)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    for d in argv:
        total, exposed, count = report(d)
        pct = 100 * exposed / total if total else 0.0
        print(f"{os.path.basename(d.rstrip('/')):26} runs={total:3}  "
              f"exposed={exposed:3} ({pct:4.0f}%)  firings={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
