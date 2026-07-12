"""The agentic orchestration loop.

Drives: ensure server up -> call model (streamed) -> parse tool intent
(tolerant, dual-path) -> gate by permission -> execute -> feed results back ->
repeat until the model stops calling tools or a budget trips. UI-agnostic: all
rendering and prompting happen through injected callbacks, so the loop is
unit-testable with stubs.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from locode.agent.cancel import CancelToken, CancelledByUser
from locode.agent.messages import build_system_prompt, tool_results_block
from locode.model import toolparse
from locode.model.profiles import profile_for
from locode.permissions import AUTO, ASK, DENY, PermissionPolicy
from locode.tools.base import Registry, ToolContext

# confirm(name, args, preview) -> "yes" | "always" | "no" | "no_always"
Confirm = Callable[[str, dict, str], Awaitable[str]]
OnEvent = Callable[[dict], Any]


@asynccontextmanager
async def _null_scope():
    yield


class AgentLoop:
    def __init__(self, client, manager, registry: Registry,
                 policy: PermissionPolicy, config, *, cwd: str,
                 on_delta=None, on_event: OnEvent | None = None,
                 confirm: Confirm | None = None, select=None, interrupt=None):
        self._client = client
        self._manager = manager
        self._registry = registry
        self._policy = policy
        self._cfg = config
        self._cwd = cwd
        self._on_delta = on_delta
        self._on_event = on_event or (lambda e: None)
        self._confirm = confirm
        self._select = select
        # interrupt: callable() -> async context manager active ONLY around the
        # streaming model call (so confirm prompts get a clean terminal).
        self._interrupt = interrupt or _null_scope
        self.model_alias = config.model.default
        self.cancel = CancelToken()
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(registry, cwd)}
        ]

    def set_model(self, alias: str) -> None:
        self.model_alias = alias

    def reset_context(self) -> None:
        self.history = self.history[:1]  # keep the system prompt

    def set_history(self, history: list[dict[str, Any]]) -> None:
        """Replace the conversation history wholesale (e.g. resuming a saved
        session). Copied so the caller's list isn't aliased into the loop."""
        self.history = list(history)

    async def run_turn(self, user_text: str) -> str:
        self.cancel.reset()
        # Server load / model switch can be a long, silent wait — let the UI spin.
        self._on_event({"phase": "busy_start", "text": f"loading {self.model_alias}…"})
        try:
            model_id = await self._manager.ensure_up(self.model_alias)
        finally:
            self._on_event({"phase": "busy_stop"})
        profile = profile_for(model_id)
        tools = self._registry.specs() if profile.native_tools else None
        self.history.append({"role": "user", "content": user_text})

        deadline = time.monotonic() + self._cfg.agent.max_wallclock_seconds
        nudged_empty = False
        nudged_truncated = False
        nudged_repeat = False
        consecutive_malformed = 0
        last_batch_sig = None
        repeat_count = 0
        try:
            for _ in range(self._cfg.agent.max_iterations):
                if time.monotonic() > deadline:
                    return self._stop("budget: wallclock exceeded")
                # Esc/Ctrl-C listening is active ONLY around streaming; tool
                # approval prompts below run outside it with a clean terminal.
                # start/end frame each streamed reply so the UI can reset its
                # stream filter and flush any held-back tail.
                self._on_event({"phase": "assistant_start"})
                try:
                    async with self._interrupt():
                        msg = await self._client.complete(
                            self.history, model_id, tools=tools,
                            temperature=self._cfg.model.temperature,
                            max_tokens=self._cfg.model.max_tokens,
                            cancel=self.cancel, on_delta=self._on_delta,
                        )
                finally:
                    # Must fire even when the stream is cancelled mid-flight, or
                    # the UI's wait spinner is never stopped and flickers into the
                    # prompt after an interrupt.
                    self._on_event({"phase": "assistant_end"})
                content = msg.get("content", "") or ""
                outcome = toolparse.extract(msg, self._registry.names(),
                                            self._registry.arg_names())
                calls = outcome.calls

                # Weak local models emit SEVERAL ```tool blocks in one turn —
                # speculatively planning ls→read→edit before seeing any result.
                # Only the first is grounded; the rest assume state that doesn't
                # exist yet (a hallucinated `old`), which is exactly what makes
                # edits cascade into "old not found" and burns the token budget on
                # a plan that truncates mid-block. Execute one grounded call, feed
                # its result, and let the model re-plan. Native tool_calls are
                # structured, intentional parallelism and are left intact.
                trimmed = len(calls) > 1 and all(c.source != "native" for c in calls)
                if trimmed:
                    calls = calls[:1]

                # Record a coherent assistant turn that ALWAYS shows the call(s)
                # we will run, in the fenced ```tool format we teach:
                #  - trimmed batch / empty content: store the fenced call(s) only.
                #  - native call(s) WITH narration: keep the prose AND append the
                #    fenced call. Native tool_calls don't round-trip into history on
                #    their own, so dropping them (as we used to whenever content was
                #    non-empty) left the model seeing its narration followed by tool
                #    results with no record of the call between — incoherent history
                #    that makes strong native tool-callers (Qwen3-Coder) narrate
                #    "let me fix this:" and then STOP instead of emitting the edit.
                #  - fenced/salvage call(s): the call is already in content; keep it
                #    as-is so we don't duplicate the block.
                if calls:
                    if trimmed or not content.strip():
                        content = _render_calls_as_fenced(calls)
                    elif all(c.source == "native" for c in calls):
                        content = content.rstrip() + "\n" + _render_calls_as_fenced(calls)
                self.history.append({"role": "assistant", "content": content})

                if not calls:
                    if outcome.malformed:
                        # Don't grind all the iterations re-nudging a model that
                        # can't fix its own tool JSON — bail clearly after a few.
                        consecutive_malformed += 1
                        if consecutive_malformed >= self._cfg.agent.max_malformed_retries:
                            return self._stop("the model kept emitting unparseable "
                                              "tool calls")
                        self._nudge(outcome.malformed)
                        continue
                    if not content.strip():
                        # Empty answer with no tool call is a dead-end: silently
                        # returning "" looks exactly like "stopped after one
                        # step". Nudge once for a real reply before giving up.
                        if not nudged_empty:
                            nudged_empty = True
                            self._nudge_empty()
                            continue
                        return "(the model returned an empty response)"
                    # An opened-but-unclosed ```tool fence means the call was cut
                    # off by the token limit: the parser can't recover it, and
                    # returning the half-written block as a "final answer" is the
                    # exact dead-end that looks like "stops without editing". Nudge
                    # the model to re-issue a smaller call before giving up.
                    if _looks_truncated(content) and not nudged_truncated:
                        nudged_truncated = True
                        self._nudge_truncated()
                        continue
                    return content  # final answer
                if trimmed:
                    self._on_event({"phase": "info",
                                    "text": "ran the first proposed step; "
                                            "continuing after its result"})
                # A model that re-issues the EXACT same call(s) turn after turn is
                # stuck (e.g. retrying an edit whose `old`/`new` are identical, a
                # no-op). Like the empty/truncated/malformed dead-ends, nudge once
                # to break it out before bailing — skip re-running the duplicate,
                # tell it the result won't change, and only stop if it repeats even
                # after the nudge.
                batch_sig = tuple(_call_sig(c) for c in calls)
                if batch_sig == last_batch_sig:
                    repeat_count += 1
                else:
                    repeat_count = 1
                    nudged_repeat = False
                last_batch_sig = batch_sig
                if repeat_count >= self._cfg.agent.max_repeat_calls:
                    if not nudged_repeat:
                        nudged_repeat = True
                        self._nudge_repeat(calls)
                        continue
                    return self._stop("the model repeated the same tool call "
                                      "without making progress")
                consecutive_malformed = 0  # progress made
                await self._run_calls(calls)
            return self._stop("budget: max iterations reached")
        except CancelledByUser:
            self.history.append({"role": "assistant", "content": "⛔ interrupted"})
            return "⛔ interrupted"

    # --- internals -------------------------------------------------------
    async def _run_calls(self, calls) -> None:
        ctx = ToolContext(cwd=self._cwd, cancel=self.cancel,
                          confirm=self._confirm, select=self._select)
        results: list[tuple[str, str]] = []
        for call in calls:
            tool = self._registry.get(call.name)
            if tool is None:
                results.append((call.name, f"error: no such tool {call.name!r}"))
                continue
            decision = self._policy.resolve(call.name, call.args, self._cwd)
            if decision == ASK:
                decision = await self._ask(call)
            if decision == DENY:
                results.append((call.name, "denied by permission policy"))
                self._on_event({"phase": "denied", "name": call.name})
                continue
            self._on_event({"phase": "run", "name": call.name, "args": call.args})
            res = await tool.run(call.args, ctx)
            self._on_event({"phase": "result", "name": call.name,
                            "error": res.is_error, "content": res.content})
            results.append((call.name, res.content))
        self.history.append({"role": "user", "content": tool_results_block(results)})

    async def _ask(self, call) -> str:
        if self._confirm is None:
            return DENY  # no human available (e.g. headless) -> refuse ASK tools
        preview = _preview(call)
        answer = await self._confirm(call.name, call.args, preview)
        if answer == "always":
            self._policy.remember(call.name, AUTO)
            return AUTO
        if answer == "no_always":
            self._policy.remember(call.name, DENY)
            return DENY
        return AUTO if answer == "yes" else DENY

    def _nudge_empty(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("You replied with an empty message. Either call a tool "
                        "using the ```tool format, or give your final answer in "
                        "plain text now."),
        })
        self._on_event({"phase": "nudge", "reason": "empty response"})

    def _nudge_truncated(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("Your last tool call was cut off before it finished — it "
                        "was too long. Re-issue it now, but keep `old` to the "
                        "SMALLEST unique snippet that needs changing (a few "
                        "lines), not the whole file, and make several small "
                        "edit_file calls instead of one giant one."),
        })
        self._on_event({"phase": "nudge", "reason": "tool call truncated"})

    def _nudge(self, malformed: list[str]) -> None:
        reason = "; ".join(malformed[:3])
        self.history.append({
            "role": "user",
            "content": (f"Your tool call could not be parsed ({reason}). "
                        "Emit exactly one ```tool block with valid JSON, or "
                        "reply normally if no tool is needed."),
        })
        self._on_event({"phase": "nudge", "reason": reason})

    def _nudge_repeat(self, calls) -> None:
        names = ", ".join(dict.fromkeys(c.name for c in calls))
        self.history.append({
            "role": "user",
            "content": (f"You have issued the same {names} call several times and "
                        "it returned the same result each time — repeating it will "
                        "not change anything. Stop repeating it. Either try a "
                        "genuinely different approach (different arguments, a "
                        "different tool, or re-read the file/error first), or if "
                        "the task is already done or truly cannot proceed, give "
                        "your final answer in plain text now."),
        })
        self._on_event({"phase": "nudge", "reason": "repeated call"})

    def _stop(self, why: str) -> str:
        self._on_event({"phase": "stopped", "reason": why})
        return f"⏹ stopped ({why})"


_OPEN_FENCE_RE = re.compile(r"```(?:tool_call|tool|json)\b", re.IGNORECASE)


def _looks_truncated(content: str) -> bool:
    """True if content opened a ```tool/```json fence that was never closed — the
    signature of a tool call cut off mid-emission by the token limit. The fence
    regex needs a closing ``` to match, so such a call parses to nothing; without
    this check the loop would return the half-written block as a final answer."""
    last_open = None
    for last_open in _OPEN_FENCE_RE.finditer(content):
        pass
    if last_open is None:
        return False
    return "```" not in content[last_open.end():]


def _call_sig(call) -> tuple:
    """A stable identity for a tool call, for detecting no-progress repetition."""
    return (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))


def _render_calls_as_fenced(calls) -> str:
    """Render parsed tool calls back into the fenced ```tool format we teach, so
    a turn that arrived as native tool_calls (empty content) still leaves a
    coherent, self-describing assistant message in the resent history."""
    blocks = []
    for c in calls:
        payload = json.dumps({"name": c.name, "args": c.args}, ensure_ascii=False)
        blocks.append(f"```tool\n{payload}\n```")
    return "\n".join(blocks)


def _preview(call) -> str:
    if call.name == "bash":
        return call.args.get("cmd", "")
    if call.name in ("write_file", "edit_file"):
        return call.args.get("path", "")
    return ", ".join(f"{k}={v!r}" for k, v in call.args.items())[:200]
