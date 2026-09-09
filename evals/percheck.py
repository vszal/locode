"""Per-check pass rates across a set of harness results dirs.

A per-case score says a case is saturated; it cannot say *why*. This separates
the three ways an individual check fails to carry information:

  ALWAYS  - passed every run. Either scaffolding (true of anything that
            produces plausible output) or a bar the model is simply past.
  NEVER   - failed every run. A wall, or a check that cannot be earned.
  MOVES   - the only kind that discriminates.

Guards and derived keys are labelled so they are never mistaken for outcomes:
a case whose only MOVES rows are guards has no dynamic range at all, it has a
flaky invariant.

Scores are RE-DERIVED from the stored per-check booleans (rule 96), never read
from the file. The stored number is a snapshot of whatever rubric was current
when the sweep ran, and reading it is what let a whole archive of pre-rule-90
scores pass for current (ROADMAP 5.151). Where the two disagree the row is
flagged `stored!=` rather than silently preferred -- that disagreement is how
the rescore defect was found in the first place.

    python evals/percheck.py evals/results/<sweep> [<sweep> ...]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from locode.bench.runner import load_grader, _score  # noqa: E402

CASE_ROOTS = ("locode/bench/cases", "evals/cases")


class _CaseRef:
    """The two attributes `load_grader` actually reads."""

    def __init__(self, path):
        self.path, self.id = path, path.name


def _decls(cid, _cache={}):
    """`(guards, derived)` for a case id, or empty sets if the case is gone."""
    if cid not in _cache:
        _cache[cid] = (frozenset(), frozenset())
        for root in CASE_ROOTS:
            d = ROOT / root / cid
            if (d / "check.py").is_file():
                _, guards, derived = load_grader(_CaseRef(d))
                _cache[cid] = (guards, derived)
                break
    return _cache[cid]


def main(argv):
    dirs = argv or sorted(str(p) for p in (ROOT / "evals" / "results").iterdir()
                          if (p / "results.json").is_file())
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    scores, stale = defaultdict(list), defaultdict(int)

    for d in dirs:
        f = Path(d) / "results.json"
        if not f.is_file():
            continue
        data = json.loads(f.read_text())
        for r in (data if isinstance(data, list) else data.get("runs", [])):
            cid, checks = r.get("case"), r.get("checks") or {}
            if not cid or not checks:
                continue
            derived_score = _score(dict(checks), *_decls(cid))
            scores[cid].append(derived_score)
            if abs(derived_score - float(r.get("score") or 0.0)) > 5e-4:
                stale[cid] += 1
            for k, v in checks.items():
                tally[cid][k][1] += 1
                tally[cid][k][0] += bool(v)

    if not tally:
        raise SystemExit(f"no results parsed from: {dirs}")

    for cid in sorted(tally):
        guards, derived = _decls(cid)
        sc = scores[cid]
        rng = (f"{min(sc):.3f} FLAT" if min(sc) == max(sc)
               else f"{min(sc):.3f}-{max(sc):.3f}")
        note = f"  [{stale[cid]}/{len(sc)} stored!=re-derived]" if stale[cid] else ""
        print(f"\n## {cid}   n={len(sc)}  score {rng}{note}")
        for k in sorted(tally[cid], key=lambda k: -tally[cid][k][0]):
            passed, total = tally[cid][k]
            kind = ("GUARD" if k in guards
                    else "DERIV" if k in derived else "out")
            verdict = ("ALWAYS" if passed == total
                       else "NEVER " if passed == 0 else "MOVES ")
            filled = round(10 * passed / total)
            bar = "#" * filled + "." * (10 - filled)
            print(f"   {verdict} [{kind:>5}] {bar} "
                  f"{passed:>2}/{total:<2}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
