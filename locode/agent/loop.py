"""The agentic orchestration loop.

Drives: ensure server up -> call model (streamed) -> parse tool intent
(tolerant, dual-path) -> gate by permission -> execute -> feed results back ->
repeat until the model stops calling tools or a budget trips. UI-agnostic: all
rendering and prompting happen through injected callbacks, so the loop is
unit-testable with stubs.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from locode.agent.cancel import (CancelToken, CancelledByUser,
                                 DeadlineExceeded)
from locode.agent.compact import compact_history, estimate_chars
from locode.agent.messages import build_system_prompt, tool_results_block
from locode.agent.plan import Plan
from locode.model import toolparse
from locode.model.profiles import profile_for
from locode.permissions import AUTO, ASK, DENY, PermissionPolicy
from locode.tools.base import Registry, ToolContext

# confirm(name, args, preview) -> "yes" | "always" | "no" | "no_always"
Confirm = Callable[[str, dict, str], Awaitable[str]]
OnEvent = Callable[[dict], Any]

# How long a no-tool-call reply must be before repeating it counts as a stall.
# The harm is proportional to what regenerating it costs: a terse "done" that
# gets nudged and repeated wastes nothing and keeps its ordinary handling, while
# a narrated document is a quarter of the turn budget each time it is re-emitted
# (18,709 chars took 245s in the sweep that motivated this). Well above any
# terse answer, well below a document.
PROSE_REPEAT_MIN_CHARS = 2000

# How much of a reply's opening identifies it. Long enough that two unrelated
# replies don't collide, short enough to sit well inside the region a
# regenerated document reproduces verbatim.
_PROSE_PREFIX = 400


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
        # Refused tool calls this turn, reset in run(). Declared here so
        # _run_calls never depends on run() having been entered.
        self._denials = 0
        # interrupt: callable() -> async context manager active ONLY around the
        # streaming model call (so confirm prompts get a clean terminal).
        self._interrupt = interrupt or _null_scope
        self.model_alias = config.model.default
        self.cancel = CancelToken()
        # The model's task list. Session-scoped rather than turn-scoped: a user
        # who says "ok, do step 3 now" is continuing the same plan, and throwing
        # it away between turns would make the loop forget what it agreed to.
        self.plan = Plan()
        # Wallclock time spent inside confirm() this turn — waiting on the human
        # to approve/deny a tool call isn't the model's fault, so it's excluded
        # from both the hard deadline and the slow-progress ratio. Reset each
        # run_turn(); accumulated in _ask().
        self._wallclock_pause = 0.0
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(registry, cwd),
             "kind": "system"}
        ]

    def set_model(self, alias: str) -> None:
        self.model_alias = alias

    def reset_context(self) -> None:
        self.history = self.history[:1]  # keep the system prompt

    def set_history(self, history: list[dict[str, Any]]) -> None:
        """Replace the conversation history wholesale (e.g. resuming a saved
        session). Copied so the caller's list isn't aliased into the loop."""
        self.history = list(history)

    def compact(self) -> str:
        """Explicit /compact: same structural rules as auto-compact (see
        agent/compact.py), run on demand rather than triggered by a size
        threshold. Returns a short human-readable report."""
        self.history, report = compact_history(
            self.history, keep_recent=self._cfg.agent.compact_keep_recent)
        return report

    async def run_turn(self, user_text: str) -> str:
        result = "(no result)"
        self._on_event({"phase": "turn_start", "model": self.model_alias,
                        "prompt_chars": len(user_text)})
        try:
            result = await self._run_turn(user_text)
            return result
        finally:
            self._on_event({"phase": "turn_end", "result": result})

    async def _run_turn(self, user_text: str) -> str:
        self.cancel.reset()
        # Server load / model switch can be a long, silent wait — let the UI spin.
        self._on_event({"phase": "busy_start", "text": f"loading {self.model_alias}…"})
        try:
            model_id = await self._manager.ensure_up(self.model_alias)
        finally:
            self._on_event({"phase": "busy_stop"})
        profile = profile_for(model_id)
        tools = self._registry.specs() if profile.native_tools else None
        self.history.append({"role": "user", "content": user_text,
                             "kind": "user_prompt"})

        start = time.monotonic()
        self._wallclock_pause = 0.0
        nudged_empty = False
        truncated_nudges = 0
        self._denials = 0
        nudged_repeat: set = set()
        nudged_stall: set = set()
        seen_prose: list = []
        nudged_slow = False
        nudged_intent = False
        open_task_nudges = 0
        missing_deliverable_nudges = 0
        # Whether a real tool call has happened since the last missing-
        # deliverable nudge — distinguishes "the model answered the nudge
        # directly" (trust it, even a plain refusal) from "the model detoured
        # through some other action and STILL didn't resolve it" (keep
        # pressing, bounded), which is what let a hallucinated "the file was
        # created" claim followed by a failed verify-read slip through as a
        # trusted final answer on the dead-end right after it.
        since_last_deliverable_nudge_call = False
        consecutive_malformed = 0
        # Both stuck-detectors below key off a STREAK PER SIGNATURE rather than
        # "is this batch equal to the one immediately before it".
        #
        # The older shape could only ever see a period-1 stall. What weak models
        # actually do is CYCLE: edit, run the test, make the same edit again, run
        # the same test again. No two adjacent iterations match, so the counters
        # reset every single turn and never fired. Measured 2026-07-21: a run
        # alternating a no-op edit_file with an identical pytest invocation burned
        # all 50 iterations, took 321s and emitted ZERO nudges. Keying per
        # signature makes whatever is interleaved irrelevant — each distinct call
        # and each distinct error accumulates its own streak.
        #
        # repeat_streaks also stores the RESULT each signature last produced, and
        # only counts a repeat when the result is unchanged too. Without that,
        # interleaving-immunity would misfire on ordinary work: running the same
        # test command between three different edits is progress, not a stall.
        repeat_streaks: dict[tuple, tuple[str, int]] = {}
        error_streaks: dict[str, int] = {}
        # Filenames the user asked to be WRITTEN this turn (e.g. "writing a
        # PLAN.md") — tracked against write_file/edit_file calls actually
        # attempted, to catch a model that reads around and then narrates a
        # plan in prose instead of ever producing the file.
        expected_artifacts = _expected_artifacts(user_text)
        attempted_paths: set[str] = set()
        # ALL file-like names mentioned in the request (read or write intent),
        # vs. which of them have actually been read — lets a repeat-call nudge
        # point at a concrete unread file instead of a vague "try something
        # different" that a stuck model just ignores.
        mentioned_files = _mentioned_files(user_text)
        read_paths: set[str] = set()
        try:
            for i in range(self._cfg.agent.max_iterations):
                now = time.monotonic()
                self._on_event({"phase": "iteration", "n": i,
                                "elapsed": round(now - start, 2)})
                # Time spent inside confirm() (waiting on the human, not the
                # model) doesn't count against the turn's wallclock budget.
                elapsed = now - start - self._wallclock_pause
                if elapsed > self._cfg.agent.max_wallclock_seconds:
                    return self._stop("budget: wallclock exceeded")
                # A stuck loop (or just a long session — history only shrinks via
                # an explicit reset) can grow the prompt past what the local
                # server can safely allocate; unlike the other budgets this isn't
                # about the MODEL's behavior; it's a resource guard. Checked
                # before every completion so it trips before the next request,
                # not after — a crashed mlx server can't return an error to react
                # to. See AgentConfig.max_history_chars for the incident this
                # guards against.
                history_chars = estimate_chars(self.history)
                # Soft threshold, checked first: shrink stale tool-result dumps
                # and bulky tool-call args (agent/compact.py) before the hard
                # stop below has to fire at all. Purely structural — no model
                # call — so it can't itself get stuck the way summarizing with
                # a weak local model could.
                if history_chars > (self._cfg.agent.max_history_chars
                                    * self._cfg.agent.auto_compact_ratio):
                    self.history, report = compact_history(
                        self.history,
                        keep_recent=self._cfg.agent.compact_keep_recent)
                    new_chars = estimate_chars(self.history)
                    if new_chars != history_chars:
                        self._on_event({"phase": "info",
                                        "text": f"auto-compacted context: {report}"})
                    history_chars = new_chars
                if history_chars > self._cfg.agent.max_history_chars:
                    return self._stop(
                        f"budget: conversation too large (~{history_chars:,} chars) "
                        "— risk of exhausting the local server's memory; start a "
                        "new session or /reset before continuing")
                # A model can be "on track" by iteration count yet still be
                # quietly burning the wallclock budget on slow/rambling
                # completions — the iteration cap alone won't catch that until
                # it's too late. Compare how much of each budget is spent: if
                # iterations are lagging wallclock by more than the configured
                # ratio, nudge once toward shorter, more decisive turns. Held
                # off by a grace period (both elapsed time AND iterations) so
                # ordinary first-iteration cold-start latency can't trip it.
                if (not nudged_slow
                        and elapsed >= self._cfg.agent.slow_progress_grace_seconds
                        and i >= self._cfg.agent.slow_progress_grace_iterations):
                    wallclock_frac = elapsed / self._cfg.agent.max_wallclock_seconds
                    iter_frac = i / self._cfg.agent.max_iterations
                    if iter_frac < wallclock_frac * self._cfg.agent.slow_progress_ratio:
                        nudged_slow = True
                        self._nudge_slow()
                # Esc/Ctrl-C listening is active ONLY around streaming; tool
                # approval prompts below run outside it with a clean terminal.
                # start/end frame each streamed reply so the UI can reset its
                # stream filter and flush any held-back tail.
                self._on_event({"phase": "assistant_start"})
                # Characters this reply actually generated, reported on
                # assistant_end so a run's throughput can be measured after the
                # fact. On local hardware tok/s is not a constant — memory
                # pressure from another process can drop it by an order of
                # magnitude, and every wallclock-derived budget silently
                # tightens with it. A sweep run on a degraded box looks like a
                # quality regression unless throughput is recorded alongside it.
                gen_chars = 0
                try:
                    async with self._interrupt():
                        msg = await self._client.complete(
                            _wire(self.history), model_id, tools=tools,
                            temperature=self._cfg.model.temperature,
                            max_tokens=self._cfg.model.max_tokens,
                            cancel=self.cancel, on_delta=self._on_delta,
                            # Cut a single runaway reply off at the turn's
                            # budget. Without this the wallclock check above
                            # only runs BETWEEN iterations, so one steadily
                            # streaming completion can overrun it many times
                            # over (httpx's timeout is per-read, and a model
                            # emitting tokens never trips it).
                            deadline=(start + self._wallclock_pause
                                      + self._cfg.agent.max_wallclock_seconds),
                        )
                    gen_chars = _reply_chars(msg)
                except DeadlineExceeded as e:
                    gen_chars = len(e.partial)
                    if e.partial:
                        self.history.append({"role": "assistant",
                                             "content": e.partial,
                                             "kind": "assistant"})
                    return self._stop(
                        "budget: wallclock exceeded during a single reply "
                        f"(~{len(e.partial):,} chars generated)")
                finally:
                    # Must fire even when the stream is cancelled mid-flight, or
                    # the UI's wait spinner is never stopped and flickers into the
                    # prompt after an interrupt.
                    self._on_event({"phase": "assistant_end", "chars": gen_chars})
                content = msg.get("content", "") or ""
                # The server tells us *why* generation stopped. "length" means
                # the reply was cut off at max_tokens — the text alone can't
                # distinguish that from a deliberate ending, and treating a
                # half-written reply as a final answer is a dead-end.
                hit_token_limit = msg.get("finish_reason") == "length"
                if hit_token_limit:
                    self._on_event({"phase": "truncated",
                                    "chars": len(content)})
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
                self.history.append({"role": "assistant", "content": content,
                                     "kind": "assistant"})
                if calls:
                    since_last_deliverable_nudge_call = True

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
                    # The same dead-end as a repeated tool call, one level up:
                    # the model repeats ITSELF rather than a call. Every stuck-
                    # detector below keys on a call signature, so a reply that
                    # makes no call is invisible to all of them.
                    #
                    # Stopping on the FIRST exact repeat is deliberate. Reaching
                    # here twice with identical text means a nudge was appended
                    # in between — every path that continues from this branch
                    # appends one — and the model produced byte-identical output
                    # anyway, so the nudge is proven inert. That is stronger
                    # evidence than a repeated tool call, which can be an honest
                    # retry, and it is far costlier to absorb: the case that
                    # motivated this regenerated an 18,709-char document
                    # verbatim, 245s and then 266s of a 600s turn, having never
                    # called write_file. A turn holds about two such replies, so
                    # nudging again just dies mid-reply with nothing written.
                    #
                    # Scoped to the branches below that NUDGE AND CONTINUE —
                    # truncation, a missing deliverable, open plan tasks. Those
                    # are the ones that grind. The announced-intent path already
                    # returns after a single nudge, and it guesses from phrasing,
                    # so a repeat there may still be a real answer that merely
                    # trips the heuristic; taking it is better than discarding it.
                    prose_sig = _prose_sig(content)
                    prose_repeat = (prose_sig[0] >= PROSE_REPEAT_MIN_CHARS
                                    and any(_same_prose(prose_sig, s)
                                            for s in seen_prose))
                    seen_prose.append(prose_sig)
                    if prose_repeat and (
                            hit_token_limit or _looks_truncated(content)
                            or (expected_artifacts - attempted_paths)
                            or self.plan.open):
                        return self._stop("the model repeated the same reply "
                                          "without making progress")
                    # An opened-but-unclosed ```tool fence means the call was cut
                    # off by the token limit: the parser can't recover it, and
                    # returning the half-written block as a "final answer" is the
                    # exact dead-end that looks like "stops without editing". Nudge
                    # the model to re-issue a smaller call before giving up. The
                    # server's own "length" verdict catches the cases the fence
                    # heuristic can't see — prose cut mid-sentence, or a call cut
                    # before its fence was ever opened.
                    if ((hit_token_limit or _looks_truncated(content))
                            and truncated_nudges
                            < self._cfg.agent.max_truncated_retries):
                        truncated_nudges += 1
                        self._nudge_truncated()
                        continue
                    # The model is about to stop with prose instead of the file(s)
                    # it was explicitly asked to write — the "reads everything,
                    # then just describes a plan" dead-end. Nudge for it to
                    # either produce the deliverable now or explain why it can't.
                    missing = expected_artifacts - attempted_paths
                    if missing and (missing_deliverable_nudges == 0
                                    or since_last_deliverable_nudge_call):
                        if missing_deliverable_nudges < self._cfg.agent.max_missing_deliverable_retries:
                            missing_deliverable_nudges += 1
                            since_last_deliverable_nudge_call = False
                            self._nudge_missing_deliverable(
                                missing,
                                drafted=prose_sig[0] >= PROSE_REPEAT_MIN_CHARS)
                            continue
                        return self._stop(
                            "the model never produced "
                            + ", ".join(sorted(missing)))
                    # The model is stopping with tasks IT declared unfinished.
                    # Its own plan is the strongest available evidence that the
                    # turn isn't over — stronger than any heuristic below, and
                    # unlike the deliverable check it works for requests that
                    # name no output file. Bounded, because a model that can't
                    # finish a task shouldn't be nudged at it forever.
                    if (self.plan.open and open_task_nudges
                            < self._cfg.agent.max_open_task_retries):
                        open_task_nudges += 1
                        self._nudge_open_tasks()
                        continue
                    # The reply ENDS by announcing an action it never took —
                    # "I'll examine the file:" followed by no tool call. The
                    # loop would otherwise hand that back as a final answer, so
                    # the turn reads as a confident no-op. Distinct from the
                    # missing-deliverable case above, which only fires when the
                    # request named a file to write; this catches the same
                    # dead-end for read/investigate/run work that names no
                    # artifact. Nudge once — if it announces intent twice, the
                    # second reply is returned rather than grinding.
                    if not nudged_intent and _announces_next_action(content):
                        nudged_intent = True
                        self._nudge_announced_intent()
                        continue
                    return content  # final answer
                if trimmed:
                    self._on_event({"phase": "info",
                                    "text": "ran the first proposed step; "
                                            "continuing after its result"})
                # A model that re-issues the same call(s) — not necessarily back
                # to back — and keeps getting the same answer is stuck (e.g.
                # retrying an edit whose `old`/`new` are identical, a no-op). Like
                # the empty/truncated/malformed dead-ends, nudge once to break it
                # out before bailing — skip re-running the known-futile call, tell
                # it the result won't change, and only stop if it persists.
                #
                # Checked BEFORE running, using the result this signature produced
                # last time: at a streak of max_repeat_calls - 1 the next identical
                # call is already known to be futile, so re-running it is pure
                # waste.
                batch_sig = tuple(_call_sig(c) for c in calls)
                seen_result, seen_streak = repeat_streaks.get(batch_sig, (None, 0))
                if seen_streak >= self._cfg.agent.max_repeat_calls - 1:
                    if batch_sig not in nudged_repeat:
                        nudged_repeat.add(batch_sig)
                        # "Try something different" is too vague for a weak model
                        # to act on — it just repeats again and burns the nudge.
                        # If the task mentioned other files this call hasn't
                        # touched yet, name them: a concrete next action is far
                        # more likely to break the loop than a generic prod.
                        unread = mentioned_files - read_paths - expected_artifacts
                        self._nudge_repeat(calls, unread)
                        continue
                    return self._stop("the model repeated the same tool call "
                                      "without making progress")
                consecutive_malformed = 0  # progress made
                for c in calls:
                    if c.name in ("write_file", "append_file", "edit_file"):
                        path = c.args.get("path")
                        if path:
                            attempted_paths.add(os.path.basename(str(path)).lower())
                    elif c.name == "read_file":
                        path = c.args.get("path")
                        if path:
                            read_paths.add(os.path.basename(str(path)).lower())
                error_sig, result_sig = await self._run_calls(calls)
                # Headless only: an ASK tool nobody can approve is refused for
                # the whole session, so a model still trying after this many
                # refusals is not going to stop on its own. Interactively the
                # same count means nothing — a user may decline several
                # unrelated calls in a turn that is otherwise going fine.
                if (self._confirm is None
                        and self._denials >= self._cfg.agent.max_error_stall):
                    return self._stop("the tools this task needs are not "
                                      "available in this session")
                # Same call, same answer -> the streak grows; a *changed* result
                # means the call did something new, so it starts over.
                repeat_streaks[batch_sig] = (
                    result_sig,
                    seen_streak + 1 if result_sig == seen_result else 1)
                # A subtler stuck signature than an identical *call*: the model
                # varies its edits each turn (so the repeat detector never fires)
                # yet the resulting ERROR is byte-for-byte the same every time —
                # the classic "keeps text-swapping a structural bug" loop. Key off
                # the error output, not the call. A clean (no-error) batch neither
                # counts nor resets: an edit succeeds, the model re-runs the test,
                # and only the recurring failure between them signals no progress.
                if error_sig is not None:
                    error_stall = error_streaks[error_sig] = \
                        error_streaks.get(error_sig, 0) + 1
                    if error_stall >= self._cfg.agent.max_error_stall:
                        if error_sig not in nudged_stall:
                            nudged_stall.add(error_sig)
                            self._nudge_stall()
                            continue
                        return self._stop("edits kept hitting the same error "
                                          "without making progress")
            return self._stop("budget: max iterations reached")
        except CancelledByUser:
            self.history.append({"role": "assistant", "content": "⛔ interrupted",
                                 "kind": "assistant"})
            return "⛔ interrupted"

    # --- internals -------------------------------------------------------
    async def _run_calls(self, calls) -> tuple[str | None, str]:
        """Run the batch, feed the results back, and return two signatures.

        The first is the ERROR signature — the joined content of any is_error
        results, keyed by tool name, or None if nothing errored — which the loop
        uses to detect edits that keep hitting the same failure. Denials and
        unknown-tool aren't model-fixable code errors, so they don't count
        toward the stall signal.

        The second is the FULL result signature, errors and successes alike. It
        answers a different question: "did this exact call actually do anything
        different this time?" A repeated call whose output changes is working;
        one whose output is identical cannot make progress no matter how often
        it is retried."""
        ctx = ToolContext(cwd=self._cwd, cancel=self.cancel,
                          confirm=self._confirm, select=self._select,
                          plan=self.plan)
        results: list[tuple[str, str]] = []
        error_parts: list[str] = []
        for call in calls:
            tool = self._registry.get(call.name)
            if tool is None:
                results.append((call.name, f"error: no such tool {call.name!r}"))
                continue
            decision = self._policy.resolve(call.name, call.args, self._cwd)
            if decision == ASK:
                decision = await self._ask(call)
            if decision == DENY:
                results.append((call.name, self._denial_text(call.name)))
                self._denials += 1
                self._on_event({"phase": "denied", "name": call.name})
                continue
            self._on_event({"phase": "run", "name": call.name, "args": call.args})
            t0 = time.monotonic()
            res = await tool.run(call.args, ctx)
            self._on_event({"phase": "result", "name": call.name,
                            "error": res.is_error, "content": res.content,
                            "seconds": round(time.monotonic() - t0, 3)})
            results.append((call.name, res.content))
            if res.is_error:
                error_parts.append(f"{call.name}: {res.content}")
        self.history.append({"role": "user", "content": tool_results_block(results),
                             "kind": "tool_result"})
        result_sig = "\n".join(f"{name}: {content}" for name, content in results)
        return ("\n".join(error_parts) if error_parts else None), result_sig

    def _denial_text(self, name: str) -> str:
        """What a refused tool call tells the model.

        "denied by permission policy" was true and useless: it named no reason
        and implied nothing about whether trying again might work. A local
        model reading that does the obvious thing and runs a variant — `npm
        install -g x`, then without -g, then with sudo — until a stuck-detector
        ends the turn three calls later. When the refusal is permanent, say so
        in the words a model acts on."""
        if self._confirm is None:
            # Headless: nobody can ever approve this, so every retry is wasted.
            return (f"denied: {name} is not available in this session — there "
                    f"is no one present to approve it, so calling it again "
                    f"will be refused every time. Do NOT retry {name}. Finish "
                    f"with the tools you do have, or stop and state plainly "
                    f"what you could not do without it.")
        return (f"denied: the user refused this {name} call. Do not repeat it. "
                f"Try a different approach, or ask what they would prefer.")

    async def _ask(self, call) -> str:
        if self._confirm is None:
            return DENY  # no human available (e.g. headless) -> refuse ASK tools
        preview = _preview(call)
        pause_start = time.monotonic()
        try:
            answer = await self._confirm(call.name, call.args, preview)
        finally:
            self._wallclock_pause += time.monotonic() - pause_start
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
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "empty response"})

    def _nudge_truncated(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("Your last reply was cut off at the token limit before "
                        "it finished — it was too long. Do it again in SMALLER "
                        "pieces. For an edit, keep `old` to the SMALLEST unique "
                        "snippet that needs changing (a few lines), not the "
                        "whole file, and make several small edit_file calls "
                        "instead of one giant one. For a long document, "
                        "write_file the FIRST section now, then add each "
                        "remaining section with a separate append_file call — "
                        "do not send the whole document again."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "tool call truncated"})

    def _nudge(self, malformed: list[str]) -> None:
        reason = "; ".join(malformed[:3])
        self.history.append({
            "role": "user",
            "content": (f"Your tool call could not be parsed ({reason}). "
                        "Emit exactly one ```tool block with valid JSON, or "
                        "reply normally if no tool is needed."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": reason})

    def _nudge_repeat(self, calls, unread: set[str] = frozenset()) -> None:
        names = ", ".join(dict.fromkeys(c.name for c in calls))
        hint = ""
        if unread:
            hint = (" You have NOT yet looked at: " + ", ".join(sorted(unread)) +
                    " — read one of those next instead of repeating this call.")
        self.history.append({
            "role": "user",
            "content": (f"You have issued the same {names} call several times and "
                        "it returned the same result each time — repeating it will "
                        f"not change anything. Stop repeating it.{hint} Either try a "
                        "genuinely different approach (different arguments, a "
                        "different tool, or re-read the file/error first), or if "
                        "the task is already done or truly cannot proceed, give "
                        "your final answer in plain text now."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "repeated call"})

    def _nudge_stall(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("Your last few edits have NOT changed the error — it is "
                        "identical each time. That means the change you keep "
                        "making is not the real fix: this is a STRUCTURAL problem "
                        "(control flow, indentation/scope, or how the pieces fit "
                        "together), not a small text substitution. Stop making the "
                        "same kind of edit. Re-read the whole relevant function and "
                        "reason about WHY the error happens, then rewrite the entire "
                        "function in one shot with write_file instead of another "
                        "small edit_file swap. If you genuinely cannot fix it, say "
                        "so in plain text now."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "error unchanged across edits"})

    def _nudge_missing_deliverable(self, missing: set[str],
                                   drafted: bool = False) -> None:
        names = ", ".join(sorted(missing))
        if drafted:
            # The model didn't fail to do the work — it did the work into the
            # wrong channel, writing the whole document as chat prose. Telling
            # it "you've only looked around" is simply false, and a model that
            # has just spent a quarter of the turn budget composing the document
            # answers that by composing it again. Name what it actually did and
            # give it the one concrete action left.
            body = (f"You wrote the contents of {names} into your reply instead "
                    "of creating the file — so the file still does not exist. Do "
                    "NOT write that text out again. Call write_file now with "
                    f"path {names} and pass the text you just wrote as the "
                    "content argument.")
        else:
            body = (f"You were asked to write {names}, but no write_file or "
                    "edit_file call for it has happened yet — you've only "
                    "looked around. Either create it now with a tool call, or "
                    "if you genuinely cannot, say exactly why in plain text.")
        self.history.append({
            "role": "user", "content": body, "kind": "nudge"})
        self._on_event({"phase": "nudge", "reason": f"missing deliverable: {names}"})

    def _nudge_open_tasks(self) -> None:
        nxt = self.plan.current
        self.history.append({
            "role": "user",
            "content": (f"You are not finished — your own plan still has "
                        f"{len(self.plan.open)} task(s) open:\n\n"
                        f"{self.plan.render()}\n\n"
                        f"Continue with: {nxt.text if nxt else 'the next task'}. "
                        "Do the work now with a tool call — do not reply with a "
                        "summary. If a task turned out to be unnecessary or "
                        "impossible, call update_plan to mark it done and say "
                        "why, then carry on with the rest."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "open plan tasks",
                        "plan": self.plan.summary()})

    def _nudge_announced_intent(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("You described what you were about to do but never did "
                        "it — no tool call followed. Saying it is not doing it. "
                        "Carry out that step now by emitting the ```tool block "
                        "for it. If you have actually finished, state your "
                        "conclusion in plain text instead, with no announcement "
                        "of further work."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "announced intent, no action"})

    def _nudge_slow(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("You're spending a lot of wallclock time relative to how "
                        "many steps you've actually taken — long or rambling "
                        "replies are burning the turn's time budget without "
                        "making proportional progress. Be more decisive: skip "
                        "restating the plan, keep any explanation brief, and "
                        "move straight to the next concrete tool call."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "slow progress vs wallclock"})

    def _stop(self, why: str) -> str:
        self._on_event({"phase": "stopped", "reason": why})
        return f"⏹ stopped ({why})"


def _wire(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The history as sent to the model server: role/content only. `history`
    entries also carry a "kind" tag (agent/compact.py's classification of
    system/user_prompt/assistant/tool_result/nudge) that's purely internal
    bookkeeping and must never leak onto the wire."""
    return [{"role": m["role"], "content": m["content"]} for m in history]


_OPEN_FENCE_RE = re.compile(r"```(?:tool_call|tool|json)\b", re.IGNORECASE)

# A file-like token — deliberately restricted to common text/code/doc
# extensions (not e.g. "3.10" or "example.com") to keep false positives down.
_ARTIFACT_RE = re.compile(
    r"\b[\w][\w\-]{0,80}\.(?:md|markdown|txt|rst|json|ya?ml|toml|csv|log"
    r"|py|ts|tsx|js|jsx|sh|cfg|ini)\b",
    re.IGNORECASE,
)
_WRITE_VERB_RE = re.compile(
    r"\b(?:writ(?:e|es|ing)|creat(?:e|es|ing)|generat(?:e|es|ing)"
    r"|produc(?:e|es|ing)|sav(?:e|es|ing)|draft(?:s|ing)?|output(?:s|ting)?"
    r"|updat(?:e|es|ing))\b",
    re.IGNORECASE,
)


def _expected_artifacts(user_text: str) -> set[str]:
    """Filenames the user's message asked to be WRITTEN this turn — an
    artifact-looking token (e.g. "PLAN.md") preceded within a short window by a
    write-ish verb ("writing a PLAN.md"), so a file merely mentioned for reading
    ("read config.py") doesn't count. Returns lowercased basenames, for later
    comparison against write_file/edit_file call paths."""
    artifacts = set()
    for m in _ARTIFACT_RE.finditer(user_text):
        window = user_text[max(0, m.start() - 60):m.start()]
        if _WRITE_VERB_RE.search(window):
            artifacts.add(m.group(0).lower())
    return artifacts


def _mentioned_files(user_text: str) -> set[str]:
    """All file-like names mentioned in the request, regardless of whether the
    intent was to read or write them — used to point a stuck model at a
    concrete unread file instead of a vague "do something different"."""
    return {m.group(0).lower() for m in _ARTIFACT_RE.finditer(user_text)}


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


# An announcement of work about to be done. Deliberately requires an action
# verb after the intent phrase: "let me know if…" is a perfectly good way to end
# a real final answer, while "let me check the file" is not.
_ANNOUNCED_INTENT_RE = re.compile(
    r"\b(?:i'?ll|i\s+will|i'?m\s+going\s+to|i\s+need\s+to|let\s+me|let'?s|"
    r"now\s+i|next\s+i|first\s+i|i\s+should)\s+(?:just\s+|now\s+|first\s+|"
    r"quickly\s+|also\s+|then\s+)*"
    r"(?:start|begin|look|check|examine|inspect|read|open|review|explore|"
    r"search|find|analyz\w*|investigat\w*|create|writ\w*|implement|add|fix|"
    r"updat\w*|modif\w*|edit|run|test|verif\w*|make|build|generat\w*|"
    r"produc\w*|draft|continue|proceed)\b",
    re.IGNORECASE,
)


def _announces_next_action(content: str) -> bool:
    """True if the reply ENDS by announcing an action it never took — the
    "I'll examine the file:" dead-end, where a model narrates intent, emits no
    tool call, and the loop hands that back as a confident final answer.

    Judged on the tail only. A genuine answer may mention what it did in the
    middle and then conclude; what marks the dead-end is the message *stopping*
    on the announcement, either with a dangling colon (the list or block it
    promised never arrived) or with the last line still in future tense.
    """
    tail = content.rstrip()
    if not tail:
        return False
    if tail.endswith(":"):
        return True
    last_line = tail.splitlines()[-1].strip()
    # A long trailing paragraph is prose, not an announcement; and a line that
    # is only a fence/bullet marker carries no intent either way.
    if not 3 < len(last_line) <= 200:
        return False
    return bool(_ANNOUNCED_INTENT_RE.search(last_line))


def _prose_sig(content: str) -> tuple[int, str]:
    """Signature for spotting a reply the model has essentially re-emitted.

    Returns its normalized length and a normalized opening. Exact equality does
    not survive contact with a sampled model: the run that exposed this
    regenerated a 25,391-character document that differed in a SINGLE character
    13,659 in — a real newline where the first copy had a literal backslash-n —
    which an exact match, and even a whitespace-normalized one, both call a
    different reply."""
    norm = " ".join(content.split())
    return len(norm), norm[:_PROSE_PREFIX]


def _same_prose(a: tuple[int, str], b: tuple[int, str]) -> bool:
    """Whether two replies are the same document written twice.

    Same opening AND near-identical length. The length test is what keeps this
    honest: a model that ANSWERS a truncation nudge writes a materially shorter
    document, and a shorter document opens exactly the same way — so on the
    prefix alone, doing the right thing would be indistinguishable from
    stalling."""
    if a[1] != b[1]:
        return False
    return abs(a[0] - b[0]) <= max(64, int(0.02 * max(a[0], b[0])))


def _reply_chars(msg) -> int:
    """How many characters a completed reply generated, for throughput metering.

    Counts the prose plus any NATIVE tool_calls. A reply that arrives as
    structured tool_calls has empty content but was every bit as expensive to
    generate, so counting content alone would report a fast model as stalled on
    exactly the turns where it was working."""
    total = len(msg.get("content", "") or "")
    calls = msg.get("tool_calls") or ()
    for c in calls:
        try:
            total += len(json.dumps(c, ensure_ascii=False))
        except (TypeError, ValueError):
            total += len(str(c))
    return total


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
    if call.name in ("write_file", "append_file", "edit_file"):
        return call.args.get("path", "")
    return ", ".join(f"{k}={v!r}" for k, v in call.args.items())[:200]
