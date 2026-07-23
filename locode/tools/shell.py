"""The bash tool: run a shell command, cancellable via the CancelToken.

The child runs in its own process group (start_new_session=True) so that on
Esc/Ctrl-C we can SIGTERM the whole group — including pipelines and children —
not just the top-level shell. Output is captured (stdout+stderr merged) and
truncated. Permission gating happens before run() is ever called.

A failed command gets one more thing: when the failure is a dependency install
that the environment blocked, `installhint` appends the project-local command
that would have worked, so the model has somewhere to go other than running the
identical thing again. See locode/tools/installhint.py.
"""

from __future__ import annotations

import asyncio
import os
import signal

from locode.tools.base import ToolContext, ToolResult
from locode.tools.installhint import install_hint

_MAX_OUTPUT = 64 * 1024
_DEFAULT_TIMEOUT = 120


class Bash:
    name = "bash"
    description = "Run a shell command in the working directory and return its output."
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "Shell command to run."},
            "timeout": {"type": "integer", "description": "Seconds (default 120)."},
        },
        "required": ["cmd"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        cmd = args["cmd"]
        timeout = int(args.get("timeout", _DEFAULT_TIMEOUT))
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=ctx.cwd,
                start_new_session=True,  # own process group for clean group-kill
            )
        except OSError as e:
            return ToolResult(f"failed to launch: {e}", is_error=True)

        def _kill_group() -> None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        deregister = ctx.cancel.add_cancel_hook(_kill_group) if ctx.cancel else (lambda: None)
        try:
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                _kill_group()
                await proc.wait()
                return ToolResult(f"timed out after {timeout}s", is_error=True)
        finally:
            deregister()

        if ctx.cancel and ctx.cancel.cancelled:
            return ToolResult("⛔ interrupted", is_error=True)

        text = (out or b"").decode("utf-8", "replace")
        if len(text) > _MAX_OUTPUT:
            text = text[:_MAX_OUTPUT] + "\n… (truncated)"
        rc = proc.returncode
        if rc != 0:
            body = f"[exit {rc}]\n{text}".rstrip()
            hint = install_hint(cmd, text, rc, ctx.cwd)
            if hint:
                body += f"\n\n{hint}"
            return ToolResult(body, is_error=True)
        return ToolResult(text.rstrip() or "(no output)")
