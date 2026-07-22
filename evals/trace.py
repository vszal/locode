#!/usr/bin/env python3
"""Pretty-print an eval event log as a one-line-per-event trace.

`tail -f`-ing raw JSONL is unreadable, and the interesting columns (when did it
stall, which nudge fired, how long did each tool call take) are buried. Usage:

    python evals/trace.py evals/results/<label>/events/*.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def render(path: Path) -> None:
    print(f"--- {path.name}")
    for line in path.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        phase = r.get("phase", "?")
        bits = []
        for key in ("n", "name", "reason", "chars", "seconds", "elapsed",
                    "stop", "result", "model"):
            if key in r:
                val = r[key]
                if isinstance(val, str):
                    val = val.replace("\n", " ")[:70]
                bits.append(f"{key}={val}")
        if r.get("error"):
            bits.append("ERROR")
        print(f"{r.get('t', 0):8.1f}s  {phase:<16} {' '.join(bits)}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for arg in argv:
        p = Path(arg)
        if p.is_dir():
            for f in sorted(p.glob("*.jsonl")):
                render(f)
        elif p.exists():
            render(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
