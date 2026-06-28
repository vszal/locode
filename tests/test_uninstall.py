import pytest

from locode.config import CONFIG_DIR, DATA_DIR, STATE_DIR
from locode.uninstall import uninstall_plan


def test_pipx():
    commands, paths = uninstall_plan("pipx")
    assert commands == [["pipx", "uninstall", "locode"]]
    assert paths == []


def test_uv():
    commands, paths = uninstall_plan("uv")
    assert commands == [["uv", "tool", "uninstall", "locode"]]
    assert paths == []


def test_venv():
    commands, paths = uninstall_plan("venv")
    assert commands == []
    assert len(paths) == 2
    assert str(paths[1]).endswith(".local/bin/locode")


def test_git():
    commands, paths = uninstall_plan("git", "/x/locode")
    assert len(commands) == 1
    assert "uninstall" in commands[0] and "-y" in commands[0] and "locode" in commands[0]
    assert paths == []


def test_unknown_method():
    with pytest.raises(ValueError):
        uninstall_plan("frobnicate")


def test_purge_appends_dirs():
    _, paths = uninstall_plan("pipx", purge=True)
    assert paths == [CONFIG_DIR, STATE_DIR, DATA_DIR]
