import pytest
from locode.install import (VALID_METHODS, InstallMethod, write_method, read_method)


def test_pipx_roundtrip(tmp_path):
    path = tmp_path / "install-method"
    write_method("pipx", path=path)
    result = read_method(path=path)
    assert result == InstallMethod("pipx", "")


def test_uv_roundtrip(tmp_path):
    path = tmp_path / "install-method"
    write_method("uv", path=path)
    result = read_method(path=path)
    assert result == InstallMethod("uv", "")


def test_venv_roundtrip(tmp_path):
    path = tmp_path / "install-method"
    write_method("venv", path=path)
    result = read_method(path=path)
    assert result == InstallMethod("venv", "")


def test_git_roundtrip(tmp_path):
    path = tmp_path / "install-method"
    checkout_path = "/home/user/locode-checkout"
    write_method("git", detail=checkout_path, path=path)
    result = read_method(path=path)
    assert result == InstallMethod("git", checkout_path)


def test_read_missing_file(tmp_path):
    path = tmp_path / "install-method"
    assert read_method(path=path) is None


def test_read_empty_file(tmp_path):
    path = tmp_path / "install-method"
    path.write_text("")
    assert read_method(path=path) is None


def test_read_invalid_method(tmp_path):
    path = tmp_path / "install-method"
    path.write_text("unknown\tfoo\n")
    assert read_method(path=path) is None


def test_write_unknown_method_raises(tmp_path):
    path = tmp_path / "install-method"
    with pytest.raises(ValueError):
        write_method("unknown", path=path)
