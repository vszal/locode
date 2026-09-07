"""Tests for `locode bench` — the shipped model diagnostic.

These never start a model or touch :8081. What they pin is the reporting
contract, because that is where a diagnostic does its damage: a bench that
calls a dead server "0/4 solved", or calls a partial fix a pass, sends the
user off tuning the wrong thing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from locode.bench import runner
from locode.bench.runner import LADDER, BenchResult, CheckCtx, load_cases


def _res(case="exec-bugfix", model="m", score=1.0, seconds=10.0, iterations=5,
         infra_error=None):
    return BenchResult(case, "easy", model, 1, score, seconds, iterations, 0,
                       infra_error=infra_error)


# --- the ladder ------------------------------------------------------------
def test_the_ladder_ships_every_case_it_names():
    cases = load_cases()
    assert [c.id for c in cases] == [cid for cid, _ in LADDER]
    for c in cases:
        assert (c.path / "case.json").is_file()
        assert (c.path / "check.py").is_file()
        assert c.prompt.strip(), f"{c.id} has an empty prompt"


def test_the_ladder_runs_easy_to_hard():
    """Order is load-bearing: a model that fails the warm-up should show it in
    the first two minutes, not after the twenty-minute case."""
    assert [d for _, d in LADDER] == ["warm-up", "easy", "medium", "hard"]


def test_cases_can_be_filtered_and_an_unknown_id_is_refused():
    assert [c.id for c in load_cases(["repro-only"])] == ["repro-only"]
    with pytest.raises(SystemExit) as e:
        load_cases(["no-such-case"])
    assert "no-such-case" in str(e.value)


def test_seeds_ship_with_the_cases():
    """The seed workspace is what the model edits; a case without it is inert."""
    for c in load_cases():
        assert (c.path / "seed").is_dir(), f"{c.id} has no seed/"
        assert any((c.path / "seed").iterdir()), f"{c.id} seed/ is empty"


# --- scoring ---------------------------------------------------------------
def test_score_is_the_unweighted_mean_of_the_checks():
    assert runner._score({"a": True, "b": True}) == 1.0
    assert runner._score({"a": True, "b": False}) == 0.5
    assert runner._score({"a": 0.5, "b": 1.0}) == 0.75
    assert runner._score({}) == 0.0


def test_only_a_full_score_counts_as_solved():
    """Every ladder case is one a capable model takes to 1.000, so partial
    credit means something was left broken."""
    assert _res(score=1.0).solved
    assert not _res(score=0.99).solved
    assert not _res(score=0.0).solved


def test_an_infrastructure_failure_is_never_solved():
    assert not _res(score=1.0, infra_error="server down").solved


# --- the check contract ----------------------------------------------------
def test_checkctx_reads_case_insensitively(tmp_path):
    (tmp_path / "DESIGN.md").write_text("hello")
    ctx = CheckCtx(workdir=tmp_path, events=[], stdout="", case=None)
    assert ctx.read("design.md") == "hello"
    assert ctx.exists("design.md")
    assert ctx.read("nope.md") == ""
    assert not ctx.exists("nope.md")


def test_checkctx_bash_runs_in_the_workspace(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    ctx = CheckCtx(workdir=tmp_path, events=[], stdout="", case=None)
    assert "marker.txt" in ctx.bash("ls").stdout


def test_the_eval_harness_grades_with_this_same_class():
    """Both graders must be one class. Two copies drift, and the same case then
    scores differently in `locode bench` than in a sweep."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent.parent / "evals" / "harness.py"
    spec = importlib.util.spec_from_file_location("_harness_ctx_check", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        assert mod.CheckCtx is CheckCtx
    finally:
        sys.modules.pop(spec.name, None)


# --- run_case's failure handling -------------------------------------------
def test_a_run_that_logs_no_events_is_an_error_not_a_zero(tmp_path, monkeypatch):
    """The whole point: a dead server must not be reported as a bad model."""
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "connection refused")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    case = load_cases(["exec-bugfix"])[0]
    r = runner.run_case(case, "somemodel")
    assert r.infra_error is not None
    assert "connection refused" in r.infra_error
    assert not r.solved
    assert r.score == 0.0


def test_the_scratch_workspace_is_cleaned_up(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cwd"] = Path(kw["cwd"])
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_case(load_cases(["exec-bugfix"])[0], "m")
    assert not seen["cwd"].exists()


def test_the_seed_is_copied_into_the_workspace(monkeypatch):
    files = {}

    def fake_run(cmd, **kw):
        wd = Path(kw["cwd"])
        files["names"] = sorted(p.name for p in wd.iterdir())
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_case(load_cases(["exec-bugfix"])[0], "m")
    assert files["names"], "the model was handed an empty workspace"


# --- reporting -------------------------------------------------------------
def _report(results, models, repeat=1):
    cases = [c for c in load_cases() if c.id in {r.case for r in results}]
    return runner.format_report(results, models, cases, repeat)


def test_the_verdict_prefers_solving_more_over_finishing_sooner():
    out = _report([_res(model="slow_but_right", score=1.0, seconds=300),
                   _res(model="fast_but_wrong", score=0.5, seconds=10)],
                  ["slow_but_right", "fast_but_wrong"])
    assert "slow_but_right recommended" in out
    assert "solves more" in out


def test_time_breaks_a_tie_on_solved():
    out = _report([_res(model="quick", score=1.0, seconds=50),
                   _res(model="slow", score=1.0, seconds=200)],
                  ["quick", "slow"])
    assert "quick recommended" in out
    assert "finishes sooner" in out


def test_an_infrastructure_failure_suppresses_the_verdict():
    """Never recommend a model on the strength of runs that never happened."""
    out = _report([_res(model="a", score=1.0, seconds=50),
                   _res(model="b", infra_error="server down", score=0.0)],
                  ["a", "b"])
    assert "recommended" not in out
    assert "not a score" in out
    assert "ERROR" in out


def test_a_single_run_says_so_and_repeats_do_not():
    two = [_res(model="a", score=1.0, seconds=10),
           _res(model="b", score=1.0, seconds=99)]
    assert "--repeat 3" in _report(two, ["a", "b"], repeat=1)
    assert "--repeat 3" not in _report(two, ["a", "b"], repeat=3)


def test_the_report_leads_with_time_not_generation_rate():
    out = _report([_res(model="a", score=1.0, seconds=42)], ["a"])
    assert "time-to-done" in out
    assert "chars/s" not in out and "tok/s" not in out


def test_a_single_model_gets_a_plain_summary_not_a_recommendation():
    out = _report([_res(model="only", score=1.0, seconds=42)], ["only"])
    assert "only: solved 1/1 in 42s." in out


# --- CLI wiring ------------------------------------------------------------
def test_bench_list_exits_clean(capsys):
    from locode.cli import main

    assert main(["bench", "--list"]) == 0
    out = capsys.readouterr().out
    for case_id, _ in LADDER:
        assert case_id in out


def test_bench_is_discoverable_from_the_top_level_help(capsys):
    """`bench` is dispatched before argparse sees it, so it can only appear in
    --help if someone remembers to list it. Guard that."""
    from locode.cli import build_parser

    help_text = build_parser().format_help()
    assert "locode bench" in help_text


def test_no_build_junk_ships_inside_a_seed():
    """`exec-pinpoint/seed/.pytest_cache` was committed once and would have been
    copied into every user's workspace. The seed is what the model sees; keep it
    to the files the case is actually about."""
    junk = []
    for c in load_cases():
        for p in (c.path / "seed").rglob("*"):
            if p.name in {"__pycache__", ".pytest_cache", ".DS_Store"}:
                junk.append(str(p.relative_to(c.path.parent)))
    assert not junk, f"build junk in shipped seeds: {junk}"


def test_a_broken_grader_is_an_error_not_a_zero(monkeypatch):
    """One bad grader must not abandon the rest of the ladder, and must not be
    reported as the model failing the case."""
    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--log-events") + 1]).write_text(
            '{"phase": "iteration"}\n')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def boom(case, ctx):
        raise RuntimeError("grader is broken")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_grade", boom)
    r = runner.run_case(load_cases(["exec-bugfix"])[0], "m")
    assert r.infra_error and "grader is broken" in r.infra_error
    assert not r.solved


def test_keep_preserves_the_events_log_inside_the_workspace(monkeypatch, capsys):
    """--keep exists to let you read what the model did; dropping the event log
    would keep the workspace and throw away the record."""
    kept = {}

    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--log-events") + 1]).write_text(
            '{"phase": "iteration"}\n')
        kept["cwd"] = Path(kw["cwd"])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    try:
        runner.run_case(load_cases(["exec-bugfix"])[0], "m", keep=True)
        assert (kept["cwd"] / "events.jsonl").is_file()
        assert "workspace kept" in capsys.readouterr().out
    finally:
        import shutil as _sh
        _sh.rmtree(kept["cwd"], ignore_errors=True)
