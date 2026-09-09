"""Is the invocation effect a RESTART effect or a time/thermal drift?

Invocation is confounded with time order in the band sweep, so the decomposition
alone cannot say. Two discriminators:
  * within-invocation run order: thermal drift should also show up from run 1
    to run 4 INSIDE an invocation; a restart effect should not.
  * generation rate (chars/sec is not in this log, so wallclock per iteration):
    thermal throttling slows the box; a restart effect changes the trajectory,
    not the clock rate.
"""
import re, statistics as st
from collections import defaultdict
from pathlib import Path

LOGPATH = (Path(__file__).resolve().parents[1]
           / "evals" / "results" / "bench-band-qwen38.log")

rows = []   # (globalorder, inv, posinInv, case, secs, iters)
inv, case, pos = 0, None, defaultdict(int)
order = 0
for line in LOGPATH.read_text().splitlines():
    line = line.strip()
    m = re.match(r"#+ invocation (\d+)", line)
    if m: inv = int(m.group(1)); pos = defaultdict(int); continue
    m = re.match(r"\[\d+/\d+\]\s+(\S+)\s+·", line)
    if m: case = m.group(1); continue
    m = re.match(r"ok\s+(\d+)s\s+(\d+) iterations", line)
    if m and case:
        pos[case] += 1; order += 1
        rows.append((order, inv, pos[case], case, int(m.group(1)), int(m.group(2))))

print("A. iterations by position WITHIN an invocation (pooled over invocations)")
for p in (1, 2, 3, 4):
    v = [r[5] for r in rows if r[2] == p]
    print(f"   run {p} of 4: mean iters {st.mean(v):5.2f}  n={len(v)}")

print("\nB. iterations by invocation (pooled over cases)")
for i in (1, 2, 3):
    v = [r[5] for r in rows if r[1] == i]
    print(f"   invocation {i}: mean iters {st.mean(v):5.2f}  n={len(v)}")

print("\nC. seconds per iteration -- the throttling tell")
for i in (1, 2, 3):
    v = [r[4]/r[5] for r in rows if r[1] == i]
    print(f"   invocation {i}: {st.mean(v):5.1f} s/iteration  n={len(v)}")
for p in (1, 2, 3, 4):
    v = [r[4]/r[5] for r in rows if r[2] == p]
    print(f"   position {p}   : {st.mean(v):5.1f} s/iteration  n={len(v)}")
