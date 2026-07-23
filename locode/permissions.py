"""Permission resolution: AUTO (run), ASK (prompt), DENY (refuse).

Resolution order (first match wins):
  1. session overrides (set by an "always" answer at a prompt)
  2. hard deny: a path-mutating tool targeting a `deny_paths` location -> DENY,
     even under --yolo. These protect secrets and the tool's own config.
  3. path auto-allow: a write/edit under an `auto_allow_under` prefix -> AUTO.
  4. base policy: config[tool] else the tool's declared default.
  5. --yolo flips a base ASK to AUTO (never overrides a hard deny).
"""

from __future__ import annotations

import os
from pathlib import Path

from locode.config import PermissionsConfig

AUTO, ASK, DENY = "auto", "ask", "deny"
_PATH_MUTATING = {"write_file", "append_file", "edit_file", "move_file"}


def _abspath(path: str, cwd: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd) / p
    # Normalize without requiring the file to exist.
    return Path(os.path.normpath(str(p)))


def _under(target: Path, prefix: str, cwd: str) -> bool:
    base = _abspath(prefix, cwd)
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


class PermissionPolicy:
    def __init__(self, perms: PermissionsConfig, yolo: bool = False):
        self._perms = perms
        self._yolo = yolo
        self._session: dict[str, str] = {}  # tool -> decision, from "always"

    def remember(self, tool: str, decision: str) -> None:
        self._session[tool] = decision

    def session_decision(self, tool: str) -> str | None:
        """The decision an "always" answer pinned for this session, if any.

        Callers need this to explain a refusal. A remembered DENY refuses every
        later call to the tool with no prompt and nothing on screen, which is
        indistinguishable from the tool being broken — one "no (always)" early
        in a session silently disarms it for the rest."""
        return self._session.get(tool)

    def resolve(self, tool_name: str, args: dict, cwd: str,
                declared: str | None = None) -> str:
        """`declared` is the tool class's own `permission` attribute.

        It used to be ignored entirely, so any tool the config did not list fell
        to ASK — which headless means "silently denied". `ask_user` was exactly
        that tool: declared "auto", unlisted, and therefore refused in the one
        mode where the install-escalation path depends on it. The config still
        wins when it names the tool; this only replaces the blind ASK default.
        """
        if tool_name in self._session:
            return self._session[tool_name]

        target = None
        if tool_name in _PATH_MUTATING and isinstance(args.get("path"), str):
            target = _abspath(args["path"], cwd)

        if tool_name == "move_file":
            # move_file has src and dst; deny if EITHER touches a deny_path.
            src = _abspath(args["src"], cwd) if isinstance(args.get("src"), str) else None
            dst = _abspath(args["dst"], cwd) if isinstance(args.get("dst"), str) else None
            if src is not None and any(_under(src, d, cwd) for d in self._perms.deny_paths):
                return DENY
            if dst is not None and any(_under(dst, d, cwd) for d in self._perms.deny_paths):
                return DENY
            if src is not None and any(_under(src, a, cwd) for a in self._perms.auto_allow_under):
                return AUTO
            if dst is not None and any(_under(dst, a, cwd) for a in self._perms.auto_allow_under):
                return AUTO

        if target is not None:
            # 2. hard deny — beats everything, including yolo.
            if any(_under(target, d, cwd) for d in self._perms.deny_paths):
                return DENY
            # 3. path auto-allow.
            if any(_under(target, a, cwd) for a in self._perms.auto_allow_under):
                return AUTO

        base = self._perms.tools.get(tool_name,
                                     declared or self._default_for(tool_name))
        if base == DENY:
            return DENY
        if self._yolo and base == ASK:
            return AUTO
        return base

    @staticmethod
    def _default_for(tool_name: str) -> str:
        # A tool that declares nothing at all defaults to ASK (conservative).
        return ASK
