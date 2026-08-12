"""Tests for evals/ambig_next.py and evals/nudge_next.py."""
import json
import importlib.util
from pathlib import Path

import pytest


def _load(name):
    """Load an evals/*.py module by path (evals/ is not a package)."""
    repo_root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        name, repo_root / "evals" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ambig_next():
    """Load the ambig_next module."""
    return _load("ambig_next")


@pytest.fixture
def nudge_next():
    """Load the nudge_next module (imports `events` from its own ambig_next)."""
    return _load("nudge_next")


def write_run(tmp_path, case, model, repeat, arm, events):
    """Write one events/CASE__MODEL__rN__ARM.jsonl file under tmp_path."""
    d = tmp_path / "events"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{case}__{model}__r{repeat}__{arm}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def run_dict(case="c", model="m", repeat=1):
    """Build a minimal ab.json run dict."""
    return {"case": case, "model": model, "repeat": repeat}


class TestArmRatesBuckets:
    """evals/ambig_next.py: arm_rates() classification of the call after an
    ambiguity error."""

    def test_occurrence_bucket(self, ambig_next, tmp_path):
        """edit_file ambiguity followed by edit_file(occurrence=...) -> occurrence."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": f"error: {ambig_next.AMBIG} more text"},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 2}},
            {"phase": "result", "content": "ok"},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["occurrence"] == 1
        assert sum(b.values()) == n

    def test_noop_bucket(self, ambig_next, tmp_path):
        """edit_file ambiguity followed by edit_file whose result is ALREADY DONE -> noop."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.ALREADY},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["noop"] == 1
        assert sum(b.values()) == n

    def test_otheredit_bucket(self, ambig_next, tmp_path):
        """edit_file ambiguity followed by an edit_file that is neither
        occurrence-carrying nor an already-done noop -> otheredit."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": "ok"},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["otheredit"] == 1
        assert sum(b.values()) == n

    def test_nonedit_bucket_bash(self, ambig_next, tmp_path):
        """edit_file ambiguity followed by a bash call -> nonedit."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "bash", "args": {"cmd": "ls"}},
            {"phase": "result", "content": "ok"},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["nonedit"] == 1
        assert sum(b.values()) == n

    def test_nonedit_bucket_read_file(self, ambig_next, tmp_path):
        """edit_file ambiguity followed by a read_file call -> nonedit."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "read_file", "args": {"path": "a.py"}},
            {"phase": "result", "content": "contents"},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["nonedit"] == 1
        assert sum(b.values()) == n

    def test_noneext_bucket_when_ambiguity_is_last_call(self, ambig_next, tmp_path):
        """edit_file ambiguity that is the last call in the run -> noneext."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1
        assert b["noneext"] == 1
        assert sum(b.values()) == n

    def test_buckets_always_sum_to_ambig_count(self, ambig_next, tmp_path):
        """A run with a mix of all five outcomes: bucket sum == n_ambig."""
        write_run(tmp_path, "c", "m", 1, "base", [
            # 1: occurrence
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 1}},
            {"phase": "result", "content": "ok"},
            # 2: noop
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.ALREADY},
            # 3: nonedit
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "bash", "args": {}},
            {"phase": "result", "content": "ok"},
            # 4: noneext (last call)
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 4
        assert sum(b.values()) == n
        assert b == {"occurrence": 1, "noop": 1, "otheredit": 0,
                      "nonedit": 1, "noneext": 1}

    def test_non_ambiguous_result_is_ignored(self, ambig_next, tmp_path):
        """edit_file whose result does NOT contain the ambiguity marker
        contributes nothing to the count or any bucket."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": "all good, no marker here"},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 1}},
            {"phase": "result", "content": "ok"},
        ])
        n, b = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 0
        assert sum(b.values()) == 0

    def test_counts_accumulate_across_multiple_runs(self, ambig_next, tmp_path):
        """Counts from several runs in ab.json add up, not overwrite each other."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 1}},
            {"phase": "result", "content": "ok"},
        ])
        write_run(tmp_path, "c", "m", 2, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 3}},
            {"phase": "result", "content": "ok"},
        ])
        runs = [run_dict(repeat=1), run_dict(repeat=2)]
        n, b = ambig_next.arm_rates(str(tmp_path), runs, "base")
        assert n == 2
        assert b["occurrence"] == 2


class TestScanOutcomes:
    """evals/nudge_next.py: scan() classification of what follows a nudge."""

    def test_nudge_followed_by_run_yields_tool_name(self, nudge_next, tmp_path):
        """A nudge then a run event yields TOOL:<name>."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "repeated call: x"},
            {"phase": "run", "name": "edit_file", "args": {}},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert out["TOOL:edit_file"] == 1

    def test_nudge_followed_by_stopped_yields_harness_stop(self, nudge_next, tmp_path):
        """A nudge then a stopped event yields HARNESS_STOP and records the reason."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "repeated call: x"},
            {"phase": "stopped", "reason": "cap hit"},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert out["HARNESS_STOP"] == 1
        assert reasons["cap hit"] == 1

    def test_nudge_followed_by_neither_yields_model_ended(self, nudge_next, tmp_path):
        """A nudge with no following run or stopped event yields MODEL_ENDED."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "repeated call: x"},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert out["MODEL_ENDED"] == 1

    def test_stopped_after_nudge_is_not_counted_as_model_ended(self, nudge_next, tmp_path):
        """CRITICAL: a nudge followed by a stopped event must NOT be counted as
        MODEL_ENDED. Merging HARNESS_STOP and MODEL_ENDED into one bucket is
        the exact defect §5.97/§5.100 describe as having produced a badly
        wrong earlier analysis (module docstring) -- keep them distinct."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "repeated call: x"},
            {"phase": "stopped", "reason": "cap hit"},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert out["HARNESS_STOP"] == 1
        assert out["MODEL_ENDED"] == 0

    def test_nudges_with_non_matching_prefix_are_skipped(self, nudge_next, tmp_path):
        """A nudge whose reason does not start with the given prefix is ignored."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "unverified edits: x"},
            {"phase": "run", "name": "edit_file", "args": {}},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert sum(out.values()) == 0

    def test_run_event_before_nudge_does_not_affect_outcome(self, nudge_next, tmp_path):
        """Only forward scanning counts -- a prior run event is irrelevant."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "bash", "args": {}},
            {"phase": "nudge", "reason": "repeated call: x"},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        assert out["MODEL_ENDED"] == 1
        assert "TOOL:bash" not in out


class TestScanGaps:
    """evals/nudge_next.py: scan() nudge-to-stop iteration gap measurement."""

    def test_gap_counts_iteration_events_with_intervening_tool_calls(
            self, nudge_next, tmp_path):
        """The gap is iterations from the run's FIRST matching nudge to its
        stop; intervening run events do not prevent it from being recorded.
        A narrower "stop must immediately follow" reading would undercount."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "nudge", "reason": "repeated call: x"},
            {"phase": "iteration"},
            {"phase": "run", "name": "bash", "args": {}},
            {"phase": "result", "content": "ok"},
            {"phase": "iteration"},
            {"phase": "run", "name": "bash", "args": {}},
            {"phase": "result", "content": "ok"},
            {"phase": "stopped", "reason": "cap hit"},
        ])
        out, reasons, gaps = nudge_next.scan(
            str(tmp_path), [run_dict(repeat=1)], "repeated call")
        # two "iteration" phase events occur between the nudge and the stop
        assert gaps == [2]


class TestRunsForDedup:
    """evals/ambig_next.py: runs_for() — the rule-66 double-count guard.

    ab.json holds ONE run entry per arm, but events() takes the arm as a
    parameter and ignores run['arm']. Iterating the unfiltered list therefore
    reads every event file twice. This inflated every absolute n in ROADMAP
    5.99-5.101 by exactly 2x. See 5.103.
    """

    def test_arm_entries_are_filtered_not_counted_twice(self, ambig_next,
                                                        tmp_path):
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 2}},
            {"phase": "result", "content": "ok"},
        ])
        # the shape ab.py actually writes: one entry per arm, same repeat
        runs = [dict(run_dict(repeat=1), arm="base"),
                dict(run_dict(repeat=1), arm="cand")]
        n, b = ambig_next.arm_rates(str(tmp_path), runs, "base")
        assert n == 1, "the base event file was counted once per arm entry"
        assert b["occurrence"] == 1

    def test_untagged_runs_still_work(self, ambig_next, tmp_path):
        """Sweeps predating the `arm` key must keep grading — that older shape
        is the one the unfiltered loop was correct for."""
        write_run(tmp_path, "c", "m", 1, "base", [
            {"phase": "run", "name": "edit_file", "args": {}},
            {"phase": "result", "content": ambig_next.AMBIG},
            {"phase": "run", "name": "edit_file", "args": {"occurrence": 2}},
            {"phase": "result", "content": "ok"},
        ])
        n, _ = ambig_next.arm_rates(str(tmp_path), [run_dict(repeat=1)], "base")
        assert n == 1

    def test_runs_for_selects_only_the_named_arm(self, ambig_next):
        runs = [dict(run_dict(repeat=1), arm="base"),
                dict(run_dict(repeat=1), arm="cand"),
                dict(run_dict(repeat=2), arm="base")]
        assert len(ambig_next.runs_for(runs, "base")) == 2
        assert len(ambig_next.runs_for(runs, "cand")) == 1
        assert ambig_next.runs_for([run_dict()], "base") == [run_dict()]
