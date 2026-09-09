"""Variance decomposition of the phase-1 band sweep (rule 95).

Three invocations (server restarted between) x 4 repeats per case. The question
is whether restarting the server shifts the mean by more than repeated runs
within one server do -- if it does, a sweep run in a single invocation
understates its own noise, which is how most of the archive was run.

One-way ANOVA on invocation, per case, on iterations.
"""
import re, math, statistics as st
from collections import defaultdict
from pathlib import Path

LOGPATH = (Path(__file__).resolve().parents[1]
           / "evals" / "results" / "bench-band-qwen38.log")

runs = defaultdict(lambda: defaultdict(list))   # case -> inv -> [iters]
secs = defaultdict(list)
inv, case = 0, None
for line in LOGPATH.read_text().splitlines():
    line = line.strip()
    m = re.match(r"#+ invocation (\d+)", line)
    if m: inv = int(m.group(1)); continue
    m = re.match(r"\[\d+/\d+\]\s+(\S+)\s+·", line)
    if m: case = m.group(1); continue
    m = re.match(r"ok\s+(\d+)s\s+(\d+) iterations", line)
    if m and case:
        runs[case][inv].append(int(m.group(2))); secs[case].append(int(m.group(1)))

print(f"{'case':<20}{'inv means':>22}{'sd_within':>11}{'sd_betw':>9}{'F':>7}{'p<.05?':>8}{'MDD':>7}")
print("-"*84)
for c, byinv in runs.items():
    groups = [v for v in byinv.values() if len(v) > 1]
    k = len(groups); n = sum(len(g) for g in groups)
    gmean = st.mean([x for g in groups for x in g])
    ss_b = sum(len(g)*(st.mean(g)-gmean)**2 for g in groups)
    ss_w = sum((x-st.mean(g))**2 for g in groups for x in g)
    df_b, df_w = k-1, n-k
    ms_b, ms_w = ss_b/df_b, ss_w/df_w
    F = ms_b/ms_w if ms_w else float('inf')
    # variance component for invocation (negative -> no detectable effect)
    nper = n/k
    var_b = max(0.0, (ms_b-ms_w)/nper)
    sd_w, sd_b = math.sqrt(ms_w), math.sqrt(var_b)
    crit = 4.26   # F(2,9) at alpha=.05
    # minimum detectable difference between two arms of n=12, total sd
    sd_tot = math.sqrt(ms_w + var_b)
    mdd = 2.98 * sd_tot * math.sqrt(2/12)   # ~t(22)*se, 80% power approx
    means = " ".join(f"{st.mean(g):.2f}" for g in groups)
    print(f"{c:<20}{means:>22}{sd_w:>11.2f}{sd_b:>9.2f}{F:>7.2f}"
          f"{('YES' if F>crit else 'no'):>8}{mdd:>7.1f}")
print("-"*84)
print("sd_within = run-to-run inside one server. sd_betw = extra spread from")
print("restarting it. F>4.26 means invocation shifts the mean detectably at")
print("n=12. MDD = iteration gap two 12-run arms must show to be believable.")
