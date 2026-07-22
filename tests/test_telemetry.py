import json

from locode.telemetry import MAX_FIELD_CHARS, EventLog, tee


def _read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_emits_jsonl_with_seq_and_timestamp(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "run", "name": "read_file"})
    log.emit({"phase": "result", "name": "read_file", "error": False})
    log.close()

    recs = _read(p)
    assert [r["seq"] for r in recs] == [1, 2]
    assert all(isinstance(r["t"], (int, float)) for r in recs)
    assert recs[0]["phase"] == "run"
    assert recs[1]["error"] is False


def test_creates_parent_directory(tmp_path):
    p = tmp_path / "nested" / "deeper" / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "turn_start"})
    log.close()
    assert _read(p)[0]["phase"] == "turn_start"


def test_long_fields_are_clipped(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "result", "content": "x" * (MAX_FIELD_CHARS + 500)})
    log.close()

    content = _read(p)[0]["content"]
    assert len(content) < MAX_FIELD_CHARS + 100
    assert "clipped 500 chars" in content


def test_nested_fields_are_clipped(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "run", "args": {"path": "a.py", "new": "y" * (MAX_FIELD_CHARS + 10)}})
    log.close()

    args = _read(p)[0]["args"]
    assert args["path"] == "a.py"
    assert "clipped" in args["new"]


def test_non_serializable_values_do_not_raise(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "run", "args": {"obj": object()}})
    log.close()
    assert _read(p)[0]["phase"] == "run"


def test_unwritable_path_degrades_to_noop(tmp_path):
    # A directory can't be opened for append -> disabled, but emit() must not raise.
    d = tmp_path / "adir"
    d.mkdir()
    log = EventLog(str(d))
    assert not log.enabled
    log.emit({"phase": "run"})  # no exception
    log.close()


def test_mark_turn_start_rebases_timestamps(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    log.emit({"phase": "a"})
    log.mark_turn_start()
    log.emit({"phase": "b"})
    log.close()

    recs = _read(p)
    assert recs[1]["t"] <= recs[0]["t"] + 0.01
    assert recs[1]["seq"] == 2  # sequence keeps counting across turns


def test_tee_feeds_both_sinks(tmp_path):
    p = tmp_path / "events.jsonl"
    log = EventLog(str(p))
    seen = []
    cb = tee(log, seen.append)
    cb({"phase": "nudge", "reason": "empty response"})
    log.close()

    assert seen == [{"phase": "nudge", "reason": "empty response"}]
    assert _read(p)[0]["reason"] == "empty response"


def test_tee_tolerates_missing_sides():
    seen = []
    assert tee(None, seen.append)({"phase": "x"}) is None or True
    assert seen == [{"phase": "x"}]
    tee(None, None)({"phase": "x"})  # no exception
