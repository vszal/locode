"""Rule 96 across the whole archive: re-derive every stored score from its
stored per-check booleans under today's GUARDS/DERIVED declarations."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from locode.bench.runner import load_grader, _score

ROOTS = ("locode/bench/cases", "evals/cases")
class _C:
    def __init__(s, p): s.path, s.id = p, p.name
_cache = {}
def decls(cid):
    if cid in _cache: return _cache[cid]
    for r in ROOTS:
        d = ROOT / r / cid
        if (d/"check.py").is_file():
            _, g, dv = load_grader(_C(d)); _cache[cid] = (g, dv); return _cache[cid]
    _cache[cid] = None; return None

rows, missing = [], set()
for f in sorted((ROOT / "evals" / "results").rglob("results.json")):
    data = json.loads(f.read_text())
    runs = data if isinstance(data, list) else data.get("runs", [])
    if not isinstance(runs, list): continue
    per = {}
    for r in runs:
        if not isinstance(r, dict): continue
        cid, ch = r.get("case"), r.get("checks")
        if not cid or not isinstance(ch, dict) or not ch: continue
        d = decls(cid)
        if d is None: missing.add(cid); continue
        k = (cid, r.get("model", "?"))
        per.setdefault(k, [[], []])
        per[k][0].append(float(r.get("score") or 0.0))
        per[k][1].append(_score(dict(ch), *d))
    for (cid, model), (old, new) in per.items():
        o, n = sum(old)/len(old), sum(new)/len(new)
        if abs(o-n) > 5e-4:
            rows.append((abs(o-n), f.parent.name, cid, model, len(old), o, n))

rows.sort(reverse=True)
print(f"{'sweep':<34}{'case':<20}{'model':<11}{'n':>3}  {'stored':>7} {'re-derived':>10}  delta")
for d, sweep, cid, model, n, o, nn in rows:
    print(f"{sweep:<34}{cid:<20}{model:<11}{n:>3}  {o:>7.3f} {nn:>10.3f}  {nn-o:+.3f}")
print(f"\n{len(rows)} (sweep,case,model) cells move." + (f"  cases w/o grader: {sorted(missing)}" if missing else ""))
