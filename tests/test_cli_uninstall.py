"""`locode uninstall` dispatch: refusal, confirmation/abort, run, and deletion."""

import builtins

import locode.cli as cli
from locode import install, uninstall
from locode.install import InstallMethod


def test_uninstall_no_marker_refuses(capsys, monkeypatch):
    monkeypatch.setattr(install, "read_method", lambda path=None: None)
    assert cli.main(["uninstall"]) == 1
    assert "install-method" in capsys.readouterr().err


def test_uninstall_abort_deletes_nothing(capsys, monkeypatch, tmp_path):
    victim = tmp_path / "venv"
    victim.mkdir()
    monkeypatch.setattr(install, "read_method",
                        lambda path=None: InstallMethod("venv", ""))
    monkeypatch.setattr(uninstall, "uninstall_plan",
                        lambda *a, **k: ([], [victim]))
    monkeypatch.setattr(builtins, "input", lambda *_: "n")
    assert cli.main(["uninstall"]) == 1
    assert "Aborted" in capsys.readouterr().err
    assert victim.exists()  # nothing removed


def test_uninstall_yes_runs_command(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "read_method",
                        lambda path=None: InstallMethod("pipx", ""))

    class _R:
        returncode = 0

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda c, *a, **k: calls.append(c) or _R())
    assert cli.main(["uninstall", "--yes"]) == 0
    assert calls == [["pipx", "uninstall", "locode"]]


def test_uninstall_yes_deletes_paths(monkeypatch, tmp_path):
    victim_dir = tmp_path / "venv"
    victim_dir.mkdir()
    victim_file = tmp_path / "shim"
    victim_file.write_text("x")
    monkeypatch.setattr(install, "read_method",
                        lambda path=None: InstallMethod("venv", ""))
    monkeypatch.setattr(uninstall, "uninstall_plan",
                        lambda *a, **k: ([], [victim_dir, victim_file]))
    assert cli.main(["uninstall", "--yes"]) == 0
    assert not victim_dir.exists()
    assert not victim_file.exists()
