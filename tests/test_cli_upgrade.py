"""`locode upgrade` subcommand dispatch + --check (no subprocess actually runs)."""

import locode.cli as cli
from locode import install
from locode.install import InstallMethod


def test_upgrade_no_marker_refuses(capsys, monkeypatch):
    monkeypatch.setattr(install, "read_method", lambda path=None: None)
    rc = cli.main(["upgrade", "--check"])
    assert rc == 1
    assert "install-method" in capsys.readouterr().err


def test_upgrade_check_git_shows_plan(capsys, monkeypatch):
    monkeypatch.setattr(install, "read_method",
                        lambda path=None: InstallMethod("git", "/x/locode"))
    rc = cli.main(["upgrade", "--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "git" in out and "/x/locode" in out and "would run" in out


def test_upgrade_check_pipx_pre(capsys, monkeypatch):
    monkeypatch.setattr(install, "read_method",
                        lambda path=None: InstallMethod("pipx", ""))
    rc = cli.main(["upgrade", "--check", "--pre"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pipx upgrade locode --pre" in out
