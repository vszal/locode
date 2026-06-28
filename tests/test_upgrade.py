import pytest

from locode.upgrade import upgrade_argv


def test_pipx():
    cmd = upgrade_argv("pipx")
    assert len(cmd) == 1
    assert cmd[0][0] == "pipx"
    assert cmd[0][-1] == "locode"


def test_uv():
    cmd = upgrade_argv("uv")
    assert cmd == [["uv", "tool", "upgrade", "locode"]]


def test_venv():
    cmd = upgrade_argv("venv")
    assert len(cmd) == 1
    assert cmd[0][0].endswith("bin/pip")
    assert "install" in cmd[0]
    assert "-U" in cmd[0]
    assert "locode" in cmd[0]


def test_git():
    detail = "/path/to/repo"
    cmd = upgrade_argv("git", detail)
    assert len(cmd) == 2
    assert cmd[0] == ["git", "-C", detail, "pull"]
    assert "-e" in cmd[1]
    assert detail in cmd[1]


def test_unknown_method():
    with pytest.raises(ValueError):
        upgrade_argv("unknown")


def test_pre():
    cmd = upgrade_argv("pipx", pre=True)
    assert cmd[0][-1] == "--pre"
