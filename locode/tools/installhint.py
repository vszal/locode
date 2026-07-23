"""Turn a failed dependency install into a next action the model can take.

A local model that decides it needs a package reaches for the command it has
seen a million times in training — `npm install -g puppeteer-extra`, `pip
install requests` — and when that fails it has no idea why, so it runs it
again. Three identical failures later the stuck-detectors end the turn, and the
transcript blames "the model repeated the same tool call" for what is really a
missing sentence: *nobody told it the local path exists*.

So when a bash command fails in a way that means "the dependency did not get
installed", we append a concrete, rewritten command to the tool result. The
policy is local-first:

- Python -> the project venv (`.venv/bin/pip`), created if it isn't there.
- Node   -> a project install, the `-g` dropped.
- Genuinely global tooling that we cannot bootstrap (npm, brew, a language
  runtime) -> stop and use `ask_user`, because installing Homebrew is the
  user's decision and not something an agent should do behind their back.
- No network -> stop; retrying a DNS failure never worked yet.

Everything here is a pure function of (command, output, exit code, cwd) so it
can be tested without running anything. Returning None — "no hint, this failure
is the model's ordinary business" — is the common case and the safe default.
"""

from __future__ import annotations

import re
from pathlib import Path

# Split a compound command so we hint about the part that actually installs,
# not the `cd` in front of it.
_SEGMENT_RE = re.compile(r"&&|\|\||;|\n")

# `python -m pip install x`, `sudo pip3 install x`, `uv pip install x`.
_PIP_RE = re.compile(
    r"\b(?:uv\s+)?(?:(?:python3?|py)\s+-m\s+)?pip3?\s+install\b(?P<rest>.*)",
    re.I)
# `npm i -g x`, `yarn global add x`, `pnpm add -g x`.
_NODE_RE = re.compile(
    r"\b(?P<pm>npm|pnpm|yarn)\s+(?:global\s+)?(?:install|add|i)\b(?P<rest>.*)",
    re.I)
# Package managers whose reach is the whole machine — we never rewrite these,
# we hand them to the user.
_SYSTEM_INSTALL_RE = re.compile(
    r"\b(?:brew|apt-get|apt|dnf|yum|pacman|port|gem|cargo\s+install|"
    r"go\s+install|pipx)\b", re.I)

# Exact tokens meaning "install this globally", dropped when we rewrite.
_GLOBAL_FLAGS = {"-g", "--global", "--location=global"}

# Tools locode cannot conjure: if these are missing, a human installs them.
_BOOTSTRAP_TOOLS = {
    "npm", "npx", "node", "yarn", "pnpm", "brew", "cargo", "rustc", "rustup",
    "go", "docker", "java", "javac", "mvn", "gradle", "ruby", "gem", "php",
    "composer", "dotnet", "swift", "make", "cmake", "gcc", "g++", "git",
}

_NETWORK_MARKERS = (
    "getaddrinfo", "enotfound", "eai_again", "could not resolve host",
    "temporary failure in name resolution", "network is unreachable",
    "etimedout", "econnreset", "econnrefused", "connection refused",
    "ssl certificate problem", "certificate verify failed",
    "proxy connect", "network error",
)
_DENIED_MARKERS = (
    "eacces", "eperm", "permission denied", "operation not permitted",
    "read-only file system", "erofs", "sudo:", "must be run as root",
    "are you root",
)
_PEP668 = "externally-managed-environment"

# `zsh: command not found: npm` / `/bin/sh: npm: command not found`
# / `sh: 1: npm: not found`
_NOTFOUND_RES = (
    re.compile(r"command not found:\s*(?P<name>[\w.+-]+)"),
    re.compile(r"(?:^|[:\s])(?P<name>[\w.+-]+):\s*(?:command\s+)?not found"),
)


def install_hint(cmd: str, output: str, exit_code: int, cwd: str) -> str | None:
    """A sentence of steering to append to a failed bash result, or None.

    None means "this failure needs no special handling" — the overwhelming
    majority of failures, including every ordinary test or compile error.
    """
    low = output.lower()

    missing = _missing_executable(cmd, low, exit_code)
    if missing:
        return _missing_hint(missing, cmd, cwd)

    ecosystem = _install_kind(cmd)
    if ecosystem is None:
        return None

    if any(m in low for m in _NETWORK_MARKERS):
        return ("This machine could not reach the network, so the install "
                "cannot succeed no matter how it is spelled. Do NOT run it "
                "again. Either continue with what is already available, or "
                "call ask_user to ask how to proceed.")

    blocked = _PEP668 in low or any(m in low for m in _DENIED_MARKERS)
    # An install can also fail for reasons that have nothing to do with where
    # it was going — a typo'd package name, a version conflict, a failing
    # build script. Steering those to a venv would be confident nonsense, so
    # unless the environment is visibly what blocked us, say nothing and let
    # the model read its own error. The one exception is an explicit global
    # Node install: the project-local form is better advice however it failed.
    explicit_global = ecosystem == "node" and _has_global_flag(cmd)
    if not blocked and not explicit_global:
        return None

    if ecosystem == "python":
        # PEP 668 and EACCES both mean the same thing here: the interpreter
        # this command reached is not ours to write to. A venv is.
        return _python_hint(cmd, cwd)
    if ecosystem == "node":
        return _node_hint(cmd, blocked)
    # A system package manager. There is no project-local form of `brew
    # install`, so this is where we stop and ask.
    return ("locode cannot install system-wide packages. If this dependency "
            "is genuinely required, call ask_user to ask the user to install "
            "it themselves. Otherwise continue without it. Do NOT run the "
            "install again.")


def _missing_executable(cmd: str, low_output: str, exit_code: int) -> str | None:
    """The name of the executable the shell could not find, if that's what
    happened. Exit 127 is the shell's own code for it; the text match catches
    the case where a wrapper script swallowed the code."""
    if exit_code != 127 and "not found" not in low_output:
        return None
    for rx in _NOTFOUND_RES:
        m = rx.search(low_output)
        if m:
            name = m.group("name")
            # `npm ERR! 404 Not Found - GET ...` is a missing *package*, not a
            # missing binary; those names come back with junk around them.
            if name not in {"error", "err", "npm", "e"} or exit_code == 127:
                return name
    if exit_code == 127:
        first = cmd.strip().split()
        if first:
            return first[0].lstrip("./")
    return None


def _missing_hint(name: str, cmd: str, cwd: str) -> str:
    if name in {"pip", "pip3"}:
        return ("There is no bare `pip` on the PATH. " + _python_hint(cmd, cwd))
    if name in _BOOTSTRAP_TOOLS:
        return (f"`{name}` is not installed on this machine, and locode cannot "
                f"install it — that is the user's call. Call ask_user to ask "
                f"whether to install {name}, and do NOT run this command "
                f"again in the meantime. If the task can be done without "
                f"{name}, say so and do that instead.")
    return (f"`{name}` is not on the PATH. Check the spelling, or look for it "
            f"in the project (node_modules/.bin, .venv/bin) before assuming "
            f"it is installed system-wide. Do not run the same command again "
            f"unchanged.")


def _install_kind(cmd: str) -> str | None:
    """Which ecosystem this command was trying to install into, if any."""
    for seg in _segments(cmd):
        if _PIP_RE.search(seg):
            return "python"
        if _NODE_RE.search(seg):
            return "node"
        if _SYSTEM_INSTALL_RE.search(seg):
            return "system"
    return None


def _segments(cmd: str) -> list[str]:
    return [s.strip() for s in _SEGMENT_RE.split(cmd) if s.strip()]


def _python_hint(cmd: str, cwd: str) -> str:
    pkgs = ""
    for seg in _segments(cmd):
        m = _PIP_RE.search(seg)
        if m:
            pkgs = _strip_global(m.group("rest"))
            break
    tail = f" install {pkgs}" if pkgs else " install <packages>"
    if (Path(cwd) / ".venv" / "bin" / "pip").exists():
        return ("Install into the project's virtualenv instead of the system "
                f"interpreter. Run: .venv/bin/pip{tail}")
    return ("Install into a project virtualenv instead of the system "
            "interpreter. Run: python3 -m venv .venv && .venv/bin/pip"
            f"{tail}  — then use .venv/bin/python to run the code.")


def _node_hint(cmd: str, blocked: bool) -> str:
    pm, pkgs = "npm", ""
    for seg in _segments(cmd):
        m = _NODE_RE.search(seg)
        if m:
            pm = m.group("pm").lower()
            pkgs = _strip_global(m.group("rest"))
            break
    verb = "add" if pm in {"yarn", "pnpm"} else "install"
    lead = ("A global install is not permitted here. " if blocked
            else "That install did not take effect globally. ")
    tail = f" {pkgs}" if pkgs else ""
    return (lead + "Install into the project instead — run: "
            f"{pm} {verb}{tail}  (from the working directory, no -g), then "
            "run the binary as ./node_modules/.bin/<name> or via npx.")


def _has_global_flag(cmd: str) -> bool:
    return any(t in _GLOBAL_FLAGS for t in cmd.split()) or bool(
        re.search(r"\byarn\s+global\b", cmd, re.I))


def _strip_global(rest: str) -> str:
    return " ".join(t for t in rest.split() if t not in _GLOBAL_FLAGS)
