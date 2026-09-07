"""Tests for `locode bench` — the shipped model diagnostic.

These never start a model or touch :8081. What they pin is the reporting
contract, because that is where a diagnostic does its damage: a bench that
calls a dead server "0/4 solved", or calls a partial fix a pass, sends the
user off tuning the wrong thing.
"""

from __future__ import annotations

import itertools
import pathlib
import shutil
import subprocess
import tempfile
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


def test_a_failed_guard_vetoes_the_whole_score():
    """Guards are true of the untouched seed, so they can only ever be lost --
    by cheating or regressing. Losing one is not a deduction, it is a zero."""
    ck = {"tests_pass": True, "did_not_edit_tests": False}
    assert runner._score(ck, guards={"did_not_edit_tests"}) == 0.0
    assert runner._score(ck) == 0.5  # flat, it would have read as half-done


def test_guards_that_hold_earn_no_credit():
    """A model that changes nothing holds every guard and deserves nothing."""
    assert runner._score({"tests_pass": False, "suite_intact": True},
                         guards={"suite_intact"}) == 0.0
    assert runner._score({"tests_pass": True, "suite_intact": True},
                         guards={"suite_intact"}) == 1.0


def test_derived_checks_are_reported_but_not_double_counted():
    ck = {"a": True, "b": False, "fully_fixed": False}
    assert runner._score(ck, derived={"fully_fixed"}) == 0.5
    assert runner._score(ck) == 0.333


def test_a_grader_that_declares_away_every_outcome_scores_zero():
    """Loud, not silently perfect: nothing was measured."""
    assert runner._score({"guard": True}, guards={"guard"}) == 0.0


def test_the_untouched_seed_earns_nothing_on_every_shipped_case():
    """The regression that motivated guards: flat averaging paid 0.500 on
    three of four cases for making no change at all (ROADMAP 5.142)."""
    for case in load_cases():
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td) / "w"
            shutil.copytree(case.path / "seed", wd)
            ctx = runner.CheckCtx(workdir=wd, events=[], stdout="", case=case)
            checks, score = runner._grade(case, ctx)
        assert score == 0.0, f"{case.id} pays {score} for doing nothing: {checks}"
        # And the aggregate really is one -- the premise the test above needs.
        _, _, derived = runner.load_grader(case)
        for name in derived:
            rest = [v for k, v in checks.items() if k not in derived]
            assert checks[name] == all(rest), f"{case.id}: {name} is not a conjunction"


def test_the_split_leaves_the_solved_verdict_unchanged():
    """Archived pass/fail has to survive the rescale: a run is solved iff every
    outcome held and no guard was lost, which is exactly the old all-true."""
    guards, derived = {"g"}, {"d"}
    for out, g in itertools.product([False, True], repeat=2):
        # `d` is the conjunction of the rest, which is what DERIVED means and
        # what all four shipped graders compute. The equivalence is only true
        # of an aggregate that is genuinely an aggregate: a DERIVED key set to
        # anything looser would move the verdict, not just the scale.
        ck = {"out": out, "g": g, "d": out and g}
        assert (runner._score(ck) == 1.0) == (
            runner._score(ck, guards, derived) == 1.0), ck


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


def test_totals_are_per_pass_not_summed_over_repeats():
    """At --repeat 3 a summed time would report three ladders as one. The
    headline is "how long do I wait", so totals divide by the repeat count."""
    rows = [_res(case=c, model="a", score=1.0, seconds=100, iterations=5)
            for c in ("exec-pinpoint", "exec-bugfix") for _ in range(3)]
    out = _report(rows, ["a"], repeat=3)
    assert "time-to-done/pass" in out
    assert "200s" in out and "600s" not in out
    assert "iterations/pass" in out
    # solved stays a count of runs: that is the reliability number.
    assert "6/6" in out


def test_a_partly_solved_case_shows_the_hit_rate():
    """Two of three passes solving is the fact --repeat exists to surface; it
    must not round to ok or to FAIL."""
    rows = [_res(case="repro-only", model="a", score=s, seconds=100)
            for s in (1.0, 1.0, 0.5)]
    out = _report(rows, ["a"], repeat=3)
    assert "2/3" in out


# --- remote endpoints ------------------------------------------------------
def test_endpoint_flags_reach_the_child_process(monkeypatch):
    """A dedicated GPU box on the LAN is the machine most worth benchmarking;
    without this, bench could only ever measure localhost."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_case(load_cases(["exec-bugfix"])[0], "m",
                    server_args=["--base-url", "http://gpu-box.lan:8081"])
    cmd = seen["cmd"]
    assert "--base-url" in cmd
    assert cmd[cmd.index("--base-url") + 1] == "http://gpu-box.lan:8081"


def test_endpoint_flags_precede_the_cases_own_args(monkeypatch):
    """A case's extra_args (iteration/wallclock caps) must stay last so a case
    can still override what the endpoint flags do not set."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    case = load_cases(["multi-defect-blind"])[0]
    assert case.extra_args, "this test needs a case that carries extra_args"
    runner.run_case(case, "m", server_args=["--host", "10.0.0.5"])
    cmd = seen["cmd"]
    assert cmd.index("--host") < cmd.index(case.extra_args[0])


def test_bench_forwards_the_endpoint_and_names_it(monkeypatch, capsys):
    from locode.cli import main

    calls = []

    def fake_run_case(case, model, rep, keep=False, server_args=None):
        calls.append(server_args)
        return BenchResult(case.id, case.difficulty, model, rep, 1.0, 1.0, 1, 0)

    monkeypatch.setattr(runner, "run_case", fake_run_case)
    rc = main(["bench", "-c", "exec-bugfix", "-m", "m",
               "--base-url", "http://gpu-box.lan:8081"])
    assert rc == 0
    assert calls == [["--base-url", "http://gpu-box.lan:8081"]]
    # The report must say which box produced it, or a pasted result is unreadable.
    assert "http://gpu-box.lan:8081" in capsys.readouterr().out


def test_bench_without_endpoint_flags_forwards_nothing(monkeypatch):
    from locode.cli import main

    calls = []

    def fake_run_case(case, model, rep, keep=False, server_args=None):
        calls.append(server_args)
        return BenchResult(case.id, case.difficulty, model, rep, 1.0, 1.0, 1, 0)

    monkeypatch.setattr(runner, "run_case", fake_run_case)
    main(["bench", "-c", "exec-bugfix", "-m", "m"])
    assert calls == [[]]


# --- rule 91: a graded run gets a flat wallclock, unoverridably -------------

def _capture_bench_argv(monkeypatch, tmp_path, extra_args):
    """Run one bench case with the child process stubbed, and return its argv."""
    seen: dict = {}
    real_run = runner.subprocess.run

    def fake_run(cmd, *a, **kw):
        # setup.sh (a list starting with "bash") still runs for real; the locode
        # invocation is the one under test.
        if cmd and cmd[0] == "bash":
            return real_run(cmd, *a, **kw)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    case = runner.Case(
        id="t", difficulty="easy", description="", path=tmp_path,
        prompt="do it", allow_tools=["bash"], timeout=60,
        extra_args=list(extra_args))
    runner.run_case(case, "m")
    return seen["cmd"]


def test_a_graded_run_always_gets_a_flat_wallclock(monkeypatch, tmp_path):
    cmd = _capture_bench_argv(monkeypatch, tmp_path, [])
    assert "--progress-grant" in cmd
    assert cmd[cmd.index("--progress-grant") + 1] == "0"


def test_a_case_cannot_buy_itself_an_extendable_budget(monkeypatch, tmp_path):
    # The whole point of appending it last. A case that tries to opt into the
    # interactive budget would make its time-to-done incomparable with the
    # archive; extra_args must not be able to reach that.
    cmd = _capture_bench_argv(
        monkeypatch, tmp_path, ["--progress-grant", "600"])
    # argparse takes the LAST occurrence, so ours must come after theirs.
    last = len(cmd) - 1 - cmd[::-1].index("--progress-grant")
    assert cmd[last + 1] == "0"


def test_the_harness_pins_it_too():
    # evals/harness.py builds its own argv and must carry the same invariant;
    # the two runners drifting is exactly how a sweep silently changes meaning.
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "evals" / "harness.py").read_text()
    assert '"--progress-grant", "0"' in src
