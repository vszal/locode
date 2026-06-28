"""Open files / diffs in the user's local editor (shell-out, no extension).

Pure helpers (editor resolution, argv construction) are unit-tested; the actual
spawning is a thin async wrapper around them.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import tempfile

from locode.config import EditorConfig

_FALLBACKS = ["code -w", "nvim", "vim", "nano", "vi"]


def resolve_editor(cfg: EditorConfig, env: dict[str, str] | None = None) -> str | None:
    env = env if env is not None else os.environ
    for cand in (cfg.command, env.get("LOCODE_EDITOR"), env.get("VISUAL"),
                 env.get("EDITOR")):
        if cand:
            return cand
    for cand in _FALLBACKS:
        if shutil.which(shlex.split(cand)[0]):
            return cand
    return None


def build_open_argv(editor: str, path: str, line: int | None = None) -> list[str]:
    parts = shlex.split(editor)
    prog = os.path.basename(parts[0])
    if line:
        if prog in ("code", "code-insiders"):
            return parts + ["-g", f"{path}:{line}"]
        if prog in ("vim", "nvim", "vi", "nano"):
            return parts + [f"+{line}", path]
    return parts + [path]


def build_diff_argv(diff_tool: str, a: str, b: str) -> list[str] | None:
    """Argv to diff file `a` (current) vs `b` (proposed), or None to signal the
    caller should fall back to an inline unified diff."""
    if diff_tool:
        return shlex.split(diff_tool) + [a, b]
    if shutil.which("code"):
        return ["code", "--diff", a, b]
    if shutil.which("git"):
        return ["git", "difftool", "--no-index", a, b]
    return None


async def open_path(editor: str, path: str, line: int | None = None,
                    wait: bool = False) -> None:
    argv = build_open_argv(editor, path, line)
    proc = await asyncio.create_subprocess_exec(*argv)
    if wait:
        await proc.wait()


async def review_proposed(cfg: EditorConfig, path: str, proposed: str,
                          current: str = "") -> str:
    """Open a diff of current vs proposed in the editor; with cfg.wait, block
    and return the (possibly hand-edited) proposed text as saved. Falls back to
    returning `proposed` unchanged if no diff tool/editor is available."""
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix="." + os.path.basename(path), delete=False)
    tmp.write(proposed)
    tmp.close()
    cur = tempfile.NamedTemporaryFile("w", suffix=".orig", delete=False)
    cur.write(current)
    cur.close()
    argv = build_diff_argv(cfg.diff_tool, cur.name, tmp.name)
    try:
        if argv is None:
            return proposed
        proc = await asyncio.create_subprocess_exec(*argv)
        if cfg.wait:
            await proc.wait()
            with open(tmp.name) as f:
                return f.read()
        return proposed
    finally:
        for n in (tmp.name, cur.name):
            try:
                os.unlink(n)
            except OSError:
                pass
