"""What a case's outcome checks actually discriminate.

`percheck` gives each check's pass rate. A rate cannot say whether two checks
are one measurement wearing two names, nor whether a check is earned by doing
the hard thing or by producing plausible output at all. Both inflate a case's
mean without adding resolution, and neither is visible in a single run.

The unit of analysis is the MIXED run -- one where the outcome checks were not
unanimous. Only a mixed run can tell two checks apart; an all-true or all-false
run makes every pair agree, and pooling those in manufactures redundancy that
is really just a pile of runs that did everything or nothing.

Within the mixed runs, each outcome check is one of:

  SCAFFOLD  never varies among mixed runs. Distinct from a rule-90 guard: a
            guard is true of the untouched *seed* and is caught by inspection,
            this is true of every run that got far enough to be partial. It
            still pays full weight into the mean. This is the check that makes
            a hard case score like an easy one.
  WALL      never varies, always false: unearnable as posed.
  TWIN      moved identically to another check. One measurement, paid twice.
  TRADEOFF  moved as another check's exact inverse. NOT redundancy -- the two
            are rarely won together, which is a finding about the case, not a
            reason to merge them.
  DISTINCT  carries its own signal.

`effective` counts SCAFFOLD and WALL as zero and a TWIN group as one.

    python evals/checkdeps.py [<sweep> ...]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.percheck import _decls  # noqa: E402

MIN_MIXED = 8  # below this, agreement of 1.0 is luck rather than structure


def _load(dirs):
    """`{case: {check: {run_key: bool}}}` pooled across sweeps."""
    obs = defaultdict(lambda: defaultdict(dict))
    for d in dirs:
        f = Path(d) / "results.json"
        if not f.is_file():
            continue
        data = json.loads(f.read_text())
        runs = data if isinstance(data, list) else data.get("runs", [])
        for i, r in enumerate(runs):
            cid, checks = r.get("case"), r.get("checks") or {}
            if not cid or not checks:
                continue
            for k, v in checks.items():
                obs[cid][k][(str(d), r.get("model"), i)] = bool(v)
    return obs


def _mixed(obs_case, out_keys):
    """Run keys where the outcome checks were not unanimous."""
    per_run = defaultdict(list)
    for k in out_keys:
        for run, v in obs_case[k].items():
            per_run[run].append(v)
    return {r for r, vs in per_run.items() if len(set(vs)) > 1}


def _relate(a, b):
    """`('twin'|'tradeoff'|None, n_common)` over runs grading both."""
    common = a.keys() & b.keys()
    if len(common) < MIN_MIXED:
        return None, len(common)
    same = sum(a[k] == b[k] for k in common)
    if same == len(common):
        return "twin", len(common)
    if same == 0:
        return "tradeoff", len(common)
    return None, len(common)


def _groups(vecs, kind):
    """Union-find over pairs related by `kind`."""
    parent = {k: k for k in vecs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    names = sorted(vecs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if _relate(vecs[a], vecs[b])[0] == kind:
                parent[find(a)] = find(b)
    out = defaultdict(list)
    for k in names:
        out[find(k)].append(k)
    return sorted(out.values(), key=lambda g: (-len(g), g[0]))


def main(argv):
    dirs = argv or sorted(str(p) for p in (ROOT / "evals" / "results").iterdir()
                          if (p / "results.json").is_file())
    obs = _load(dirs)
    if not obs:
        raise SystemExit(f"no results parsed from: {dirs}")

    for cid in sorted(obs):
        guards, derived = _decls(cid)
        out = {k: v for k, v in obs[cid].items()
               if k not in guards and k not in derived}
        if not out:
            continue
        nrun = len({r for vec in out.values() for r in vec})
        mixed = _mixed(obs[cid], out)
        sub = {k: {r: v for r, v in vec.items() if r in mixed}
               for k, vec in out.items()}

        head = f"\n## {cid}   k={len(out)} outcome checks   {len(mixed)}/{nrun} runs mixed"
        if len(mixed) < MIN_MIXED:
            reason = ("every run was all-or-nothing" if not mixed
                      else f"only {len(mixed)} informative runs")
            print(f"{head}\n   -- cannot analyse: {reason}")
            continue

        scaffold = sorted(k for k, v in sub.items()
                          if v and len(set(v.values())) < 2 and any(v.values()))
        wall = sorted(k for k, v in sub.items()
                      if v and len(set(v.values())) < 2 and not any(v.values()))
        dead = set(scaffold) | set(wall)
        moving = {k: v for k, v in sub.items() if k not in dead and v}
        twins = _groups(moving, "twin")
        eff = len(twins)
        print(f"{head} -> {eff} effective of {len(out)}")
        if scaffold:
            print(f"   SCAFFOLD  {', '.join(scaffold)}")
        if wall:
            print(f"   WALL      {', '.join(wall)}")
        for g in twins:
            if len(g) > 1:
                print(f"   TWIN x{len(g)}   {', '.join(g)}")
        # Between twin-GROUPS, and pairwise: an inverse relation does not
        # chain (two inverses compose to a direct one), so union-find would
        # merge checks that are plain duplicates of each other.
        for i, ga in enumerate(twins):
            for gb in twins[i + 1:]:
                if _relate(moving[ga[0]], moving[gb[0]])[0] == "tradeoff":
                    print(f"   TRADEOFF  {ga[0]} <-inverse-> {gb[0]}"
                          f"{' (and their twins)' if len(ga) + len(gb) > 2 else ''}")
        singles = [g[0] for g in twins if len(g) == 1]
        if singles:
            print(f"   DISTINCT  {', '.join(singles)}")
        if dead:
            print(f"   -> {len(dead)}/{len(out)} = "
                  f"{len(dead) / len(out):.0%} of the mean is earned without "
                  f"discriminating anything")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
