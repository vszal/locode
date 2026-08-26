"""The one empty result that means "wrong command", not "nothing matched".

`git ls-tree <ref> <URL>` reads the local object database and takes the URL as
a pathspec: exit 0, no output, byte-identical to a true empty answer. Every
behaviour asserted below about real git was measured (2026-08-26), not assumed
-- see the module docstring of locode/tools/gitremote.py for the transcript.
"""

import pytest

from locode.tools.gitremote import hint

URL = "file:///tmp/upstream.git"


@pytest.mark.parametrize("cmd", [
    f"git ls-tree -r --name-only main {URL} handlers/",
    f"git ls-tree main {URL}",
    "git ls-tree -r --name-only main https://github.com/google/skills.git skills/cloud",
    f"git ls-files {URL}",
    f"git ls-files -- {URL}",
    f"git log --oneline main -- {URL}",
    f"git diff main -- {URL}",
    "git ls-tree HEAD git@github.com:owner/repo.git",
    f"git -C sub ls-tree HEAD {URL}",          # global opt taking a value
    f"git -c core.pager=cat ls-tree main {URL}",
    f"cd repo && git ls-tree main {URL} | head -30",  # inside a compound
    f"/usr/bin/git ls-tree main {URL}",        # absolute path to git
])
def test_detects_url_in_pathspec_position(cmd):
    msg = hint(cmd)
    assert msg is not None, cmd
    # Rule 9: the call that works comes first, before any explanation.
    assert msg.index("git clone") < msg.index("Why this call printed nothing")
    assert "cannot take a URL" in msg


@pytest.mark.parametrize("cmd", [
    # A genuine empty query. This is the case _EMPTY_OK exists for.
    "git ls-tree -r --name-only main handlers/",
    "grep -rn nothing-here .",
    # Commands that DO take a URL and are the right answer, not the bug.
    f"git clone {URL} upstream",
    f"git ls-remote {URL}",
    f"git fetch {URL} main",
    f"git archive --remote={URL} main",
    # git is not the program being run.
    f"echo {URL}",
    f"grep -r {URL} .",
    # A URL-shaped string that is not in a git command at all.
    f"curl -s {URL}",
])
def test_leaves_ordinary_empty_results_alone(cmd):
    assert hint(cmd) is None


def test_names_the_subcommand_and_the_url():
    msg = hint(f"git ls-tree -r --name-only main {URL} handlers/")
    assert URL in msg and "git ls-tree" in msg


def test_survives_unparseable_quoting():
    # shlex raises on an unbalanced quote; a tool result must never be the thing
    # that takes the turn down.
    assert hint(f"git ls-tree main '{URL}") is None


def test_empty_and_none_commands():
    assert hint("") is None
    assert hint(None) is None


def test_bare_git_and_git_with_only_options():
    assert hint("git") is None
    assert hint("git -C sub") is None
