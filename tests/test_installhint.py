"""The steering appended to a failed dependency install.

Two things matter about this module and both are tested here: that it fires on
the failures the user actually hit, and — more importantly — that it stays
quiet on ordinary command failures, where a confident wrong suggestion is worse
than none.
"""

import pytest

from locode.tools.installhint import install_hint


# --- Node: the reported case ---------------------------------------------

EACCES = """npm ERR! code EACCES
npm ERR! syscall mkdir
npm ERR! path /usr/local/lib/node_modules/puppeteer-extra
npm ERR! errno -13
npm ERR! Error: EACCES: permission denied, mkdir '/usr/local/lib/node_modules'
"""


def test_global_npm_install_denied_is_rewritten_locally(tmp_path):
    h = install_hint(
        "npm install -g puppeteer-extra puppeteer-extra-plugin-stealth",
        EACCES, 243, str(tmp_path))
    assert h is not None
    assert "npm install puppeteer-extra puppeteer-extra-plugin-stealth" in h
    assert "-g" not in h.split("run:")[1].split("(from")[0]
    assert "node_modules/.bin" in h


def test_yarn_and_pnpm_get_their_own_verb(tmp_path):
    h = install_hint("yarn global add typescript", EACCES, 1, str(tmp_path))
    assert "yarn add typescript" in h
    h = install_hint("pnpm add -g typescript", EACCES, 1, str(tmp_path))
    assert "pnpm add typescript" in h


def test_explicit_global_install_is_steered_even_without_a_known_marker(tmp_path):
    # Whatever went wrong, `npm install -g` was the wrong shape here.
    h = install_hint("npm i -g cowsay", "npm ERR! something odd", 1,
                     str(tmp_path))
    assert h is not None and "npm install cowsay" in h


def test_a_local_npm_install_that_fails_normally_gets_no_hint(tmp_path):
    # No -g, no permission problem: this is a real package error and the model
    # should read it, not be told to drop a flag it never passed.
    h = install_hint("npm install lodash",
                     "npm ERR! 404 Not Found - GET .../lodashh", 1,
                     str(tmp_path))
    assert h is None


# --- Python: venv steering ------------------------------------------------

PEP668 = """error: externally-managed-environment

× This environment is externally managed
"""


def test_pep668_steers_to_a_new_venv_when_there_is_none(tmp_path):
    h = install_hint("pip install requests", PEP668, 1, str(tmp_path))
    assert "python3 -m venv .venv" in h
    assert ".venv/bin/pip install requests" in h


def test_existing_venv_is_used_instead_of_being_recreated(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "pip").write_text("#!/bin/sh\n")
    h = install_hint("pip3 install requests httpx", PEP668, 1, str(tmp_path))
    assert ".venv/bin/pip install requests httpx" in h
    assert "venv .venv" not in h


def test_python_m_pip_form_is_recognized(tmp_path):
    h = install_hint("sudo python3 -m pip install numpy",
                     "ERROR: Could not install packages... Permission denied",
                     1, str(tmp_path))
    assert ".venv/bin/pip install numpy" in h


def test_a_pip_install_that_fails_on_the_package_gets_no_hint(tmp_path):
    h = install_hint(
        "pip install reqeusts",
        "ERROR: Could not find a version that satisfies the requirement", 1,
        str(tmp_path))
    assert h is None


# --- System package managers: hand it to the user -------------------------

def test_brew_install_is_escalated_not_rewritten(tmp_path):
    h = install_hint("brew install ffmpeg",
                     "Error: Permission denied @ dir_s_mkdir", 1,
                     str(tmp_path))
    assert "ask_user" in h
    assert "cannot install system-wide" in h


def test_apt_get_is_escalated(tmp_path):
    h = install_hint("sudo apt-get install -y ffmpeg",
                     "E: Could not open lock file - open (13: Permission denied)",
                     100, str(tmp_path))
    assert "ask_user" in h


# --- Missing executables --------------------------------------------------

@pytest.mark.parametrize("out", [
    "/bin/sh: npm: command not found",
    "zsh: command not found: npm",
    "sh: 1: npm: not found",
])
def test_missing_bootstrap_tool_asks_the_user(out, tmp_path):
    h = install_hint("npm install express", out, 127, str(tmp_path))
    assert "ask_user" in h
    assert "npm" in h
    assert "not run this command again" in h.lower()


def test_missing_pip_is_answered_with_a_venv_not_a_question(tmp_path):
    h = install_hint("pip install requests",
                     "/bin/sh: pip: command not found", 127, str(tmp_path))
    assert "python3 -m venv .venv" in h
    assert "ask_user" not in h


def test_missing_project_binary_points_at_the_project(tmp_path):
    h = install_hint("eslint src/", "/bin/sh: eslint: command not found", 127,
                     str(tmp_path))
    assert "node_modules/.bin" in h
    assert "ask_user" not in h


def test_a_404_from_npm_is_not_read_as_a_missing_binary(tmp_path):
    # "404 Not Found" contains "not found" but the shell ran npm just fine.
    h = install_hint("npm install nosuchpkg",
                     "npm ERR! 404 Not Found - GET https://registry/nosuchpkg",
                     1, str(tmp_path))
    assert h is None


# --- Network --------------------------------------------------------------

@pytest.mark.parametrize("out", [
    "npm ERR! code ENOTFOUND\nnpm ERR! getaddrinfo ENOTFOUND registry.npmjs.org",
    "Could not resolve host: pypi.org",
    "WARNING: Retrying ... Temporary failure in name resolution",
])
def test_network_failure_stops_rather_than_steering(out, tmp_path):
    h = install_hint("npm install -g express", out, 1, str(tmp_path))
    assert "network" in h.lower()
    assert "Do NOT run it again" in h
    # The point is to stop, so it must not also hand over a command to retry.
    assert "npm install express" not in h


# --- Silence is the default ------------------------------------------------

@pytest.mark.parametrize("cmd,out,rc", [
    ("pytest -q", "1 failed, 3 passed", 1),
    ("ls nope", "ls: nope: No such file or directory", 1),
    ("python app.py", "Traceback...\nPermissionError: [Errno 13] denied", 1),
    ("git push", "error: failed to push some refs", 1),
])
def test_ordinary_failures_get_no_hint(cmd, out, rc, tmp_path):
    assert install_hint(cmd, out, rc, str(tmp_path)) is None


def test_a_succeeding_command_is_never_asked_about(tmp_path):
    # The tool only consults us on rc != 0; this documents that contract by
    # showing a "success" call still produces nothing surprising.
    assert install_hint("npm install lodash", "added 1 package", 0,
                        str(tmp_path)) is None
