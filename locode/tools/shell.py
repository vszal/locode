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

# The rc-0-no-output sentinel. Exported because the agent loop matches on it to
# detect a run of calls that all succeeded and all said nothing (see
# agent/loop.py, _NOINFO_RESULTS) — keep the two in sync.
_EMPTY_OK = ("(exit 0 — the command ran fine but printed nothing. If it was a "
             "query, that IS the result: nothing matched. Re-running it "
             "unchanged will print nothing again — question the assumption "
             "behind it instead.)")


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
        # rc 0 with no output has TWO readings and one sentence has to serve
        # both. As a verify — `py_compile`, a quiet formatter, `pytest -q` that
        # printed nothing — silence is success, and a bare "(no output)" reads
        # as ambiguous enough that a weak model re-runs the identical command
        # hoping for confirmation (observed on indent-bug: file fixed, compile
        # green, re-ran to a repeat-stop). As a QUERY — ls-tree, ls-remote,
        # find, git log -- <path> — silence is the answer: nothing matched.
        # Saying only "command succeeded" there is worse than ambiguous, it is
        # misleading, and the model reads it as "worked but told me nothing" and
        # re-runs. Observed live: six consecutive empty-but-green git queries
        # against a path prefix that did not exist in the repo, four of them
        # byte-identical, until the repeat guard ended the turn. So: state that
        # it ran, give the query reading, and rule out the unchanged re-run.
        return ToolResult(text.rstrip() or _EMPTY_OK)
