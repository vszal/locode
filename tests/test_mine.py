"""Tests for evals/mine.py — the archive miner (§5.121).

The load-bearing behaviour is rule 75: `centred` must strip case mix out of a
bucket comparison. `test_centring_removes_case_mix` builds the exact trap that
fooled the raw §5.121 numbers — two buckets with identical within-case
performance but different case mixes — and asserts the pooled means differ
while the centred means do not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import mine  # noqa: E402


def _sweep(root: Path, name: str, runs: list[tuple[str, str, int, str, float, list[dict]]]):
    """Write one sweep dir: ab.json plus an event log per run."""
    d = root / name
    (d / "events").mkdir(parents=True)
    pairs = {}
    for case, model, repeat, arm, score, events in runs:
        pairs.setdefault((case, model, repeat), {"case": case, "model": model,
                                                 "repeat": repeat})[arm] = score
        (d / "events" / f"{case}__{model}__r{repeat}__{arm}.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events))
    (d / "ab.json").write_text(json.dumps({"pairs": list(pairs.values())}))
    return d


def _run_call(name="bash", **args):
    return {"phase": "run", "name": name, "args": args}


def _repeat_nudge():
    return {"phase": "nudge", "reason": "repeated call"}


def test_iter_runs_parses_identity_and_attaches_scores(tmp_path):
    _sweep(tmp_path, "s1", [("caseA", "qwythos9", 1, "base", 0.25, [_run_call()])])
    runs = list(mine.iter_runs(tmp_path))
    assert len(runs) == 1
    r = runs[0]
    assert (r.case, r.model, r.repeat, r.arm) == ("caseA", "qwythos9", 1, "base")
    assert r.score == 0.25
    assert r.stratum == ("caseA", "qwythos9")


def test_unparseable_filenames_are_skipped_not_guessed(tmp_path):
    d = _sweep(tmp_path, "s1", [("caseA", "m", 1, "base", 0.5, [_run_call()])])
    (d / "events" / "not-a-run-name.jsonl").write_text('{"phase": "run"}\n')
    (d / "events" / "a__b__rX__base.jsonl").write_text('{"phase": "run"}\n')
    assert len(list(mine.iter_runs(tmp_path))) == 1


def test_truncated_json_tail_does_not_kill_the_run(tmp_path):
    d = _sweep(tmp_path, "s1", [("caseA", "m", 1, "base", 0.5, [_run_call()])])
    log = d / "events" / "caseA__m__r1__base.jsonl"
    log.write_text(log.read_text() + '{"phase": "resu')
    assert len(list(mine.iter_runs(tmp_path))[0].events) == 1


def test_missing_score_leaves_run_usable(tmp_path):
    d = _sweep(tmp_path, "s1", [("caseA", "m", 1, "base", 0.5, [_run_call()])])
    (d / "ab.json").write_text(json.dumps({"pairs": []}))
    assert list(mine.iter_runs(tmp_path))[0].score is None


@pytest.mark.parametrize("after, expected", [
    ([{"phase": "run", "name": "bash", "args": {"cmd": "x"}}], "SAME call again"),
    ([{"phase": "run", "name": "bash", "args": {"cmd": "y"}}], "same tool, diff args"),
    ([{"phase": "run", "name": "read_file", "args": {"cmd": "x"}}], "different tool"),
    ([{"phase": "turn_end", "result": "done"}], "gave final answer"),
    ([], None),
])
def test_next_action_classifies_against_the_repeated_call(after, expected):
    events = [_run_call(cmd="x"), _repeat_nudge()] + after
    assert mine.next_action(events, 1) == expected


def test_next_action_needs_a_prior_call():
    assert mine.next_action([_repeat_nudge(), _run_call()], 0) is None


def test_centring_removes_case_mix():
    """Rule 75: the trap that fooled §5.121's raw numbers.

    Both buckets perform IDENTICALLY within each case. Bucket "quit" is drawn
    mostly from the easy case and "work" mostly from the hard one, so the
    pooled means differ by 0.48 — an effect made entirely of mix.
    """
    easy, hard = ("easy", "m"), ("hard", "m")
    rows = ([(easy, "quit", 0.8)] * 9 + [(hard, "quit", 0.2)] * 1 +
            [(easy, "work", 0.8)] * 1 + [(hard, "work", 0.2)] * 9)
    stats = mine.centred(rows)
    quit_n, quit_pooled, quit_cent = stats["quit"]
    work_n, work_pooled, work_cent = stats["work"]

    assert quit_n == work_n == 10
    assert quit_pooled - work_pooled == pytest.approx(0.48, abs=0.01)  # pure mix
    assert quit_cent == pytest.approx(0.0, abs=1e-9)
    assert work_cent == pytest.approx(0.0, abs=1e-9)


def test_centred_reports_a_real_within_case_effect():
    """Centring must not flatten an effect that survives inside the stratum."""
    s = ("caseA", "m")
    rows = [(s, "good", 0.9), (s, "good", 0.9), (s, "bad", 0.1), (s, "bad", 0.1)]
    stats = mine.centred(rows)
    assert stats["good"][2] == pytest.approx(0.4)
    assert stats["bad"][2] == pytest.approx(-0.4)


def test_report_counts_exposure_and_failing_share(tmp_path, capsys):
    ok = [_run_call(cmd="x"), {"phase": "result", "name": "bash", "error": False},
          _repeat_nudge(), {"phase": "turn_end", "result": "done"}]
    bad = [_run_call(cmd="x"), {"phase": "result", "name": "bash", "error": True},
           _repeat_nudge(), {"phase": "turn_end", "result": "done"}]
    untouched = [_run_call(cmd="x"), {"phase": "turn_end", "result": "done"}]
    _sweep(tmp_path, "s1", [
        ("caseA", "m", 1, "base", 0.5, ok),
        ("caseA", "m", 2, "base", 0.5, bad),
        ("caseA", "m", 3, "base", 0.5, untouched),
    ])
    mine._report_nudge("repeated call", tmp_path)
    out = capsys.readouterr().out
    assert "fired in 2/3" in out
    assert "1 (50%) followed a FAILING call" in out


def test_zero_exposure_says_so_rather_than_reporting_buckets(tmp_path, capsys):
    _sweep(tmp_path, "s1", [("caseA", "m", 1, "base", 0.5, [_run_call()])])
    mine._report_nudge("no such nudge", tmp_path)
    out = capsys.readouterr().out
    assert "Zero exposure" in out
    assert "rule 52" in out


def test_list_nudges_ranks_by_frequency(tmp_path, capsys):
    events = ([{"phase": "nudge", "reason": "repeated call"}] * 3 +
              [{"phase": "nudge", "reason": "unverified edits"}])
    _sweep(tmp_path, "s1", [("caseA", "m", 1, "base", 0.5, events)])
    mine._list_nudges(tmp_path)
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert "repeated call" in lines[0] and "3" in lines[0]
    assert "unverified edits" in lines[1]
