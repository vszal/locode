#!/usr/bin/env python3
"""Does locode's harness beat a mature one, with the model held constant?

Every result in this repo so far compares *models* through locode. That leaves
the obvious question unanswered: how much of a score is the model, and how much
is the scaffolding around it? The way to find out is to hold the model fixed and
swap the harness. Aider is the natural comparator -- it is mature, it drives an
OpenAI-compatible endpoint, and the task set here was lifted from its own
benchmark.

THE DESIGN
----------
One model (`--model`, served on one endpoint), one task set (the generated
polyglot cases), one grader (each case's own `check.py`, unchanged). The only
thing that varies is which program sits between the model and the files.

This is a *within-model* comparison, so rule 98 applies and -- unusually -- can
actually be satisfied: both arms speak to the same already-running server, so
they share a server invocation, and the arms are interleaved case by case. That
is why `--base-url` is required rather than letting either tool start its own
server: two invocations would confound the comparison with the 2.00x invocation
effect from §5.152, which is as large as anything this suite measures.

Which arm runs first alternates by case index. Within a case the second arm
runs against a warmer server, and always putting the same arm there would hand
it a systematic advantage; alternating spreads it over both.

WHAT IS EQUALISED, AND WHAT IS NOT
----------------------------------
Equalised: the model, the endpoint, the seed workspace, the instructions, the
grader, and the wall-clock budget (`--budget`, default 600s -- the same flat
budget rule 91 gives a graded locode run).

Not equalised, and it cannot be: the internal loop. locode gets 40 iterations.
Aider has no iteration budget -- it reflects at most 3 times per invocation, a
class attribute with no CLI flag -- so its own benchmark protocol re-invokes it
with the failing test output as the next message. This runner does the same,
and keeps re-invoking until the tests pass or the budget is gone. That is
deliberately the *generous* reading of aider: it removes "stopped after three
reflections" as an artefact, so a loss here cannot be blamed on a stingy cap.

Report `attempts` alongside the score. A win that needs six invocations to a
locode win in one is a different claim from a win in one.

    # start the server yourself and leave it up for the whole comparison
    python evals/aider_compare.py --model qwen38 --base-url http://127.0.0.1:8081/v1
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import (  # noqa: E402
    CheckCtx, discover_cases, parse_events, metrics_from_events,
    LOCODE_BIN, GRADED_MAX_ITERATIONS, _run_setup, _load_grader, _score,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
AIDER_BIN = ROOT / "evals" / ".aidervenv" / "bin" / "aider"
CONFIG = Path.home() / ".config" / "locode" / "config.toml"


@dataclass
class ArmResult:
    arm: str
    case_id: str
    score: float
    checks: dict
    seconds: float
    attempts: int = 1
    iterations: int = 0
    budget_stopped: bool = False      # rule 99: an unknown, not a failure
    error: str = ""
    workdir: str = ""


@dataclass
class Pair:
    case_id: str
    locode: ArmResult = None
    aider: ArmResult = None


# --------------------------------------------------------------------------
# alias resolution
# --------------------------------------------------------------------------

def resolve_alias(alias: str) -> str:
    """`qwen38` -> `lukaskremla/Qwen3.8-27B-3bit-MLX-TextOnly`.

    Read from the same config locode reads, so the two arms cannot silently
    end up on different weights -- which would make the whole comparison
    meaningless while still producing a tidy-looking number.
    """
    if not CONFIG.is_file():
        raise SystemExit(f"no locode config at {CONFIG}")
    data = tomllib.loads(CONFIG.read_text())
    aliases = data.get("aliases") or {}
    if alias not in aliases:
        raise SystemExit(
            f"alias {alias!r} not in {CONFIG} -- known: {', '.join(sorted(aliases))}")
    return aliases[alias]


def v1(base_url: str) -> str:
    """Aider and the probe want the `/v1` root; locode wants the bare origin."""
    b = base_url.rstrip("/")
    return b if b.endswith("/v1") else b + "/v1"


def origin(base_url: str) -> str:
    b = base_url.rstrip("/")
    return b[:-3].rstrip("/") if b.endswith("/v1") else b


def assert_server_serves(base_url: str, model_id: str) -> None:
    """Fail loudly if the endpoint is not offering the weights we asked for.

    Cheap, and it forecloses the worst failure mode this script has: a silent
    fallback to whatever the server happened to have loaded would produce a
    perfectly plausible table comparing two harnesses on two different models.
    """
    import urllib.request
    url = v1(base_url) + "/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as fh:
            served = {m["id"] for m in json.load(fh).get("data", [])}
    except Exception as exc:
        raise SystemExit(f"cannot reach {url}: {exc}")
    if model_id not in served:
        raise SystemExit(
            f"{base_url} does not list {model_id}.\n"
            f"  serving: {', '.join(sorted(served)) or '(nothing)'}")


# --------------------------------------------------------------------------
# the two arms
# --------------------------------------------------------------------------

def run_locode(case, model, workdir, budget, base_url) -> ArmResult:
    """One graded locode run, invoked the way evals/harness.py invokes it."""
    log = workdir / "_events.jsonl"
    cmd = [str(LOCODE_BIN), "-p", case.prompt, "-m", model,
           "--base-url", origin(base_url),
           "--log-events", str(log), "--no-markdown",
           "--allow-tool", ",".join(case.allow_tools),
           "--max-iterations", str(GRADED_MAX_ITERATIONS)] + list(case.extra_args)
    # [rule 91] last, so it beats the case's own extra_args
    cmd += ["--max-wallclock", str(budget), "--progress-grant", "0"]

    env = dict(os.environ, NO_COLOR="1")
    # Both arms share ONE server invocation (rule 98), so locode must not own
    # the process: `manage=no` stops it starting, stopping or model-switching
    # the endpoint out from under the arm that is not currently running.
    env["LOCODE_MANAGE_SERVER"] = "no"
    env["LOCODE_BASE_URL"] = origin(base_url)
    t0 = time.monotonic()
    out = workdir / "_locode.txt"
    with out.open("w") as fh:
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, text=True,
                                stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            proc.wait(timeout=budget + 120)
        except subprocess.TimeoutExpired:
            proc.kill()
    seconds = round(time.monotonic() - t0, 1)

    events = parse_events(log) if log.is_file() else []
    metrics = metrics_from_events(events)
    stdout = out.read_text(errors="replace")
    stop = str(metrics.get("stop_reason") or "").lower()
    return ArmResult(
        arm="locode", case_id=case.id, score=0.0, checks={}, seconds=seconds,
        attempts=1, iterations=metrics.get("iterations") or 0,
        budget_stopped=stop.startswith("budget:")
                       and not stop.startswith("budget: no progress"),
        workdir=str(workdir),
    ), events, stdout


# Aider prints its own summary lines; this is the only one worth mining.
_TOKENS = re.compile(r"Tokens:")


def run_aider(case, model_id, workdir, budget, base_url, test_cmd,
              stub, protected) -> ArmResult:
    """Aider, re-invoked with the failing test output until the budget is gone.

    Mirrors aider's own benchmark protocol (a first attempt from the
    instructions, then attempts seeded with what the tests said) rather than
    the single shot a naive comparison would give it.
    """
    env = dict(os.environ, NO_COLOR="1", AIDER_ANALYTICS="false")
    env["OPENAI_API_KEY"] = "not-needed-local"
    env["OPENAI_API_BASE"] = v1(base_url)

    base = [str(AIDER_BIN),
            "--model", f"openai/{model_id}",
            "--openai-api-base", v1(base_url),
            "--openai-api-key", "not-needed-local",
            "--no-git",                 # locode's cases run in a plain dir
            "--yes-always",             # headless: never prompt
            "--no-stream", "--no-pretty", "--no-analytics",
            "--no-check-update", "--no-show-model-warnings",
            "--map-tokens", "0",        # single-file exercises; no repo map
            "--no-auto-commits",
            "--test-cmd", test_cmd,
            "--file", stub]
    for name in protected:              # context, but not editable
        base += ["--read", name]

    message = case.prompt
    deadline = time.monotonic() + budget
    t0 = time.monotonic()
    attempts = 0
    transcript = workdir / "_aider.txt"
    transcript.write_text("")

    while True:
        left = deadline - time.monotonic()
        if left <= 5:
            break
        attempts += 1
        with transcript.open("a") as fh:
            fh.write(f"\n===== attempt {attempts} ({left:.0f}s left) =====\n")
            proc = subprocess.Popen(base + ["--message", message], cwd=workdir,
                                    env=env, text=True, stdout=fh,
                                    stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                proc.wait(timeout=left)
            except subprocess.TimeoutExpired:
                proc.kill()
                break

        probe = subprocess.run(test_cmd, shell=True, cwd=workdir,
                               capture_output=True, text=True, timeout=180)
        if probe.returncode == 0:
            break
        tail = (probe.stdout + probe.stderr)[-4000:]
        message = ("The tests still fail. Fix `" + stub + "` so they pass.\n"
                   "Do not edit the test file.\n\n" + tail)

    seconds = round(time.monotonic() - t0, 1)
    return ArmResult(
        arm="aider", case_id=case.id, score=0.0, checks={}, seconds=seconds,
        attempts=attempts, budget_stopped=seconds >= budget - 5,
        workdir=str(workdir),
    ), [], transcript.read_text(errors="replace")


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------

def grade(case, workdir, events, stdout, result) -> ArmResult:
    """Score with the case's own check.py -- identical for both arms.

    Goes through the harness's own `_load_grader`/`_score` rather than calling
    `check()` directly, so GUARDS and DERIVED are honoured exactly as they are
    in a sweep (rule 90). A second scoring path here would let the same checks
    mean two different numbers.
    """
    checker, guards, derived = _load_grader(case)
    if checker is None:
        result.error = "no checker"
        return result
    try:
        ctx = CheckCtx(workdir=workdir, events=events, stdout=stdout, case=case)
        checks = dict(checker(ctx))
    except Exception as exc:
        result.error = f"checker raised: {type(exc).__name__}: {exc}"
        return result
    result.checks = checks
    result.score = _score(checks, guards, derived)
    return result


def _grader_module(case):
    """Import the case's check.py and read its constants directly.

    The generated grader already states the command it will run and the files
    it protects; scraping them back out with a regex would be a second, silently
    divergent source of truth for both.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"_cmp_{case.id.replace('-', '_')}", case.path / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _test_command(mod) -> str:
    """The command the grader runs, minus its shell redirection.

    Both arms get exactly this: locode is told it in `prompt.md`, and aider
    runs it through `--test-cmd`. If it drifted from what `check.py` executes,
    a model could be chasing a green run the grader never sees.
    """
    return str(mod.TEST_CMD).replace(" 2>&1", "")


def _protected(mod) -> list:
    return sorted(mod.SPEC_SHAS)


def _stub(case) -> str:
    """The file the model is asked to write: named in prompt.md's imperative."""
    m = re.search(r"Implement the above in `([^`]+)`", case.prompt)
    if not m:
        raise SystemExit(f"{case.id}: cannot find the stub name in prompt.md")
    return m.group(1)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="qwen38", help="locode alias")
    ap.add_argument("--base-url", default="http://127.0.0.1:8081",
                    help="an ALREADY RUNNING endpoint, e.g. http://127.0.0.1:8081 "
                         "-- both arms share it, which is what makes this a "
                         "rule-98-clean comparison. Give it with or without /v1")
    ap.add_argument("--prefix", default="polyglot-",
                    help="case-id prefix to sweep (default: the python track)")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--budget", type=int, default=600,
                    help="flat wall-clock seconds per arm per case (rule 91)")
    ap.add_argument("--label", default="")
    ap.add_argument("--scratch", default="",
                    help="where workspaces go (default: a temp dir per run)")
    args = ap.parse_args(argv)

    if not AIDER_BIN.is_file():
        raise SystemExit(
            f"no aider at {AIDER_BIN}\n"
            "  python3 -m venv evals/.aidervenv && "
            "evals/.aidervenv/bin/pip install aider-chat")

    model_id = resolve_alias(args.model)
    assert_server_serves(args.base_url, model_id)

    cases = [c for c in discover_cases(args.case or None)
             if c.id.startswith(args.prefix)]
    # `polyglot-js-` also starts with `polyglot-`; only take it if asked for.
    if args.prefix == "polyglot-":
        cases = [c for c in cases if not c.id.startswith("polyglot-js-")]
    if not cases:
        raise SystemExit(f"no cases matching {args.prefix!r}")

    label = args.label or time.strftime("aider-compare-%Y%m%d-%H%M%S")
    out_dir = RESULTS / label
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(args.scratch) if args.scratch else out_dir / "work"
    scratch.mkdir(parents=True, exist_ok=True)

    print(f"{len(cases)} cases  model={args.model} ({model_id})  "
          f"budget={args.budget}s  -> {out_dir}")

    pairs = []
    for i, case in enumerate(cases):
        pair = Pair(case_id=case.id)
        gmod = _grader_module(case)
        test_cmd = _test_command(gmod)
        stub, protected = _stub(case), _protected(gmod)
        # Alternate which arm meets the warmer server.
        order = ["locode", "aider"] if i % 2 == 0 else ["aider", "locode"]
        for arm in order:
            work = scratch / f"{case.id}__{arm}"
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(case.path / "seed", work)
            err = _run_setup(case, work)
            if err:
                res = ArmResult(arm=arm, case_id=case.id, score=0.0, checks={},
                                seconds=0.0, error=f"setup.sh failed: {err}")
                setattr(pair, arm, res)
                continue
            if arm == "locode":
                res, events, stdout = run_locode(
                    case, args.model, work, args.budget, args.base_url)
            else:
                res, events, stdout = run_aider(
                    case, model_id, work, args.budget, args.base_url,
                    test_cmd, stub, protected)
            setattr(pair, arm, grade(case, work, events, stdout, res))
        pairs.append(pair)
        l, a = pair.locode, pair.aider
        print(f"[{i+1}/{len(cases)}] {case.id:38} "
              f"locode={l.score:.2f} ({l.seconds:5.0f}s, {l.iterations} it)  "
              f"aider={a.score:.2f} ({a.seconds:5.0f}s, {a.attempts} att)"
              + ("  [budget]" if l.budget_stopped or a.budget_stopped else ""))
        (out_dir / "results.json").write_text(json.dumps({
            "label": label, "model": args.model, "model_id": model_id,
            "base_url": args.base_url, "budget": args.budget,
            "pairs": [{"case_id": p.case_id,
                       "locode": asdict(p.locode) if p.locode else None,
                       "aider": asdict(p.aider) if p.aider else None}
                      for p in pairs],
        }, indent=2) + "\n")

    report(pairs)
    return 0


def mcnemar_exact(a_only: int, b_only: int) -> float:
    """Two-sided exact McNemar p for `a_only` vs `b_only` discordant pairs.

    Only the discordant pairs carry information: a case both harnesses solved,
    or neither did, says nothing about which is better. Under the null they
    split 50/50, so this is a two-sided sign test on `n = a_only + b_only`
    trials -- exact rather than chi-square because n here is routinely under 20.

    Returns 1.0 when there is nothing to test, which is the honest answer: no
    discordant pairs is no evidence, not a significant tie.
    """
    n = a_only + b_only
    if n == 0:
        return 1.0
    from math import comb
    k = min(a_only, b_only)
    return min(2.0 * sum(comb(n, j) for j in range(k + 1)) / 2 ** n, 1.0)


def report(pairs):
    """McNemar on the discordant pairs, and nothing before correctness.

    Rule 89: neither clock decides anything until correctness has. Rule 99: a
    budget-stopped run is an unknown, so it is named rather than averaged into
    a verdict.
    """
    solved = lambda r: bool(r) and r.score >= 1.0
    both = [p for p in pairs if p.locode and p.aider]
    lo = sum(solved(p.locode) for p in both)
    ai = sum(solved(p.aider) for p in both)
    l_only = [p.case_id for p in both if solved(p.locode) and not solved(p.aider)]
    a_only = [p.case_id for p in both if solved(p.aider) and not solved(p.locode)]

    print("\n" + "=" * 68)
    print(f"solved   locode {lo}/{len(both)}      aider {ai}/{len(both)}")
    print(f"discordant pairs: locode-only {len(l_only)}, aider-only {len(a_only)}")
    if l_only or a_only:
        p = mcnemar_exact(len(l_only), len(a_only))
        print(f"McNemar exact (two-sided): p = {p:.4f}")
    else:
        print("McNemar: no discordant pairs -- no evidence either way")
    if l_only:
        print("  locode only: " + " ".join(l_only))
    if a_only:
        print("  aider only:  " + " ".join(a_only))

    censored = [f"{p.case_id}/{a.arm}" for p in both for a in (p.locode, p.aider)
                if a.budget_stopped]
    if censored:
        print(f"\n[rule 99] {len(censored)} run(s) stopped by the budget -- these "
              "are unknowns, and their 0.00s are lower bounds, not failures:")
        print("  " + " ".join(censored))

    done = [p for p in both if solved(p.locode) and solved(p.aider)]
    if done:
        import statistics
        print(f"\non the {len(done)} cases BOTH solved (rule 89: correctness "
              "gated first):")
        print(f"  median seconds   locode "
              f"{statistics.median(p.locode.seconds for p in done):6.0f}   "
              f"aider {statistics.median(p.aider.seconds for p in done):6.0f}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
