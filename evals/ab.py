#!/usr/bin/env python3
"""Paired same-session A/B: run two versions of the agent against each other NOW.

Why this exists
---------------
The sweep-vs-saved-sweep comparison in `harness.py compare` has a confounder it
cannot remove: the two sweeps ran hours or days apart. Between them the box was
differently loaded, the server was restarted, and — the part with no fix — the
model is sampled, so its own behaviour drifts. Every gate in `harness.py` is
built to survive that drift, which is why it is deliberately hard to trip and
why a real 5-point improvement can sit under the noise floor unprovable.

The fix is not a better statistic, it is a better experiment. Run both versions
in the SAME session, alternating, and compare them run-for-run:

  - **Same session.** No restart, no reload, no overnight thermal change between
    the two arms. Whatever the box is doing today, it is doing it to both.
  - **Interleaved.** Arm order flips on every repeat, so warmup, cache state and
    slow thermal drift land on both arms equally instead of on whichever went
    first.
  - **Paired.** The statistic is the per-pair difference, so the between-run
    variance that dominates the unpaired test — one case is simply harder than
    another, one repeat drew a bad sample — cancels within the pair.

The baseline arm is a git worktree at some ref; the candidate is the live
working tree, uncommitted edits included. That is the question you actually
have: *does the thing I just wrote help?*

Usage
-----
    python evals/ab.py --base HEAD~1 -m qythos9 --repeat 6
    python evals/ab.py --base v0.1.0 --cand /path/to/other/tree -m qythos9

Exit codes mirror `harness.py compare`: 0 = no regression (improved, or no
detectable difference), 1 = the candidate is worse, 2 = the experiment did not
answer the question — either `inconclusive` or, far more often, `underpowered`.

A warning worth reading before you size a sweep (ROADMAP 5.27). Across the 22
sweeps in the archive, 3 ever reached the 6 informative pairs below which
p<0.05 is unattainable, exactly 1 ever reached p<0.05, and 68% of all 194 pairs
tied outright. On `exec-bugfix`, the workhorse case, only 43% of pairs are
informative — so settling anything on SCORE there takes ~14 runs per arm, and
the -r 8 every sweep has used expects 3.4 informative against a threshold of 6.
Grade on turn ENDINGS (`evals/armstats.py`) unless you have budgeted for that.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (  # noqa: E402
    REPO_ROOT, RESULTS_DIR, RunResult, discover_cases, run_case, summarize,
    print_report, server_fingerprint, same_server,
)

#: Captured once per process: `_persist` checkpoints after every run, and
#: shelling out to `ps` 28 times to re-learn the same pid is waste. `"unset"`
#: distinguishes "not looked yet" from "looked, found no server".
_SERVER_FP: object = "unset"


def _server_fp() -> dict | None:
    global _SERVER_FP
    if _SERVER_FP == "unset":
        _SERVER_FP = server_fingerprint()
    return _SERVER_FP  # type: ignore[return-value]


def _prior_sweep_server() -> tuple[str, dict] | None:
    """The newest earlier sweep that recorded a server, as (label, fingerprint)."""
    best: tuple[str, str, dict] | None = None
    for path in RESULTS_DIR.glob("*/ab.json"):
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        fp, created = report.get("server"), report.get("created", "")
        if fp and (best is None or created > best[0]):
            best = (created, report.get("label", path.parent.name), fp)
    return (best[1], best[2]) if best else None

# A sign-flip test on n pairs can produce at most 2**n distinct outcomes, so its
# smallest attainable two-sided p-value is 2/2**n. At 5 pairs that floor is
# 0.0625 — above alpha, so NO result, however clean, could ever be called
# significant. Running fewer pairs than this is not a weak experiment, it is an
# experiment whose answer is fixed in advance.
_ALPHA = 0.05
_MIN_PAIRS = 6
# Above this, enumerating every sign assignment costs more than sampling them.
_EXACT_MAX_PAIRS = 18
_PERM_ITERS = 20000


# --------------------------------------------------------------------------
# the two arms
# --------------------------------------------------------------------------
def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def make_worktree(ref: str, path: Path) -> str:
    """Check `ref` out into its own worktree. Returns the resolved sha."""
    sha = _git("rev-parse", "--short", ref)
    _git("worktree", "add", "--detach", "-q", str(path), ref)
    return sha


def remove_worktree(path: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                   cwd=REPO_ROOT, capture_output=True, text=True)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT,
                   capture_output=True, text=True)


def tree_digest(root: Path) -> str:
    """Content hash of a tree's `locode/` package — what actually runs.

    Compared between the arms before any GPU time is spent. Two arms that are
    byte-identical produce a delta of exactly zero, which reads as "the change
    had no effect" and is the most believable wrong answer this tool could give.
    """
    h = hashlib.sha256()
    for p in sorted((root / "locode").rglob("*.py")):
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------
# the paired statistic
# --------------------------------------------------------------------------
def pair_runs(runs: list[RunResult]) -> tuple[list[dict], list[dict]]:
    """Match base/cand runs on (case, model, repeat).

    Returns (pairs, dropped). A pair survives only if BOTH arms produced a
    verdict: an invalid run has no score, and substituting one — as a 0.0, or by
    comparing against the other arm's mean — would put a number where the
    experiment has none. Dropping in pairs is what keeps the comparison paired.
    """
    by_key: dict[tuple, dict] = {}
    for r in runs:
        key = (r.case, r.model, r.repeat)
        by_key.setdefault(key, {})[r.arm] = r

    pairs, dropped = [], []
    for key in sorted(by_key):
        arms = by_key[key]
        b, c = arms.get("base"), arms.get("cand")
        rec = {"case": key[0], "model": key[1], "repeat": key[2]}
        if b is None or c is None:
            dropped.append(dict(rec, why="incomplete pair: one arm never ran"))
            continue
        if b.invalid or c.invalid:
            why = " / ".join(f"{a}: {r.invalid}" for a, r in
                             (("base", b), ("cand", c)) if r.invalid)
            dropped.append(dict(rec, why=f"ungraded — {why}"))
            continue
        pairs.append(dict(rec, base=b.score, cand=c.score,
                          delta=round(c.score - b.score, 6)))
    return pairs, dropped


def signflip_p(deltas: list[float], iters: int = _PERM_ITERS,
               seed: int = 0) -> float:
    """Two-sided paired sign-flip (randomization) test on the mean difference.

    Under the null "the two arms are interchangeable", the sign of each pair's
    delta is arbitrary — so the null distribution is generated by flipping signs,
    not by pooling and reshuffling across pairs. Pooling would throw away the
    pairing and reintroduce exactly the between-case variance the design exists
    to cancel.

    The statistic is the SUM of the deltas, not their mean, which is what lets
    tied pairs be dropped first: flipping the sign of a zero changes nothing, so
    a tie contributes no information and the null distribution over n pairs with
    k ties is identical to the one over the n-k that moved. Dropping them keeps
    the exact-enumeration branch reachable and — via `effective_pairs` — stops a
    run padded with ties from looking better powered than it is.

    Exact by enumeration while that is cheap, sampled above that. +1 smoothing on
    the sampled branch so p is never reported as 0.
    """
    ds = [d for d in deltas if d != 0]
    n = len(ds)
    if n == 0:
        return 1.0
    obs = abs(sum(ds))
    if obs == 0:  # symmetric deltas that cancel exactly
        return 1.0
    if n <= _EXACT_MAX_PAIRS:
        ge = sum(1 for signs in itertools.product((1, -1), repeat=n)
                 if abs(sum(s * d for s, d in zip(signs, ds))) >= obs - 1e-12)
        return ge / (2 ** n)
    rng = random.Random(seed)
    ge = sum(1 for _ in range(iters)
             if abs(sum(d if rng.random() < 0.5 else -d for d in ds))
             >= obs - 1e-12)
    return (ge + 1) / (iters + 1)


def effective_pairs(deltas: list[float]) -> int:
    """Pairs that carry information: the ones where the two arms differed.

    A pair scoring the same on both arms cannot move a sign-flip test, so
    counting it toward the sample size overstates the experiment's power: six
    pairs of which five tie is a one-pair experiment wearing a six-pair label.
    """
    return sum(1 for d in deltas if d != 0)


def analyze(pairs: list[dict], dropped: list[dict]) -> dict:
    """Verdict on the paired deltas. `status` is one of improved / regressed /
    no-difference / underpowered / inconclusive.

    `underpowered` and `inconclusive` are deliberately different words. A census
    of all 22 sweeps in the archive (ROADMAP 5.27) found 3 that ever reached
    _MIN_PAIRS informative pairs and exactly 1 that ever reached p<_ALPHA, with
    68% of all 194 pairs tying — so the overwhelmingly common outcome was a
    design that COULD NOT have detected any effect, printed under a word that
    reads as a finding about the change. It is a finding about the sample.
    """
    deltas = [p["delta"] for p in pairs]
    n = len(deltas)
    n_eff = effective_pairs(deltas)
    out: dict = {
        "n_pairs": n,
        "n_effective": n_eff,
        "n_dropped": len(dropped),
        "min_pairs": _MIN_PAIRS,
        "mean_delta": round(statistics.mean(deltas), 4) if deltas else None,
        "wins": sum(1 for d in deltas if d > 0),
        "losses": sum(1 for d in deltas if d < 0),
        "ties": sum(1 for d in deltas if d == 0),
        "p": None,
        "status": "inconclusive",
        "why": "",
    }
    if n == 0:
        out["why"] = "no pair had a verdict on both arms"
        return out
    if n_eff < _MIN_PAIRS:
        # Stated as arithmetic, not as a hunch: the test cannot reach alpha here.
        out["status"] = "underpowered"
        out["p"] = signflip_p(deltas)
        tied = "" if n_eff == n else (
            f" ({n} ran, but {n - n_eff} scored the same on both arms and a tie "
            f"cannot move a sign-flip test)")
        out["why"] = (f"only {n_eff} informative pair(s){tied}; a sign-flip test "
                      f"needs {_MIN_PAIRS} before any result can reach "
                      f"p<{_ALPHA} (its floor is 2/2^n = {2 / 2 ** n_eff:.3f})")
        # The actionable half: say how many runs per arm this observed
        # informative rate would have needed. Without it the reader knows the
        # sweep failed but not what a sweep that works would cost.
        if n_eff:
            out["runs_needed"] = math.ceil(_MIN_PAIRS * n / n_eff)
            out["why"] += (f". At the observed rate ({n_eff}/{n} informative) "
                           f"this design needs ~{out['runs_needed']} runs per "
                           f"arm, not {n}")
        else:
            out["runs_needed"] = None
            out["why"] += (". Every pair tied: this case may be SATURATED and "
                           "carry no signal at any sample size — check whether "
                           "both arms are scoring at the ceiling")
        return out
    p = signflip_p(deltas)
    out["p"] = round(p, 4)
    mean = out["mean_delta"]
    if p < _ALPHA and mean > 0:
        out["status"] = "improved"
    elif p < _ALPHA and mean < 0:
        out["status"] = "regressed"
    else:
        out["status"] = "no-difference"
        out["why"] = ("the arms are not distinguishable at this sample size — "
                      "which is not the same as equal")
    return out


def per_case(pairs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for case in sorted({p["case"] for p in pairs}):
        ds = [p["delta"] for p in pairs if p["case"] == case]
        out[case] = {
            "n": len(ds),
            "mean_delta": round(statistics.mean(ds), 4),
            "base": round(statistics.mean(
                [p["base"] for p in pairs if p["case"] == case]), 4),
            "cand": round(statistics.mean(
                [p["cand"] for p in pairs if p["case"] == case]), 4),
        }
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
_STATUS_LINE = {
    "improved": "✅ IMPROVED — the candidate beat the baseline",
    "regressed": "❌ REGRESSED — the candidate lost to the baseline",
    "no-difference": "➖ NO DETECTABLE DIFFERENCE",
    "underpowered": "🚫 UNDERPOWERED — this design could not have detected "
                    "an effect of ANY size",
    "inconclusive": "⚠️  INCONCLUSIVE — the experiment did not answer the question",
}
_EXIT = {"improved": 0, "no-difference": 0, "regressed": 1, "inconclusive": 2,
         "underpowered": 2}


def print_ab_report(report: dict) -> int:
    a = report["analysis"]
    print("\n" + "=" * 72)
    print(f"PAIRED A/B · {report['label']}")
    print(f"  base : {report['base_ref']} ({report['base_sha']})")
    print(f"  cand : {report['cand_desc']}")
    print("=" * 72)

    if report["case_table"]:
        print(f"\n{'case':<24}{'n':>4}{'base':>9}{'cand':>9}{'delta':>9}")
        print("-" * 55)
        for case, row in report["case_table"].items():
            print(f"{case:<24}{row['n']:>4}{row['base']:>9.3f}"
                  f"{row['cand']:>9.3f}{row['mean_delta']:>+9.3f}")

    if a["n_pairs"]:
        tied = ("" if a["n_effective"] == a["n_pairs"]
                else f" — {a['n_effective']} informative")
        print(f"\npairs      : {a['n_pairs']}  "
              f"(W{a['wins']}/L{a['losses']}/T{a['ties']}){tied}")
        print(f"mean delta : {a['mean_delta']:+.4f}  (candidate − baseline)")
        print(f"sign-flip p: {a['p']}")
    if a["n_dropped"]:
        print(f"dropped    : {a['n_dropped']} pair(s) with an ungraded arm")
        for d in report["dropped"][:5]:
            print(f"             {d['case']} r{d['repeat']} — {d['why']}")

    if a.get("calibrated"):
        print(f"noise floor: {a['noise_floor']:+.4f} "
              f"(largest of {a['noise_floor_n']} A/A run(s), identical code)")

    print(f"\n{_STATUS_LINE[a['status']]}")
    if a["why"]:
        print(f"   {a['why']}")
    return _EXIT[a["status"]]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def _plan(cases, models, repeat):
    """(case, model, repeat, arm_order) for every pair, arm order alternating.

    The flip is per repeat so that across the run each arm goes first exactly
    half the time. Whichever arm runs second inherits a warmer cache and a hotter
    box; unflipped, that advantage would be a constant offset on one arm and
    would read as a real effect.
    """
    for model in models:
        for case in cases:
            for rep in range(1, repeat + 1):
                order = ("base", "cand") if rep % 2 else ("cand", "base")
                yield case, model, rep, order


def run_ab(*, base_root: Path, cand_root: Path | None, cases, models,
           repeat: int, results_dir: Path, label: str, base_ref: str,
           base_sha: str, keep: bool = True, identical: bool = False) -> dict:
    roots = {"base": base_root, "cand": cand_root}
    key = floor_key(cases, models, repeat)
    runs: list[RunResult] = []
    total = len(cases) * len(models) * repeat * 2
    n = 0
    for case, model, rep, order in _plan(cases, models, repeat):
        for arm in order:
            n += 1
            print(f"[{n}/{total}] {case.id} · {model} · r{rep} · {arm}…",
                  flush=True)
            r = run_case(case, model, rep, results_dir, keep=keep,
                         agent_root=roots[arm], arm=arm)
            runs.append(r)
            note = f"  [{r.invalid}]" if r.invalid else ""
            print(f"        {arm}: score={r.score:.2f} {r.seconds}s{note}",
                  flush=True)
            _persist(results_dir, runs, label, base_ref, base_sha, cand_root,
                     key, identical)
    report = _persist(results_dir, runs, label, base_ref, base_sha, cand_root,
                      key, identical)
    if identical and report["analysis"]["mean_delta"] is not None:
        record_calibration(key, report["analysis"]["mean_delta"], label)
    return report


NOISE_FLOOR = Path(__file__).resolve().parent / "noise_floor.json"


def floor_key(cases, models, repeat: int) -> str:
    """Identity of an experimental setup, for looking up its A/A noise floor."""
    return (",".join(sorted(c.id for c in cases)) + "|"
            + ",".join(sorted(models)) + f"|r{repeat}")


def _read_floor() -> dict:
    try:
        return json.loads(NOISE_FLOOR.read_text())
    except (OSError, ValueError):
        return {}


def record_calibration(key: str, mean_delta: float, label: str) -> None:
    """Append one A/A observation to the checked-in noise floor."""
    data = _read_floor()
    row = data.setdefault(key, {"samples": [], "labels": []})
    row["samples"].append(round(abs(mean_delta), 4))
    row["labels"].append(label)
    row["updated"] = time.strftime("%Y-%m-%d")
    NOISE_FLOOR.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def apply_noise_floor(analysis: dict, key: str) -> dict:
    """Refuse to call a delta real when identical code has produced as much.

    Earned on 2026-08-07: a b93-vs-b94 sweep read `+0.375, p=0.031, IMPROVED`,
    and an A/A calibration of the SAME setup — identical code in both arms —
    came back `+0.281, W5/L0`. Five n=8 samples of one build spanned 0.438 to
    0.812. The sign-flip test was not wrong about the signs; it just cannot
    know that this case's per-run score is coarse and heavy-tailed, so its
    nominal p is far too generous at this sample size. The A/A number is the
    only thing that knows.
    """
    row = _read_floor().get(key)
    mean = analysis.get("mean_delta")
    claims = analysis["status"] in ("improved", "regressed")
    if not row or not row.get("samples") or mean is None:
        analysis["calibrated"] = False
        if claims:
            analysis["status"] = "inconclusive"
            analysis["why"] = (
                "UNCALIBRATED — no A/A run has measured this setup's noise "
                "floor, so nothing here says the delta is bigger than what "
                "IDENTICAL code produces. That is not a technicality: the "
                "sweep this gate was written for read +0.375 at p=0.031 and "
                "an A/A of the same setup returned +0.281. Run "
                "`--base HEAD --allow-identical` with the same -c/-m/-r, then "
                "re-read this sweep.")
        return analysis
    floor = max(row["samples"])
    k = len(row["samples"])
    # A floor is only as good as the number of draws behind it, so demand a
    # margin over it that shrinks as calibration accumulates: 2x the floor at
    # k=2, 1.33x at k=6, approaching the floor itself once there are many. The
    # multiplier is a judgement call, not a theorem — what is not negotiable is
    # that a thin calibration has to buy a bigger effect.
    required = floor * (1 + 2 / k)
    analysis["calibrated"] = True
    analysis["noise_floor"] = round(floor, 4)
    analysis["noise_floor_n"] = k
    analysis["noise_floor_required"] = round(required, 4)
    if claims and abs(mean) <= required:
        under = "does not clear" if abs(mean) <= floor else "clears"
        analysis["status"] = "inconclusive"
        analysis["why"] = (
            f"delta {mean:+.4f} {under} the measured noise floor "
            f"{floor:.4f} but falls short of the {required:.4f} required at "
            f"k={k} calibration run(s) (identical code in both arms). The "
            f"sign-flip p is not wrong about the signs; it cannot know this "
            f"score is coarse enough that identical code has already produced "
            f"a delta this big. Add pairs, or add A/A runs to tighten the "
            f"floor.")
    return analysis


def _persist(results_dir: Path, runs: list[RunResult], label: str,
             base_ref: str, base_sha: str, cand_root: Path | None,
             key: str = "", identical: bool = False) -> dict:
    pairs, dropped = pair_runs(runs)
    report = {
        "kind": "paired-ab",
        "label": label,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "cand_desc": (str(cand_root) if cand_root else
                      f"working tree ({REPO_ROOT})"),
        "server": _server_fp(),
        "pairs": pairs,
        "dropped": dropped,
        "case_table": per_case(pairs),
        "analysis": analyze(pairs, dropped),
        "runs": [asdict(r) for r in runs],
        "summary": summarize(runs),
        "floor_key": key,
        "is_calibration": identical,
    }
    a = report["analysis"]
    if identical:
        # An A/A has no verdict to give — it MEASURES. Whatever delta it found
        # is, by construction, noise, so say so and bank it.
        a["status"] = "inconclusive"
        # _persist runs after EVERY run for checkpointing, so the first call
        # happens with one run banked and no complete pair — mean_delta is None
        # there. Formatting it unconditionally crashed the sweep on run 1, which
        # meant no A/A could ever finish: the standing 0.2812 floor rests on the
        # 2 samples that predate this call site. Say "no pair yet" instead.
        delta = a.get("mean_delta")
        a["why"] = (
            (f"A/A calibration: both arms ran identical code, so this delta of "
             f"{delta:+.4f} IS the noise floor, not a result. Recorded for "
             f"{key or 'this setup'}.")
            if delta is not None else
            (f"A/A calibration in progress for {key or 'this setup'}: no "
             f"complete pair yet, so there is no delta to record."))
    elif key:
        apply_noise_floor(a, key)
    (results_dir / "ab.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True,
                    help="git ref for the baseline arm (e.g. HEAD~1, a tag)")
    ap.add_argument("--cand", default=None,
                    help="candidate source tree (default: the live working tree)")
    ap.add_argument("-m", "--model", action="append", required=True)
    ap.add_argument("-c", "--case", action="append")
    # Above _MIN_PAIRS on purpose: eval scores tie often, and a tie carries no
    # information, so a run sized exactly at the floor lands on "underpowered"
    # the moment one pair comes out even. 8 is still not enough to settle
    # anything on score — see the module docstring and ROADMAP 5.27 — it is the
    # size at which the MECHANISM channel reads cleanly. Raise it deliberately
    # when the score is the thing you intend to believe.
    ap.add_argument("-r", "--repeat", type=int, default=8)
    ap.add_argument("--label", default=None)
    ap.add_argument("--clean", action="store_true",
                    help="delete each run's scratch workspace when it finishes")
    ap.add_argument("--allow-identical", action="store_true",
                    help="run even when both arms are byte-identical (an A/A "
                         "calibration: measures the noise floor, not a change)")
    args = ap.parse_args(argv)

    cases = discover_cases(args.case or None)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2

    label = args.label or f"ab-{time.strftime('%Y%m%d-%H%M%S')}"
    results_dir = RESULTS_DIR / label
    results_dir.mkdir(parents=True, exist_ok=True)

    cand_root = Path(args.cand).resolve() if args.cand else REPO_ROOT
    wt = Path(tempfile.mkdtemp(prefix="locode-ab-base-")) / "tree"
    base_sha = make_worktree(args.base, wt)
    try:
        identical = tree_digest(wt) == tree_digest(cand_root)
        if identical and not args.allow_identical:
            print(f"!! both arms are the same code ({args.base} == the "
                  f"candidate tree). Every delta would be noise reported as a "
                  f"result. Pass --allow-identical to run it as an A/A "
                  f"calibration.", file=sys.stderr)
            return 2
        fp, prior = _server_fp(), _prior_sweep_server()
        if fp is None:
            print("!! could not identify the model server process; this "
                  "sweep's results will not be comparable to others by "
                  "server identity.\n", flush=True)
        else:
            print(f"   server: pid {fp['pid']} up since {fp['started']}"
                  f"{' · ' + fp['model'] if fp['model'] else ''}")
            if prior and same_server(fp, prior[1]) is False:
                print(f"!! the server has RESTARTED since {prior[0]} (pid "
                      f"{prior[1]['pid']} up {prior[1]['started']}). This "
                      f"sweep's own base-vs-cand comparison is unaffected, but "
                      f"do NOT compare its absolute rate against {prior[0]} or "
                      f"anything older — a restart alone has moved clean "
                      f"finishes by 40+ points before.\n", flush=True)
        n_pairs = len(cases) * len(args.model) * args.repeat
        if n_pairs < _MIN_PAIRS:
            print(f"!! this plan yields {n_pairs} pair(s); below {_MIN_PAIRS} "
                  f"no outcome can reach p<{_ALPHA}. Raise --repeat.\n",
                  flush=True)
        report = run_ab(base_root=wt, cand_root=cand_root, cases=cases,
                        models=args.model, repeat=args.repeat,
                        results_dir=results_dir, label=label,
                        base_ref=args.base, base_sha=base_sha,
                        keep=not args.clean, identical=identical)
    finally:
        remove_worktree(wt)
        shutil.rmtree(wt.parent, ignore_errors=True)

    print_report(report["summary"], f"RUNS · {label}")
    rc = print_ab_report(report)
    print(f"\nwrote {results_dir / 'ab.json'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
