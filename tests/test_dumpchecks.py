"""Tests for evals/dumpchecks.py -- pure extraction, no grading."""
import csv
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import dumpchecks  # noqa: E402


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_list_shaped_results(tmp_path):
    sweep = tmp_path / "sweep-a"
    _write(sweep / "results.json", [
        {
            "case": "c1", "model": "m1", "track": "execute", "repeat": 1,
            "score": 1.0, "checks": {"chk_a": True, "chk_b": False},
            "metrics": {"iterations": 3, "wallclock": 12.5, "nudges": 0,
                        "stop_reason": None, "clean_finish": True},
        },
    ])
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["sweeps_read"] == 1
    assert stats["sweeps_unparsed"] == 0
    assert stats["runs_seen"] == 1
    assert stats["runs_no_checks"] == 0
    assert len(rows) == 2
    checks = {r["check"]: r["value"] for r in rows}
    assert checks == {"chk_a": "true", "chk_b": "false"}
    assert rows[0]["sweep"] == "sweep-a"
    assert rows[0]["label"] == ""
    assert rows[0]["case"] == "c1"
    assert rows[0]["iterations"] == 3


def test_dict_with_runs_shape(tmp_path):
    sweep = tmp_path / "sweep-b"
    _write(sweep / "results.json", {
        "label": "my-label",
        "server": "qwen38",
        "runs": [
            {"case": "c1", "model": "m1", "checks": {"only_check": True}},
        ],
    })
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["sweeps_read"] == 1
    assert len(rows) == 1
    assert rows[0]["label"] == "my-label"
    assert rows[0]["check"] == "only_check"
    assert rows[0]["value"] == "true"


def test_run_missing_checks_is_skipped_and_counted(tmp_path):
    sweep = tmp_path / "sweep-c"
    _write(sweep / "results.json", [
        {"case": "c1", "model": "m1", "checks": {"a": True}},
        {"case": "c2", "model": "m1"},  # no "checks" key at all
        {"case": "c3", "model": "m1", "checks": None},  # explicit null
        {"case": "c4", "model": "m1", "checks": {}},  # empty dict
    ])
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["runs_seen"] == 4
    assert stats["runs_no_checks"] == 3
    assert len(rows) == 1
    assert rows[0]["case"] == "c1"


def test_missing_metrics_render_empty_and_do_not_crash(tmp_path):
    sweep = tmp_path / "sweep-d"
    _write(sweep / "results.json", [
        {"case": "c1", "checks": {"a": True}},  # no model, track, metrics...
    ])
    rows, stats = dumpchecks.dump(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == ""
    assert row["track"] == ""
    assert row["repeat"] == ""
    assert row["arm"] == ""
    assert row["score"] == ""
    assert row["iterations"] == ""
    assert row["wallclock"] == ""
    assert row["nudges"] == ""
    assert row["stop_reason"] == ""
    assert row["clean_finish"] == ""


def test_one_row_per_check(tmp_path):
    sweep = tmp_path / "sweep-e"
    _write(sweep / "results.json", [
        {"case": "c1", "model": "m1",
         "checks": {"a": True, "b": False, "c": True, "d": False, "e": True}},
    ])
    rows, stats = dumpchecks.dump(tmp_path)
    assert len(rows) == 5
    assert stats["rows_written"] == 5


def test_case_filter(tmp_path):
    sweep = tmp_path / "sweep-f"
    _write(sweep / "results.json", [
        {"case": "wanted", "model": "m1", "checks": {"a": True}},
        {"case": "unwanted", "model": "m1", "checks": {"b": True}},
    ])
    rows, stats = dumpchecks.dump(tmp_path, cases=["wanted"])
    assert len(rows) == 1
    assert rows[0]["case"] == "wanted"
    # runs_seen still counts both runs; only row filtering excludes the other
    assert stats["runs_seen"] == 2


def test_model_filter(tmp_path):
    sweep = tmp_path / "sweep-g"
    _write(sweep / "results.json", [
        {"case": "c1", "model": "wanted-model", "checks": {"a": True}},
        {"case": "c1", "model": "other-model", "checks": {"a": True}},
    ])
    rows, stats = dumpchecks.dump(tmp_path, models=["wanted-model"])
    assert len(rows) == 1
    assert rows[0]["model"] == "wanted-model"


def test_invalid_json_counted_as_unparsed(tmp_path):
    sweep = tmp_path / "sweep-h"
    (sweep).mkdir(parents=True, exist_ok=True)
    (sweep / "results.json").write_text("{not valid json")
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["sweeps_unparsed"] == 1
    assert stats["sweeps_read"] == 0
    assert len(rows) == 0


def test_dict_without_runs_key_falls_back_to_list_of_dicts_with_case(tmp_path):
    sweep = tmp_path / "sweep-i"
    _write(sweep / "results.json", {
        "meta": "irrelevant",
        "results": [
            {"case": "c1", "model": "m1", "checks": {"a": True}},
        ],
    })
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["sweeps_read"] == 1
    assert len(rows) == 1
    assert rows[0]["case"] == "c1"


def test_dict_without_any_usable_list_is_unparsed(tmp_path):
    sweep = tmp_path / "sweep-j"
    _write(sweep / "results.json", {"meta": "irrelevant", "count": 5})
    rows, stats = dumpchecks.dump(tmp_path)
    assert stats["sweeps_unparsed"] == 1
    assert len(rows) == 0


def test_csv_output_columns_and_order(tmp_path):
    sweep = tmp_path / "sweep-k"
    _write(sweep / "results.json", [
        {"case": "c1", "model": "m1", "track": "execute", "repeat": 2,
         "score": 0.5, "arm": "A", "checks": {"z": True},
         "metrics": {"iterations": 4, "wallclock": 9.1, "nudges": 1,
                     "stop_reason": "done", "clean_finish": False}},
    ])
    rows, stats = dumpchecks.dump(tmp_path)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=dumpchecks.COLUMNS)
    w.writeheader()
    for row in rows:
        w.writerow(row)
    buf.seek(0)
    reader = csv.reader(buf)
    header = next(reader)
    assert header == [
        "sweep", "label", "case", "model", "track", "repeat", "arm", "score",
        "check", "value", "iterations", "wallclock", "nudges", "stop_reason",
        "clean_finish",
    ]
    data_row = next(reader)
    assert data_row == [
        "sweep-k", "", "c1", "m1", "execute", "2", "A", "0.5",
        "z", "true", "4", "9.1", "1", "done", "false",
    ]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
