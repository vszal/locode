"""Tests for evals/metrics.py aggregation logic."""
import json
import importlib.util
import os
from pathlib import Path

import pytest


# Load metrics module by path, not sys.path hack
def _load_metrics():
    """Load metrics.py using importlib from the repo root."""
    repo_root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "metrics",
        repo_root / "evals" / "metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def metrics():
    """Load the metrics module."""
    return _load_metrics()


@pytest.fixture
def sweep_dir(tmp_path, monkeypatch, metrics):
    """Create a minimal test sweep with ab.json and events/.

    One case "demo-case" with 7 runs (base and cand arms):
    - base r1: fully_fixed=true, 1 edit_file with "not found in" result
    - base r2: fully_fixed=false, 2 edit_file calls, has stopped event
    - base r3: fully_fixed=false, 1 edit_file with occurrence=5, NO stopped (false completion)
    - base invalid: marked invalid (excluded)
    - cand r1: fully_fixed=true, 1 edit_file with replace_all=true, "not found in" result
    - cand r2: fully_fixed=true, 2 edit_file calls, one with occurrence=1
    - cand r3: fully_fixed=false, 1 replace_lines call, has stopped event

    Monkeypatches RESULTS to point to tmp_path.
    """
    sweep_root = tmp_path / "test-sweep"
    sweep_root.mkdir()
    events_dir = sweep_root / "events"
    events_dir.mkdir()

    # Monkeypatch RESULTS
    monkeypatch.setattr(metrics, "RESULTS", str(tmp_path))

    # Create ab.json
    ab_data = {
        "base_ref": "abc1234",
        "runs": [
            {"case": "demo-case", "arm": "base", "model": "m1", "repeat": 1,
             "checks": {"fully_fixed": True}},
            {"case": "demo-case", "arm": "base", "model": "m1", "repeat": 2,
             "checks": {"fully_fixed": False}},
            {"case": "demo-case", "arm": "base", "model": "m1", "repeat": 3,
             "checks": {"fully_fixed": False}},
            {"case": "demo-case", "arm": "base", "model": "m1", "repeat": 99,
             "checks": {"fully_fixed": True}, "invalid": True},
            {"case": "demo-case", "arm": "cand", "model": "m1", "repeat": 1,
             "checks": {"fully_fixed": True}},
            {"case": "demo-case", "arm": "cand", "model": "m1", "repeat": 2,
             "checks": {"fully_fixed": True}},
            {"case": "demo-case", "arm": "cand", "model": "m1", "repeat": 3,
             "checks": {"fully_fixed": False}},
        ]
    }
    (sweep_root / "ab.json").write_text(json.dumps(ab_data))

    # Helper to write events file
    def write_events(arm, repeat, events_list):
        fname = events_dir / f"demo-case__m1__r{repeat}__{arm}.jsonl"
        fname.write_text("\n".join(json.dumps(e) for e in events_list) + "\n")

    # base r1: fully_fixed=true
    # 1 edit_file call with "not found in" result
    write_events("base", 1, [
        {"phase": "run", "name": "edit_file", "args": {}},
        {"phase": "result", "content": "old not found in file"},
    ])

    # base r2: fully_fixed=false, has stopped
    # 2 edit_file calls: one with occurrence=0, one with replace_all=true (gets "ambiguity" result)
    write_events("base", 2, [
        {"phase": "run", "name": "edit_file", "args": {"occurrence": 0}},
        {"phase": "result", "content": "ok"},
        {"phase": "run", "name": "edit_file", "args": {"replace_all": True}},
        {"phase": "result", "content": "so it is not clear which"},
        {"phase": "stopped", "reason": "user stopped"},
    ])

    # base r3: fully_fixed=false, NO stopped event (false completion)
    # 1 edit_file call with occurrence=5
    write_events("base", 3, [
        {"phase": "run", "name": "edit_file", "args": {"occurrence": 5}},
        {"phase": "result", "content": "ok"},
    ])

    # cand r1: fully_fixed=true
    # 1 edit_file with replace_all=true, "not found in" result
    write_events("cand", 1, [
        {"phase": "run", "name": "edit_file", "args": {"replace_all": True}},
        {"phase": "result", "content": "old not found in file"},
    ])

    # cand r2: fully_fixed=true
    # 2 edit_file calls, one with occurrence=1
    write_events("cand", 2, [
        {"phase": "run", "name": "edit_file", "args": {"occurrence": 1}},
        {"phase": "result", "content": "ok"},
        {"phase": "run", "name": "edit_file", "args": {}},
        {"phase": "result", "content": "ok"},
    ])

    # cand r3: fully_fixed=false, has stopped
    # 1 replace_lines call (not edit_file)
    write_events("cand", 3, [
        {"phase": "run", "name": "replace_lines", "args": {}},
        {"phase": "result", "content": "ok"},
        {"phase": "stopped", "reason": "model stopped"},
    ])

    return sweep_root, metrics


class TestFullyFixedCounts:
    """Test that fully_fixed counts are correct for both arms."""

    def test_fully_fixed_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r1=true, r2=false, r3=false => fully_fixed=1
        assert base_metrics["per_run"]["fully_fixed"] == 1

    def test_fully_fixed_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: r1=true, r2=true, r3=false => fully_fixed=2
        assert cand_metrics["per_run"]["fully_fixed"] == 2


class TestInvalidRunsExcluded:
    """Test that invalid runs are excluded from all counts."""

    def test_invalid_excluded_from_runs_total(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base has 3 valid runs (r1, r2, r3) + 1 invalid (r99) => runs=3
        assert base_metrics["runs"] == 3

    def test_invalid_excluded_from_fully_fixed(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # Invalid run has fully_fixed=true but should not be counted
        assert base_metrics["per_run"]["fully_fixed"] == 1


class TestEditFileCalls:
    """Test edit_file_calls and edit-related argument counts."""

    def test_edit_file_calls_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r1=1 edit_file, r2=2 edit_file, r3=1 edit_file => 4 total
        assert base_metrics["per_call"]["edit_file_calls"] == 4

    def test_edit_file_calls_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: r1=1 edit_file, r2=2 edit_file, r3=0 edit_file => 3 total
        assert cand_metrics["per_call"]["edit_file_calls"] == 3

    def test_non_edit_file_tools_not_counted(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand r3 has replace_lines but that should NOT increment edit_file_calls
        assert cand_metrics["per_call"]["edit_file_calls"] == 3
        # But replace_lines should still appear in tool_calls
        assert cand_metrics["per_call"]["tool_calls"].get("replace_lines") == 1

    def test_edits_with_occurrence_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r1 occurrence not set, r2 occurrence=0 (not None, counts), r3 occurrence=5 => 2
        assert base_metrics["per_call"]["edits_with_occurrence"] == 2

    def test_edits_with_occurrence_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: r1 no occurrence, r2 occurrence=1 => 1
        assert cand_metrics["per_call"]["edits_with_occurrence"] == 1

    def test_edits_with_replace_all_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r1 no replace_all, r2 replace_all=true => 1
        assert base_metrics["per_call"]["edits_with_replace_all"] == 1

    def test_edits_with_replace_all_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: r1 replace_all=true, r2 no replace_all => 1
        assert cand_metrics["per_call"]["edits_with_replace_all"] == 1


class TestEditsErrorMessages:
    """Test that error messages in result events are counted correctly."""

    def test_edits_old_not_found_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r1 result has "not found in" => 1
        assert base_metrics["per_call"]["edits_old_not_found"] == 1

    def test_edits_old_not_found_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: r1 result has "not found in" => 1
        assert cand_metrics["per_call"]["edits_old_not_found"] == 1

    def test_edits_hit_ambiguity_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: r2 second edit_file result has "so it is not clear which" => 1
        assert base_metrics["per_call"]["edits_hit_ambiguity"] == 1

    def test_edits_hit_ambiguity_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: no ambiguity messages => 0
        assert cand_metrics["per_call"]["edits_hit_ambiguity"] == 0


class TestFalseCompletions:
    """Test false completion detection."""

    def test_false_completion_detected(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base r3: fully_fixed=false, no stopped event => 1 false completion
        assert base_metrics["per_run"]["false_completions"] == 1

    def test_stopped_event_prevents_false_completion(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base r2: fully_fixed=false BUT has stopped event => NOT a false completion
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand r3: fully_fixed=false BUT has stopped event => NOT a false completion
        # base and cand each have only 1 false completion (r3 for base)
        assert base_metrics["per_run"]["false_completions"] == 1
        assert cand_metrics["per_run"]["false_completions"] == 0


class TestMissingEventsFiles:
    """Test handling of missing events files."""

    def test_missing_events_increments_counter(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        # Add a run with no events file
        ab_data = json.loads((sweep_root / "ab.json").read_text())
        ab_data["runs"].append({
            "case": "demo-case",
            "arm": "base",
            "model": "m2",
            "repeat": 1,
            "checks": {"fully_fixed": True}
        })
        (sweep_root / "ab.json").write_text(json.dumps(ab_data))

        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # Now base has 4 valid runs (the new one has no events file)
        assert base_metrics["runs"] == 4
        assert base_metrics["runs_missing_events"] == 1

    def test_missing_events_does_not_crash(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        # Add a run with no events file
        ab_data = json.loads((sweep_root / "ab.json").read_text())
        ab_data["runs"].append({
            "case": "demo-case",
            "arm": "base",
            "model": "m3",
            "repeat": 1,
            "checks": {"fully_fixed": True}
        })
        (sweep_root / "ab.json").write_text(json.dumps(ab_data))

        # Should not raise an exception
        result = metrics.derive("test-sweep")
        assert result is not None


class TestToolCalls:
    """Test tool_calls aggregation."""

    def test_tool_calls_base(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        # base: 4 edit_file calls from r1, r2, r3
        assert base_metrics["per_call"]["tool_calls"].get("edit_file") == 4

    def test_tool_calls_cand(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        cand_metrics = result["cases"]["demo-case"]["cand"]
        # cand: 3 edit_file calls, 1 replace_lines
        assert cand_metrics["per_call"]["tool_calls"].get("edit_file") == 3
        assert cand_metrics["per_call"]["tool_calls"].get("replace_lines") == 1

    def test_tool_calls_is_dict(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        base_metrics = result["cases"]["demo-case"]["base"]
        assert isinstance(base_metrics["per_call"]["tool_calls"], dict)


class TestDeriveWithoutAbJson:
    """Test derive() behavior when ab.json is missing."""

    def test_derive_returns_none_when_no_ab_json(self, metrics, tmp_path, monkeypatch):
        monkeypatch.setattr(metrics, "RESULTS", str(tmp_path))
        # Create a sweep dir without ab.json
        (tmp_path / "no-ab").mkdir()
        result = metrics.derive("no-ab")
        assert result is None


class TestOutputSchema:
    """Test the structure of the returned dict."""

    def test_top_level_keys(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        assert "schema" in result
        assert "label" in result
        assert "base_ref" in result
        assert "cases" in result
        assert "READ_FIRST" in result

    def test_schema_value(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        assert result["schema"] == 1

    def test_label_value(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        assert result["label"] == "test-sweep"

    def test_base_ref_value(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        assert result["base_ref"] == "abc1234"

    def test_read_first_is_list(self, sweep_dir):
        sweep_root, metrics = sweep_dir
        result = metrics.derive("test-sweep")
        assert result is not None
        assert isinstance(result["READ_FIRST"], list)
        assert len(result["READ_FIRST"]) > 0


class TestServerIdentity:
    """Server identity is a first-class experimental variable (ROADMAP 5.94).

    A restart alone once moved VERIFIED from 0-7/14 to 4-11/14 with the code
    held constant (5.47), so rule 62 requires checking the pid/started pair
    before any cross-sweep comparison. If this field silently stopped being
    carried through, that check would pass vacuously.
    """

    @staticmethod
    def _sweep(tmp_path, monkeypatch, metrics, ab_extra):
        root = tmp_path / "s"
        (root / "events").mkdir(parents=True)
        monkeypatch.setattr(metrics, "RESULTS", str(tmp_path))
        ab = {"base_ref": "deadbee", "runs": [
            {"case": "c", "arm": "base", "model": "m", "repeat": 1,
             "checks": {"fully_fixed": True}},
            {"case": "c", "arm": "cand", "model": "m", "repeat": 1,
             "checks": {"fully_fixed": False}},
        ]}
        ab.update(ab_extra)
        (root / "ab.json").write_text(json.dumps(ab))
        return metrics.derive("s")

    def test_server_fields_are_carried_through(self, tmp_path, monkeypatch, metrics):
        out = self._sweep(tmp_path, monkeypatch, metrics, {"server": {
            "pid": "69851", "started": "Sat Aug 8 16:18:10 2026",
            "model": "some-model", "argv": ["ignored"]}})
        assert out["server"]["pid"] == "69851"
        assert out["server"]["started"] == "Sat Aug 8 16:18:10 2026"
        assert out["server"]["model"] == "some-model"
        # argv is deliberately dropped — it is long and not part of identity
        assert "argv" not in out["server"]

    def test_missing_server_block_does_not_crash(self, tmp_path, monkeypatch, metrics):
        out = self._sweep(tmp_path, monkeypatch, metrics, {})
        assert out["server"] == {"pid": None, "started": None, "model": None}

    def test_created_timestamp_is_carried_through(self, tmp_path, monkeypatch, metrics):
        out = self._sweep(tmp_path, monkeypatch, metrics,
                          {"created": "2026-08-11 21:06:49"})
        assert out["created"] == "2026-08-11 21:06:49"

    def test_read_first_warns_about_cross_sweep_server_check(self, tmp_path,
                                                             monkeypatch, metrics):
        out = self._sweep(tmp_path, monkeypatch, metrics, {})
        assert any("server pid" in n for n in out["READ_FIRST"])
