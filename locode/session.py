"""Session persistence: save/restore a conversation transcript to disk.

A session is the agent's message history plus a little context (the model alias,
cwd, and when it was saved), stored as JSON under STATE_DIR/sessions/. Used by
the REPL's /save and /resume commands.

Filenames are derived through safe_name(), which strips a user-supplied name down
to [a-z0-9-_] — no path separators, no dots — so a name can never write or read
outside the sessions directory.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from locode.config import STATE_DIR

SESSIONS_DIR = STATE_DIR / "sessions"

_REQUIRED = ("name", "model", "cwd", "saved_at", "history")


@dataclass(frozen=True)
class Session:
    name: str
    model: str
    cwd: str
    saved_at: str           # ISO8601, set by the caller
    history: list[dict]     # the agent message list (system + turns)


def safe_name(name: str) -> str:
    """A filesystem-safe stem for `name`: lowercased, only [a-z0-9-_], hyphen
    runs collapsed, trimmed. Empty -> "session". Guarantees no path separators
    or dots, so the result can never escape the sessions directory."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "session"


def session_path(name: str, base: Path | None = None) -> Path:
    return (base or SESSIONS_DIR) / (safe_name(name) + ".json")


def save_session(session: Session, base: Path | None = None) -> Path:
    path = session_path(session.name, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(session), ensure_ascii=False, indent=2))
    return path


def load_session(name: str, base: Path | None = None) -> Session:
    """Load a saved session. FileNotFoundError if it doesn't exist; ValueError if
    the file is unparseable or missing required fields."""
    path = session_path(name, base)
    text = path.read_text()  # FileNotFoundError propagates
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"session {safe_name(name)!r} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or any(k not in data for k in _REQUIRED):
        raise ValueError(f"session {safe_name(name)!r} is missing required fields")
    return Session(name=data["name"], model=data["model"], cwd=data["cwd"],
                   saved_at=data["saved_at"], history=data["history"])


def list_sessions(base: Path | None = None) -> list[Session]:
    """All saved sessions, newest (by saved_at) first. Unparseable files are
    skipped; a missing directory yields an empty list."""
    root = base or SESSIONS_DIR
    if not root.is_dir():
        return []
    out: list[Session] = []
    for f in root.glob("*.json"):
        try:
            out.append(load_session(f.stem, base=root))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda s: s.saved_at, reverse=True)
    return out
