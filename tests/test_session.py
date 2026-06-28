import pytest

from locode.session import (
    Session, list_sessions, load_session, safe_name, save_session, session_path,
)


def _sess(name="work", saved_at="2026-06-28T10:00:00", history=None):
    return Session(name=name, model="qythos9", cwd="/x",
                   saved_at=saved_at,
                   history=history or [{"role": "system", "content": "hi"}])


def test_save_load_roundtrip(tmp_path):
    s = _sess(history=[{"role": "system", "content": "sys"},
                       {"role": "user", "content": "hello"}])
    save_session(s, base=tmp_path)
    loaded = load_session("work", base=tmp_path)
    assert loaded == s


def test_safe_name_strips_dangerous_chars():
    out = safe_name("../../etc/passwd")
    assert "/" not in out and "." not in out
    assert safe_name("My Work!") == "my-work"


def test_safe_name_empty_and_symbols_default_to_session():
    assert safe_name("") == "session"
    assert safe_name("!!!///") == "session"


def test_path_traversal_confined(tmp_path):
    # A malicious name must resolve to a file *inside* tmp_path, never above it.
    p = session_path("../../../etc/passwd", base=tmp_path)
    assert p.parent == tmp_path
    assert tmp_path in p.resolve().parents or p.resolve().parent == tmp_path


def test_load_missing_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_session("never-saved", base=tmp_path)


def test_load_corrupt_raises_valueerror(tmp_path):
    p = session_path("broken", base=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")
    with pytest.raises(ValueError):
        load_session("broken", base=tmp_path)


def test_load_missing_fields_raises_valueerror(tmp_path):
    p = session_path("partial", base=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"name": "partial", "model": "x"}')  # missing cwd/saved_at/history
    with pytest.raises(ValueError):
        load_session("partial", base=tmp_path)


def test_list_sessions_newest_first(tmp_path):
    save_session(_sess(name="older", saved_at="2026-06-01T09:00:00"), base=tmp_path)
    save_session(_sess(name="newer", saved_at="2026-06-28T09:00:00"), base=tmp_path)
    names = [s.name for s in list_sessions(base=tmp_path)]
    assert names == ["newer", "older"]


def test_list_sessions_empty_when_no_dir(tmp_path):
    assert list_sessions(base=tmp_path / "nope") == []
