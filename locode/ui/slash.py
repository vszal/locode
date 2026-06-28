"""Slash-command parsing + help. The REPL owns the handlers (they touch session
state); this module keeps the parsing and the command catalog pure/testable.
"""

from __future__ import annotations

COMMANDS: dict[str, str] = {
    "model": "Show or switch the served model: /model [alias]",
    "models": "List served + known model aliases",
    "server": "Manage the backend: /server [status|restart|stop]",
    "open": "Open a file in your editor: /open <path>",
    "diff": "Show a file's working diff: /diff [path]",
    "retry": "Re-run your last prompt (or /retry <new text>)",
    "clear": "Reset the conversation context (keep the session)",
    "permissions": "Show the per-tool permission policy",
    "cwd": "Show or change the working directory: /cwd [path]",
    "help": "List commands",
    "quit": "Exit locode",
}


def command_names() -> list[str]:
    return list(COMMANDS)


def describe(name: str) -> str:
    return COMMANDS.get(name, "")


def parse(line: str) -> tuple[str, str] | None:
    """Split a '/command args' line into (command, rest). None if not a slash."""
    if not line.startswith("/"):
        return None
    body = line[1:].strip()
    if not body:
        return ("help", "")
    name, _, rest = body.partition(" ")
    return (name.lower(), rest.strip())


def help_text() -> str:
    width = max(len(c) for c in COMMANDS)
    lines = ["Commands:"]
    for name, desc in COMMANDS.items():
        lines.append(f"  /{name.ljust(width)}  {desc}")
    return "\n".join(lines)
