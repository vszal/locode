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
from locode.tools.base import Registry, ToolContext, ToolResult
from locode.tools.shell import _EMPTY_OK

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

# Content-mutating edit tools. A byte-identical call to one of these is a loop
# even when its result echo differs run-to-run: re-applying the same edit either
# no-ops or, for a line-number edit (replace_lines) against a file that has
# shifted under it, silently DUPLICATES content. So the repeat detector must not
# let their changing diff/snippet echo reset the streak (see the repeat_streaks
# update). move_file is excluded — a repeated rename just errors "not found".
_MUTATING_EDIT_TOOLS = frozenset(
    {"write_file", "append_file", "edit_file", "replace_lines"})

# Pure reads: no side effects, and their whole value is the output they put in
# the context. When compaction throws that output away (agent/compact.py), a
# re-issued read is the ONLY way back to the evidence — see _forgive_rereads.
# bash is deliberately absent: it can mutate, so its streak always stands.
_REREADABLE_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})

# How many times one read signature may be forgiven before the repeat guard is
# allowed to see it again. Two: the first re-read after a compaction is the
# system working as designed, a second is plausible, a third is a loop.
_MAX_FORGIVEN_REREADS = 2

# Exact sentinels a tool returns when it ran fine and found nothing. Matched as
# whole results, never as substrings — a grep that legitimately finds the text
# "(no matches)" inside a file must not read as an empty grep. shell._EMPTY_OK
# is imported rather than copied so the two can't drift apart.
_NOINFO_RESULTS = frozenset({
    _EMPTY_OK,            # bash, rc 0 with no output
    "(no matches)",       # grep / glob
    "(empty directory)",  # ls
    "(empty file)",       # read_file
    "(no output)",
})


def _is_noinfo(content: str) -> bool:
    """True when a SUCCESSFUL result carried no information at all."""
    return (content or "").strip() in _NOINFO_RESULTS

# How many times a turn tells the model that its context was compacted. The
# advice ("write down what you found; re-reading costs the same space again")
# is worth saying, and worth saying twice; by the third time it is boilerplate
# the model reads past, and it competes for the same budget it is warning about.
_MAX_COMPACT_NOTICES = 2

# How many times one signature is excused for being the call an open-tasks nudge
# just asked for. One: we demanded it, so the first is ours, not the model's.
_MAX_FORGIVEN_NUDGED = 1

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
        salvage_writes = 0
        repetition_aborts = 0
        self._denials = 0
        nudged_repeat: set = set()
        nudged_stall: set = set()
        seen_prose: list = []
        nudged_slow = False
        nudged_intent = False
        nudged_unverified_tests = False
        nudged_unverified_verify = False
        # Reset per turn: set True the moment a bash result shows a genuinely
        # green pytest tally, so a "the tests pass" final answer can be gated on
        # the model having actually SEEN green rather than asserting it blind.
        self._saw_green_test = False
        # Sibling flag for the non-test verify class (compile/run/import): set
        # True the moment a code-CHECKING bash command (py_compile, python, ruff,
        # ...) exits cleanly, so a "the file compiles / runs fine" final answer
        # can be gated on the model having actually watched it pass.
        self._saw_verify_ok = False
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
        # Did a given signature's result CHANGE while it was being repeated? True
        # only for the duplicating-edit case (identical replace_lines "succeeding"
        # with a new diff each time) — not a plain no-op repeat (constant result).
        # Lets the repeat nudge pick the accurate message.
        repeat_varied: dict[tuple, bool] = {}
        error_streaks: dict[str, int] = {}
        # Per-signature budget for _forgive_rereads, so frequent compaction
        # can't permanently disarm the repeat guard for read-only batches.
        forgiven_rereads: dict[tuple, int] = {}
        # Same, for the verify call an open-tasks nudge just demanded.
        forgiven_nudged: dict[tuple, int] = {}
        compact_notices = 0
        # Consecutive iterations whose edit batch changed the file NOTHING (a
        # blind guess — usually at a line the error names but that is actually
        # fine, since tracebacks/compilers misreport the location). The first is
        # tolerated (the model often self-corrects); a second in a row earns a
        # specific redirect (confirm the real failure before editing again); a
        # third stops the turn. Reset the moment any batch does real work.
        nochange_streak = 0
        nudged_nochange = False
        # Consecutive batches in which nothing succeeded, regardless of what
        # the errors said. See the all_errored branch below.
        allerr_streak = 0
        nudged_allerr = False
        # Consecutive batches in which everything succeeded and returned
        # nothing. See the all_noinfo branch below.
        noinfo_streak = 0
        nudged_noinfo = False
        # Verify-gate: consecutive mutating edits to a file (by basename) with no
        # intervening look at ground truth. A verify bash run (py_compile/pytest/
        # python) resets ALL counters; re-reading a file resets that file's. When
        # a file crosses max_unverified_edits it earns a one-time nudge to run or
        # re-read before editing again — closing the open loop that lets a weak
        # model edit a file into a duplicated mess without ever checking. The
        # nudge gate clears on the same reset, so genuine compliance re-arms it.
        unverified_edits: dict[str, int] = {}
        nudged_verify: set[str] = set()
        # Episodic action-ledger (Lever 3): cumulative-for-the-turn tallies that,
        # unlike unverified_edits, are never reset. When a cycling nudge fires
        # (repeat-edit or verify-gate) it prepends a terse "so far this turn you
        # have: edited f.py 3×, run a check 2× (still not green)" so a model that
        # has lost the thread is reminded what it already tried. Selective by
        # construction — only attached at those already-gated moments, never every
        # turn — so it does not bloat the context or corrupt tool-call JSON.
        edit_tally: dict[str, int] = {}
        read_tally: dict[str, int] = {}
        run_count = 0
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
        # [zero-change gate] Did the request ask for the workspace to end up
        # different, and has the model done anything that could have changed it?
        # A model that answers "done" having never acted is invisible to every
        # other stop-net: with no mutating call there is no repeat, no-op or
        # error streak to accrue. Measured live (gemmacoder12 diff-report,
        # 2026-07-28): one iteration, ZERO tool calls, a self-terminated
        # "answered", and silently wrong output.
        wants_change = _asks_for_a_change(user_text)
        acted = False
        nudged_zero_change = False
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
                        # Compaction just deleted evidence from the context, so
                        # re-reading it is no longer repetition (_forgive_rereads).
                        forgiven = _forgive_rereads(repeat_streaks, nudged_repeat,
                                                    forgiven_rereads)
                        self._on_event({"phase": "info",
                                        "text": f"auto-compacted context: {report}",
                                        "rereads_forgiven": forgiven})
                        # Tell the MODEL, not just the user. Silent compaction is
                        # the other half of the ratchet: evidence vanishes from
                        # under a model that has no idea it happened, so it never
                        # consolidates and just re-reads — and re-reading costs
                        # the same space again, so it compacts again. Observed as
                        # a hard thrash (eval long-context-find): six modules
                        # totalling 88k chars against a 70k budget, read
                        # alpha..echo, compact, then alpha/bravo/alpha/charlie/
                        # alpha/charlie/delta/delta until the repeat guard
                        # stopped the turn. Bounded like the re-read forgiveness
                        # for the same reason: repeating the advice every
                        # compaction becomes noise the model tunes out.
                        if compact_notices < _MAX_COMPACT_NOTICES:
                            compact_notices += 1
                            self._notice_compacted()
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
                            _wire(self.history, profile.strict_alternation),
                            model_id, tools=tools,
                            temperature=self._cfg.model.temperature,
                            max_tokens=self._cfg.model.max_tokens,
                            frequency_penalty=self._cfg.model.frequency_penalty,
                            repetition_penalty=self._cfg.model.repetition_penalty,
                            stop=self._cfg.model.stop,
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
                    # If the wallclock ran out MID-write — the model was streaming
                    # a large document as one write_file and the turn's budget
                    # expired before it closed — that partial document is real
                    # work. Land it before stopping instead of throwing away the
                    # whole reply. Unlike the token-limit path there is no budget
                    # left to append the rest, so this only rescues the partial
                    # (a half-written design doc scores far above nothing); the
                    # finish_reason=length path is the one that completes across
                    # turns. Recorded as the clean fenced call, not the raw
                    # partial with its unclosed fence.
                    salvaged = toolparse.salvage_truncated_write(
                        e.partial, self._registry.names(),
                        self._registry.arg_names()) if e.partial else None
                    if salvaged is not None:
                        self.history.append(
                            {"role": "assistant",
                             "content": _render_calls_as_fenced([salvaged]),
                             "kind": "assistant"})
                        await self._run_calls([salvaged])
                    elif e.partial:
                        self.history.append({"role": "assistant",
                                             "content": e.partial,
                                             "kind": "assistant"})
                    # The deadline is the TURN's, not this reply's, so the same
                    # exception covers two very different runs: one reply that
                    # generated for most of the budget, and a turn that spent
                    # its budget elsewhere and tripped on the next reply before
                    # it produced anything. The old wording asserted the first
                    # in both cases — an eval row that had cycled edit_file for
                    # 600s read as "~0 chars generated during a single reply",
                    # which is a sentence that cannot be true.
                    landed = (" — landed its partial file first"
                              if salvaged is not None else "")
                    return self._stop(
                        "budget: the turn's wallclock ran out while generating "
                        f"(~{len(e.partial):,} chars into this reply){landed}")
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
                # The client cut off a degenerate token loop mid-stream (the
                # model was repeating a short phrase toward the token/wallclock
                # limit and producing nothing usable). The partial reply is
                # garbage — never record it — so discard it and nudge the model
                # to break out, bounded so a model stuck in the attractor can't
                # spin forever.
                if msg.get("finish_reason") == "repetition":
                    self._on_event({"phase": "truncated",
                                    "chars": len(content),
                                    "reason": "repetition"})
                    if repetition_aborts >= self._cfg.agent.max_repetition_aborts:
                        return self._stop("the model fell into a repetition loop "
                                          "it could not break out of")
                    repetition_aborts += 1
                    self._nudge_repetition()
                    continue
                outcome = toolparse.extract(msg, self._registry.names(),
                                            self._registry.arg_names())
                calls = outcome.calls

                # A large document written as one write_file truncates at the
                # token limit: the JSON string never closes, extract() recovers
                # nothing, and the whole partial reply is lost — the qythos9
                # "long mode writes 40k and NOTHING lands" failure. When that
                # happens, salvage the partial content and land it, then (below,
                # after it runs) steer the model to APPEND the rest. Bounded so a
                # model that keeps re-writing the same doc can't loop forever; a
                # same-content re-salvage is also caught by the repeat detector.
                if (not calls and hit_token_limit
                        and salvage_writes < self._cfg.agent.max_salvaged_writes):
                    salvaged = toolparse.salvage_truncated_write(
                        content, self._registry.names(),
                        self._registry.arg_names())
                    if salvaged is not None:
                        salvage_writes += 1
                        calls = [salvaged]
                        # Drop the truncated prose: the clean fenced call rendered
                        # below is the coherent record, and the raw reply carries
                        # an unclosed fence that would poison history.
                        content = ""

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
                    # Retry budget spent and the reply STILL ends inside an
                    # unclosed ```tool fence — the parser recovers nothing from
                    # it, so falling through to `return content` would hand the
                    # user a raw, half-written JSON block as the "final answer".
                    # Observed on devstral24 e2e runs: 5/6 ended mid-edit exactly
                    # this way. Stop cleanly instead. Scoped to the broken-fence
                    # case only — a prose reply cut mid-sentence (no dangling
                    # fence) is at least readable, so it keeps falling through.
                    if _looks_truncated(content):
                        return self._stop(
                            "the model's reply kept getting cut off mid tool "
                            "call — try a smaller step or writing less at once")
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
                    # The model ran the suite to green but never checked off its
                    # own "run the tests" task, so the plan still shows it open.
                    # The open-tasks nudge below would then tell the model to "do
                    # the work" — i.e. re-run the tests it already passed. Measured
                    # live (qythos9 add-test, 2026-07-27): green suite, plan stuck
                    # at 2/3, the model re-ran pytest every turn to a repeat-stop
                    # on a task it had finished. A green test IS that task's
                    # completion, so credit it here. Double-scoped — a green result
                    # actually appeared this turn AND the current task is
                    # run/verify-tests-shaped — so ordinary work the model merely
                    # claimed can't be completed out from under it. One-shot per
                    # task (it's DONE afterward), and it only fires on real green.
                    if (self.plan.open and self._saw_green_test
                            and _is_verify_task(self.plan.current)):
                        credited = self.plan.complete_current()
                        self._on_event({
                            "phase": "nudge",
                            "reason": "verify task credited (tests already green)",
                            "task": credited.text if credited else "",
                            "plan": self.plan.summary()})
                    # The model is stopping with tasks IT declared unfinished.
                    # Its own plan is the strongest available evidence that the
                    # turn isn't over — stronger than any heuristic below, and
                    # unlike the deliverable check it works for requests that
                    # name no output file. Bounded, because a model that can't
                    # finish a task shouldn't be nudged at it forever.
                    if (self.plan.open and open_task_nudges
                            < self._cfg.agent.max_open_task_retries):
                        open_task_nudges += 1
                        # We are about to demand more work. Don't also punish
                        # the model for doing it (see _forgive_nudged_verifies).
                        _forgive_nudged_verifies(repeat_streaks, nudged_repeat,
                                                 forgiven_nudged)
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
                    # The model is ending the turn ASSERTING the tests pass, but
                    # no green pytest result ever appeared this turn — the
                    # "tests should now pass" false-completion, which in the eval
                    # data is the single largest source of a run declaring done
                    # while checks['tests_pass'] is False. Measured across 89
                    # self-declared-done exec/e2e runs, an ever-saw-green gate
                    # caught 4/4 of these and blocked 0/85 legitimate
                    # completions (perfect discrimination). Nudge once to run the
                    # suite to green; scoped to test-specific claims so a
                    # design/plan task that never runs tests can't trip it.
                    if (not nudged_unverified_tests
                            and not self._saw_green_test
                            and _TEST_CLAIM_RE.search(content)):
                        nudged_unverified_tests = True
                        self._nudge_unverified_tests()
                        continue
                    # Sibling of the test gate above, for the compile/run/import
                    # class of check. The model ends the turn ASSERTING a named
                    # verification succeeded — "compiles cleanly", "py_compile
                    # succeeds", "syntactically correct", "runs without error" —
                    # but no code-checking command ever exited cleanly this turn.
                    # This is the hallucinated-verify false-completion: measured
                    # live (gemmacoder12 syntax-fix, 2026-07-27) the model read
                    # `def parse(line)` — a missing colon — declared "the file is
                    # syntactically correct and already compiles", marked its plan
                    # done, and self-terminated WITHOUT running py_compile, leaving
                    # the file broken. No pathology counter sees this: zero
                    # repeats, zero fails, a clean "answered" stop — only actually
                    # running the check catches it. Nudge once to run the
                    # verification the claim names. Double-gated (claim AND never
                    # saw a clean check) so a run that really did verify, or a
                    # task needing no shell check, can't trip it.
                    if (not nudged_unverified_verify
                            and not self._saw_verify_ok
                            and _VERIFY_CLAIM_RE.search(content)):
                        nudged_unverified_verify = True
                        self._nudge_unverified_verify()
                        continue
                    # [zero-change gate] The request asked for a change to a
                    # named file and the turn is ending without the model having
                    # taken a single action that could have made one. Every
                    # pathology counter is blind here by construction — they all
                    # count actions, and there were none — so this is the last
                    # thing standing between a plan-shaped monologue and a
                    # confident wrong answer.
                    #
                    # Last in the cascade on purpose: it is the broadest gate,
                    # and the specific ones above (missing deliverable, open
                    # tasks, announced intent, unverified test/compile claims)
                    # give better-targeted advice for the cases they own.
                    #
                    # `expected_artifacts` is the seam between this gate and the
                    # deliverable one, and they partition cleanly because
                    # _expected_artifacts needs a WRITE verb next to the
                    # filename: "write a PLAN.md" belongs to that gate, which
                    # already nudges and then stops naming the missing file,
                    # while "fix the bug in report.py" names nothing to create
                    # and so reaches here. Without this clause the two stack, and
                    # a model that answered a deliverable nudge in prose gets
                    # nudged twice for one mistake — caught by
                    # test_differing_prose_is_not_a_repeat, which went from a
                    # clean return to a repeat-stop.
                    #
                    # Double-gated in the shape build 50 established: the request
                    # must be change-shaped AND nothing may have run. One-shot,
                    # and it nudges rather than stops — if the model comes back
                    # and says the file already does what was asked, that answer
                    # is returned. The gate's claim is only that "I did nothing"
                    # deserves to be said twice before it is believed.
                    if (wants_change and not expected_artifacts
                            and not acted and not nudged_zero_change):
                        nudged_zero_change = True
                        self._nudge_zero_change()
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
                # [first-repeat-plan-finish] A redundant (already-seen) update_plan
                # on a plan whose tasks are ALL done is a weak model signalling
                # completion the only way it knows — re-stating its finished plan
                # instead of stopping. This case is tight enough to finish on the
                # FIRST repeat (seen_streak >= 1) rather than waiting for the
                # general repeat-stop's max_repeat_calls-1 identical calls: every
                # call an update_plan AND the plan fully complete AND this exact
                # batch already emitted once. Finishing here is honest (the model's
                # own plan says done) and avoids both an extra spin and the
                # repeat-stop's failure-toned "repeated … without making progress"
                # on work that in fact landed. Measured live (2026-07-27): gemma
                # does a 2-call restate on already-correct AND rename-across-files —
                # build 53 caught only the 3-call variant. The FIRST plan-completing
                # update_plan (seen_streak 0) still passes through: a real final
                # summary may follow it.
                if (seen_streak >= 1
                        and all(c.name == "update_plan" for c in calls)
                        and self.plan.complete):
                    self._on_event({
                        "phase": "info",
                        "text": "all planned tasks complete — finishing "
                                "(model re-stated its finished plan)",
                        "plan": self.plan.summary()})
                    return "All planned tasks are complete.\n\n" + self.plan.render()
                if seen_streak >= self._cfg.agent.max_repeat_calls - 1:
                    if batch_sig not in nudged_repeat:
                        nudged_repeat.add(batch_sig)
                        # "Try something different" is too vague for a weak model
                        # to act on — it just repeats again and burns the nudge.
                        # If the task mentioned other files this call hasn't
                        # touched yet, name them: a concrete next action is far
                        # more likely to break the loop than a generic prod.
                        unread = mentioned_files - read_paths - expected_artifacts
                        if repeat_varied.get(batch_sig) and all(
                                c.name in _MUTATING_EDIT_TOOLS for c in calls):
                            self._nudge_repeat_edit(
                                calls,
                                _ledger_line(edit_tally, read_tally, run_count,
                                             self._saw_green_test))
                        else:
                            self._nudge_repeat(calls, unread)
                        continue
                    return self._stop("the model repeated the same tool call "
                                      "without making progress")
                consecutive_malformed = 0  # progress made
                for c in calls:
                    # [zero-change gate] Anything that could have left the
                    # workspace different disarms it. bash counts even when the
                    # command only reads: it CAN mutate (sed -i, mkdir, a
                    # generator script), the loop cannot cheaply tell which, and
                    # the same reasoning already keeps bash out of
                    # _REREADABLE_TOOLS. Erring toward silence is deliberate —
                    # see _asks_for_a_change.
                    if c.name in _MUTATING_EDIT_TOOLS or c.name in ("bash",
                                                                    "move_file"):
                        acted = True
                    if c.name in ("write_file", "append_file", "edit_file"):
                        path = c.args.get("path")
                        if path:
                            attempted_paths.add(os.path.basename(str(path)).lower())
                    elif c.name == "read_file":
                        path = c.args.get("path")
                        if path:
                            read_paths.add(os.path.basename(str(path)).lower())
                    # Verify-gate bookkeeping (see unverified_edits init). A verify
                    # bash run clears every file's streak and re-arms all nudges; a
                    # re-read clears just that file's; a mutating edit grows it.
                    if c.name == "bash" and _is_verify_bash(c.args.get("cmd", "")):
                        unverified_edits.clear()
                        nudged_verify.clear()
                        run_count += 1
                    elif c.name == "read_file":
                        base = os.path.basename(str(c.args.get("path") or "")).lower()
                        unverified_edits.pop(base, None)
                        nudged_verify.discard(base)
                        if base:
                            read_tally[base] = read_tally.get(base, 0) + 1
                    elif c.name in _MUTATING_EDIT_TOOLS:
                        base = os.path.basename(str(c.args.get("path") or "")).lower()
                        if base:
                            unverified_edits[base] = unverified_edits.get(base, 0) + 1
                            edit_tally[base] = edit_tally.get(base, 0) + 1
                error_sig, result_sig, no_change, all_errored, all_noinfo = \
                    await self._run_calls(calls)
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
                # means the call did something new, so it starts over. EXCEPTION:
                # a byte-identical mutating edit (same batch_sig seen before, all
                # calls in _MUTATING_EDIT_TOOLS) is a loop even when its echo
                # differs — re-issuing the same replace_lines against a file that
                # keeps growing under it "succeeds" with a new diff each time
                # while duplicating content (the gemmacoder12 report). Don't let
                # that shifting echo reset the streak.
                same_result = result_sig == seen_result
                repeated_edit = seen_streak >= 1 and all(
                    c.name in _MUTATING_EDIT_TOOLS for c in calls)
                if seen_streak >= 1 and not same_result:
                    repeat_varied[batch_sig] = True  # results shifting under a repeat
                repeat_streaks[batch_sig] = (
                    result_sig,
                    seen_streak + 1 if (same_result or repeated_edit) else 1)
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
                # Content-INDEPENDENT sibling of the stall above. The same-error
                # streak keys on the error text, so a model that varies the
                # thing it gets wrong slips past it: guessing at filenames
                # yields a new "no such file" every iteration, and the repeat
                # guard sees genuinely-new calls too. Nine consecutive
                # all-failing iterations went unremarked that way. Nothing
                # succeeding, whatever the reason, is not progress. A single
                # success anywhere in a batch clears it.
                if all_errored:
                    allerr_streak += 1
                    if allerr_streak >= self._cfg.agent.max_consecutive_errors:
                        if not nudged_allerr:
                            nudged_allerr = True
                            self._nudge_all_errors(allerr_streak)
                            continue
                        return self._stop("every tool call kept failing — the "
                                          "model could not find its footing")
                else:
                    allerr_streak = 0
                # The success-side twin of the branch above. Nothing failed, so
                # no error-keyed guard can see it; the calls differ, so the
                # repeat guard can't either — yet the model is learning nothing
                # and will keep rephrasing the same wrong question.
                if all_noinfo:
                    noinfo_streak += 1
                    if noinfo_streak >= self._cfg.agent.max_noinfo_calls:
                        if not nudged_noinfo:
                            nudged_noinfo = True
                            self._nudge_no_information(noinfo_streak)
                            continue
                        return self._stop("every tool call kept coming back "
                                          "empty — the model never questioned "
                                          "the assumption behind them")
                else:
                    noinfo_streak = 0
                # A no-change edit (old==new, indent-only, identical replace) is
                # the model editing blind — almost always the reported error line
                # is fine and the real fault is elsewhere. Distinct from the
                # same-error stall above: nothing was even changed. One is
                # tolerated (self-correction is common); a second consecutive one
                # earns a redirect toward CONFIRMING the fault first; a third ends
                # the turn rather than letting it grind out no-op edits.
                if no_change:
                    nochange_streak += 1
                    if nochange_streak >= self._cfg.agent.max_nochange_edits:
                        if not nudged_nochange:
                            nudged_nochange = True
                            self._nudge_nochange()
                            continue
                        return self._stop("the model kept submitting edits that "
                                          "change nothing")
                else:
                    nochange_streak = 0
                # Verify-gate: a file has been edited max_unverified_edits times
                # in a row with no py_compile/pytest/python run and no re-read
                # between them. The edits DID land (this runs after _run_calls) —
                # the risk is the model can't see whether they were right and
                # keeps piling on more, the open loop behind the duplicated-mess
                # failure. Nudge once per file to look before editing again; the
                # gate re-arms as soon as it complies (bash/read clears the count).
                gate = self._cfg.agent.max_unverified_edits
                if gate > 0:
                    over = next((f for f, n in unverified_edits.items()
                                 if n >= gate and f not in nudged_verify), None)
                    if over is not None:
                        nudged_verify.add(over)
                        self._nudge_verify(
                            over, unverified_edits[over],
                            _ledger_line(edit_tally, read_tally, run_count,
                                         self._saw_green_test))
                        continue
                # A salvaged truncated write just landed a PARTIAL document. The
                # tool result reads like a normal success, so without this the
                # model would call the file done and stop. Tell it plainly the
                # write was cut off and hand it the tail to resume from, so it
                # appends the rest instead of re-writing (which would truncate
                # again) or walking away.
                salvaged = next((c for c in calls
                                 if c.source == "salvage-truncated"), None)
                if salvaged is not None:
                    self._nudge_continue_salvaged(salvaged)
                    continue
            return self._stop("budget: max iterations reached")
        except CancelledByUser:
            self.history.append({"role": "assistant", "content": "⛔ interrupted",
                                 "kind": "assistant"})
            return "⛔ interrupted"

    # --- internals -------------------------------------------------------
    async def _run_calls(self, calls) -> tuple[str | None, str, bool, bool, bool]:
        """Run the batch, feed the results back, and return two signatures plus
        a no-change flag, an all-errored flag and an all-no-information flag.

        The first is the ERROR signature — the joined content of any is_error
        results, keyed by tool name, or None if nothing errored — which the loop
        uses to detect edits that keep hitting the same failure. Denials and
        unknown-tool aren't model-fixable code errors, so they don't count
        toward the stall signal. A no-change edit is an error the model must see
        but is ALSO excluded here: "no edit happened" is a different failure
        from "the edit hit a code error", tracked on its own faster streak so it
        doesn't inflate the same-error stall or drown out a real recurring error.

        The second is the FULL result signature, errors and successes alike. It
        answers a different question: "did this exact call actually do anything
        different this time?" A repeated call whose output changes is working;
        one whose output is identical cannot make progress no matter how often
        it is retried.

        The third is True when any call in the batch was a no-change edit.

        The fourth is True when every call that actually RAN in this batch
        errored — content-independent, unlike the error signature above. That
        distinction is the whole point: a model guessing at paths produces a
        different error string every time ("no such file: …/golf.py", then
        hotel, then india), so the same-error stall never fires and the repeat
        guard never fires either, because each call is genuinely new. Nine such
        iterations in a row were observed after compaction dropped the file
        contents out from under qythos9. Whatever the errors say, a batch in
        which nothing succeeded is not progress. Denied and unknown-tool calls
        are excluded — they never reached a tool, and denials have their own
        counter.

        The fifth is the mirror image of the fourth: every call that ran
        SUCCEEDED and returned no information — an empty grep, an empty glob, an
        empty directory, a green-but-silent shell command. Every guard in this
        loop keys on failure, so a model that is wrong in a way that produces no
        errors falls through all of them. Observed live: `git ls-remote <url>
        <path>` and `git ls-tree -r HEAD <path>` against a path prefix that did
        not exist in the repo — six consecutive exit-0 empty results, four of
        them byte-identical, before the repeat guard finally ended the turn with
        nothing diagnosed. Empty output was the answer (the prefix is wrong) and
        nothing in the harness could say so."""
        ctx = ToolContext(cwd=self._cwd, cancel=self.cancel,
                          confirm=self._confirm, select=self._select,
                          plan=self.plan)
        results: list[tuple[str, str]] = []
        error_parts: list[str] = []
        no_change = False
        ran = errored = noinfo = 0
        for call in calls:
            tool = self._registry.get(call.name)
            if tool is None:
                results.append((call.name, f"error: no such tool {call.name!r}"))
                continue
            decision = self._policy.resolve(call.name, call.args, self._cwd,
                                            getattr(tool, "permission", None))
            # Why a call was refused decides both what we tell the model and
            # what the UI can show. "It just quit without prompting me" has
            # several very different causes and none of them used to leave a
            # trace: a "no (always)" answer earlier in the session disarms the
            # tool permanently, a config deny never prompts at all, and headless
            # has nobody to ask. Name it once, here.
            if decision == DENY:
                reason = ("session policy"
                          if self._policy.session_decision(call.name) == DENY
                          else "config")
            elif decision == ASK:
                decision = await self._ask(call)
                reason = ("no approver" if self._confirm is None
                          else "user declined")
            if decision == DENY:
                results.append((call.name, self._denial_text(call.name, reason)))
                self._denials += 1
                self._on_event({"phase": "denied", "name": call.name,
                                "reason": reason})
                continue
            self._on_event({"phase": "run", "name": call.name, "args": call.args})
            t0 = time.monotonic()
            # Listen for Esc while the tool runs, not just while the model
            # streams. A bash call can hold the turn for 120s with nothing
            # watching the keyboard, which is most of what "it appears stuck and
            # there is no way to stop it" actually is; Bash already registers a
            # hook that SIGTERMs the process group, so the scope is what makes
            # it reachable. Nothing may print inside the scope — raw mode drops
            # newline translation — and the run/result events bracket the call,
            # so the window is silent.
            scope = (_null_scope if getattr(tool, "prompts_user", False)
                     else self._interrupt)
            try:
                async with scope():
                    res = await tool.run(call.args, ctx)
            except (CancelledByUser, DeadlineExceeded):
                raise
            except Exception as e:
                # A tool raising anything unexpected used to end the TURN. A 9B
                # model emitting `edit_file` with no `new` field is an ordinary
                # bad call — `args["new"]` raised KeyError('new'), which escaped
                # run_turn and killed a run 19 iterations deep with the message
                # "'new'" and nothing else. The model can recover from a tool
                # error; it cannot recover from the loop exiting. Name the
                # failure and hand it back as a result like any other.
                res = ToolResult(f"{call.name} failed: {type(e).__name__}: {e}"
                                 f" — check the call's arguments against the "
                                 f"tool's schema and try again.", is_error=True)
            self._on_event({"phase": "result", "name": call.name,
                            "error": res.is_error, "content": res.content,
                            "seconds": round(time.monotonic() - t0, 3)})
            results.append((call.name, res.content))
            ran += 1
            if res.is_error:
                errored += 1
            elif _is_noinfo(res.content):
                noinfo += 1
            if call.name == "bash" and _looks_green_test(res.content):
                # A genuinely green test run appeared this turn. Restricted to
                # bash (the only tool that runs tests) so a read_file of a file
                # that happens to contain "5 passed" can't spoof it. Gates the
                # unverified-tests finish nudge in run_turn.
                self._saw_green_test = True
            if (call.name == "bash" and not res.is_error
                    and _is_verify_bash(call.args.get("cmd", ""))):
                # A code-CHECKING command (py_compile / python / ruff / ...) ran
                # and exited cleanly this turn — the model has actually watched
                # the code pass a check, not merely asserted it. Sibling of the
                # green-test flag above; gates the unverified-compile finish
                # nudge. is_error is the shell's rc!=0 signal, so a failing
                # py_compile (SyntaxError) correctly leaves this False.
                self._saw_verify_ok = True
            if getattr(res, "no_change", False):
                # A no-change edit is a real error to the model but not a
                # "same recurring code error" — keep it out of the error-stall
                # signal; the no-change streak handles it (sooner, and with the
                # right redirect).
                no_change = True
            elif res.is_error:
                error_parts.append(f"{call.name}: {res.content}")
        self.history.append({"role": "user", "content": tool_results_block(results),
                             "kind": "tool_result"})
        result_sig = "\n".join(f"{name}: {content}" for name, content in results)
        return (("\n".join(error_parts) if error_parts else None), result_sig,
                no_change, ran > 0 and errored == ran,
                ran > 0 and noinfo == ran)

    def _denial_text(self, name: str, reason: str = "user declined") -> str:
        """What a refused tool call tells the model.

        "denied by permission policy" was true and useless: it named no reason
        and implied nothing about whether trying again might work. A local
        model reading that does the obvious thing and runs a variant — `npm
        install -g x`, then without -g, then with sudo — until a stuck-detector
        ends the turn three calls later. When the refusal is permanent, say so
        in the words a model acts on.

        Only "user declined" leaves the door open: a human said no to one call
        and might say yes to a different one. The other three are settled for
        the rest of the session, and a model told to "try a different approach"
        will keep spending turns rediscovering that."""
        if reason != "user declined":
            why = {
                "no approver": "there is no one present to approve it",
                "session policy": "you were refused it earlier and that answer "
                                  "stands for the whole session",
                "config": "this session's configuration forbids it",
            }.get(reason, "it is not permitted here")
            return (f"denied: {name} is not available in this session — {why}, "
                    f"so calling it again will be refused every time. Do NOT "
                    f"retry {name}. Finish with the tools you do have, or stop "
                    f"and state plainly what you could not do without it.")
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

    def _nudge_continue_salvaged(self, call) -> None:
        path = call.args.get("path", "the file")
        landed = call.args.get("content", "")
        tail = landed[-160:]
        self.history.append({
            "role": "user",
            "content": (
                f"Your write_file was CUT OFF at the token limit — only the "
                f"first {len(landed):,} characters of {path} were saved, so the "
                f"file is INCOMPLETE. It currently ends with:\n\n…{tail}\n\n"
                f"Continue it now with a SINGLE append_file call on {path} that "
                f"adds ONLY the remaining part (everything that comes after the "
                f"text above). Do NOT call write_file again — that erases what "
                f"was saved. Do NOT repeat any text already shown above. Keep "
                f"this piece short enough to finish in one reply; if the rest is "
                f"still long, append it in several small pieces."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "continue truncated write"})

    def _nudge_repetition(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("Your last reply got stuck repeating the same text over "
                        "and over, so it was discarded. Stop repeating yourself. "
                        "Take a single concrete next step: either call ONE tool "
                        "using the ```tool format, or give a short final answer. "
                        "Do not restate anything you have already said."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "repetition loop"})

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

    def _nudge_repeat_edit(self, calls, ledger: str = "") -> None:
        names = ", ".join(dict.fromkeys(c.name for c in calls))
        self.history.append({
            "role": "user",
            "content": (ledger
                        + f"You have applied the same {names} edit repeatedly. "
                        "Re-issuing a mutating edit with the SAME arguments does "
                        "not converge: it either changes nothing, or — for a "
                        "line-number edit like replace_lines — the target lines "
                        "SHIFT after each change, so the same start/end now lands "
                        "on different text and DUPLICATES content. The file has "
                        "already been modified. STOP editing, RE-READ the file to "
                        "see its current contents, and only then make a single "
                        "corrected edit. If it already looks right, give your "
                        "final answer in plain text now — do not repeat the edit."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "repeated edit"})

    def _nudge_verify(self, filename: str, count: int, ledger: str = "") -> None:
        self.history.append({
            "role": "user",
            "content": (ledger
                        + f"You have edited {filename} {count} times in a row "
                        "without running anything or re-reading it. You cannot "
                        "tell whether those edits are correct — or whether they "
                        "duplicated or broke something — until you look at the "
                        "CURRENT state of the file. Before editing it again, "
                        f"either run it (py_compile, the test, or python {filename}) "
                        f"or re-read {filename}, then decide from what you see. "
                        "If it already does what was asked, give your final answer "
                        "in plain text instead of editing again."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "unverified edits"})

    def _nudge_nochange(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("Your edits are changing the file NOTHING — the `new` you "
                        "submit equals what is already there, so you are editing a "
                        "line you have not confirmed is actually wrong. Error "
                        "locations are frequently MISREPORTED: the tool that failed "
                        "often points at a line that is fine while the real fault is "
                        "elsewhere. Before editing again, get the current ground "
                        "truth — re-run the exact command that produced the error to "
                        "see its real location and message, or re-read the "
                        "surrounding code. Only edit once you know the exact text "
                        "that is wrong and what it should become."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "edit changed nothing"})

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

    def _nudge_all_errors(self, n: int) -> None:
        """Steer a model that is failing on every call, whatever the errors say.

        The shape this was written for: after compaction discarded the file
        contents, the model kept reading plausible-sounding paths that did not
        exist — golf.py, hotel.py, india.py — inventing the rest of the NATO
        alphabet. It needs to be told to stop guessing and go look, not to try
        harder."""
        self.history.append({
            "role": "user",
            "content": (f"Your last {n} tool calls ALL failed — nothing "
                        "succeeded. Stop and re-establish what actually "
                        "exists before calling anything else. If the failures "
                        "are missing paths, do NOT guess another filename: "
                        "list the directory (ls) or glob for the files, and "
                        "work only from names that came back. If they are "
                        "something else, read the error text and fix the call "
                        "itself rather than retrying a variant of it. If you "
                        "cannot make progress, say so in plain text now."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "every tool call failing"})

    def _nudge_no_information(self, n: int) -> None:
        """Steer a model that is getting clean, empty answers and not hearing them.

        The shape this was written for: the model read a SOURCE_PATH constant
        out of the script it was debugging and went looking for that path in
        git — `ls-remote <url> <path>`, then `ls-tree -r HEAD <path>`, then the
        same again with `2>&1`, then the same again. Every one exited 0 with no
        output, because the path prefix simply wasn't in the repo. That was the
        diagnosis, sitting in front of it, indistinguishable from silence."""
        self.history.append({
            "role": "user",
            "content": (f"Your last {n} tool calls all succeeded and all came "
                        "back empty. Empty output is a RESULT, not a failure to "
                        "run: it means nothing matched. Re-running the same "
                        "query — or the same query with a flag, a pipe, or a "
                        "redirect changed — will return empty again. The thing "
                        "that is wrong is the assumption behind the query: the "
                        "path, the ref, the directory, the pattern or the repo "
                        "you are asking about probably does not exist as you "
                        "think it does. Verify that assumption with a call that "
                        "MUST produce output if you are right — list the parent "
                        "directory, list the whole tree, print the value you are "
                        "matching on — and work from what comes back. If the "
                        "empty result is itself the answer to the task, say so "
                        "in plain text now and move on."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "every call returning empty"})

    def _notice_compacted(self) -> None:
        """Tell the model its own context was just structurally compacted.

        Deliberately not framed as a correction — nothing has gone wrong yet.
        It is the one piece of information the model cannot observe for itself
        and cannot act correctly without: that the tool output it is reasoning
        from is being deleted behind it, and that its own written words are the
        only thing that survives. Without this the model treats the gap as
        forgetfulness and re-reads, which is what makes the thrash."""
        self.history.append({
            "role": "user",
            "content": ("Context notice: this conversation exceeded its size "
                        "budget, so older tool output has been dropped. Two "
                        "consequences. First, you cannot hold every file at "
                        "once — re-reading a file you already read costs the "
                        "same space again and will just push out something "
                        "else, so do not re-read unless you have a specific "
                        "reason to doubt what you saw. Second, your own replies "
                        "survive compaction but tool output does not: after you "
                        "examine something, state what you concluded from it in "
                        "plain text — the file, the finding, and whether it is "
                        "still open — before you move to the next one. Work "
                        "through the remaining items one at a time, recording "
                        "each result as you go."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge", "reason": "context compacted"})

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

    def _nudge_unverified_tests(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("You're ending the turn on the tests passing, but no "
                        "passing test result actually appeared this turn — you "
                        "asserted it without running the suite to a green "
                        "result. Run the tests now with a bash tool call and let "
                        "the output show them passing before you stop. If they "
                        "don't pass, keep fixing until they do."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge",
                        "reason": "tests claimed passing but never seen green"})

    def _nudge_unverified_verify(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("You're ending the turn asserting the code compiles / "
                        "runs cleanly, but you never actually ran that check "
                        "this turn — you claimed it without watching it pass. "
                        "Run the exact verification now with a bash tool call "
                        "(the py_compile / python command the task named) and "
                        "read its output. If it reports an error, fix it and "
                        "re-run until the output is clean before you stop."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge",
                        "reason": "compile/run claimed ok but never verified"})

    def _nudge_zero_change(self) -> None:
        self.history.append({
            "role": "user",
            "content": ("The request asked you to change a file, and this turn "
                        "you have not run a single tool — nothing has been read, "
                        "edited or checked, so nothing in the workspace is "
                        "different. Describing the change is not making it. "
                        "Open the file with a read_file tool call, make the edit "
                        "with edit_file, and confirm the result. If you believe "
                        "no change is needed, say so explicitly and give the "
                        "specific evidence from the file that shows it."),
            "kind": "nudge",
        })
        self._on_event({"phase": "nudge",
                        "reason": "declared done without acting"})

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


def _wire(history: list[dict[str, Any]],
          merge_roles: bool = False) -> list[dict[str, Any]]:
    """The history as sent to the model server: role/content only. `history`
    entries also carry a "kind" tag (agent/compact.py's classification of
    system/user_prompt/assistant/tool_result/nudge) that's purely internal
    bookkeeping and must never leak onto the wire.

    `merge_roles` collapses consecutive same-role messages into one, for models
    whose chat template refuses them (see Profile.strict_alternation). It is
    off by default so every other model's wire format is byte-identical to
    before — nudges keep arriving as their own user turn."""
    msgs = [{"role": m["role"], "content": m["content"]} for m in history]
    return _merge_consecutive(msgs) if merge_roles else msgs


def _merge_consecutive(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join runs of same-role messages with a blank line between them.

    Content-preserving: nothing is dropped, so a nudge merged into the tool
    result it follows still reaches the model — just inside the same turn."""
    out: list[dict[str, Any]] = []
    for m in msgs:
        if out and out[-1]["role"] == m["role"]:
            joined = f"{out[-1]['content']}\n\n{m['content']}".strip()
            out[-1] = {"role": m["role"], "content": joined}
        else:
            out.append(dict(m))
    return out


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


# Verbs that ask for the WORKSPACE to end up different — a superset of
# _WRITE_VERB_RE, which only covers producing a new file. "fix", "remove" and
# "refactor" are requests for a change that name no artifact to create, and they
# are the shapes the completion gate below exists for.
_CHANGE_VERB_RE = re.compile(
    r"\b(?:fix(?:es|ing|ed)?|repair\w*|correct(?:s|ing|ed)?|add(?:s|ing|ed)?"
    r"|implement\w*|creat(?:e|es|ing)|writ(?:e|es|ing)|updat(?:e|es|ing)"
    r"|modif\w*|chang(?:e|es|ing)|edit(?:s|ing|ed)?|remov(?:e|es|ing)"
    r"|delet(?:e|es|ing)|renam(?:e|es|ing)|refactor\w*|replac(?:e|es|ing)"
    r"|insert(?:s|ing|ed)?|append(?:s|ing|ed)?|rewrit(?:e|es|ing)"
    r"|extract(?:s|ing|ed)?|split(?:s|ting)?|migrat\w*|convert\w*)\b",
    re.IGNORECASE,
)
# How far a change verb may sit from the file it acts on. Both directions:
# "fix the bug in report.py" puts the verb before, "report.py needs fixing"
# after.
_CHANGE_WINDOW = 80


def _asks_for_a_change(user_text: str) -> bool:
    """Whether the request asks for the workspace to end up different.

    Anchored on a file-like token with a change verb nearby, the same windowing
    idiom `_expected_artifacts` uses. Requiring the filename is what keeps
    "write a short summary of what this does" — a request whose deliverable is
    prose in the reply — from reading as a request to edit something. The cost
    is a narrow gate: a change request that names no file at all does not match,
    and the completion gate simply stays quiet. That is the right direction to
    be wrong in. A gate that stays quiet leaves today's behaviour; a gate that
    fires on a question wastes a turn arguing with the model about whether it
    should have edited a file the user never asked it to touch.
    """
    for m in _ARTIFACT_RE.finditer(user_text):
        window = user_text[max(0, m.start() - _CHANGE_WINDOW):
                           m.end() + _CHANGE_WINDOW]
        if _CHANGE_VERB_RE.search(window):
            return True
    return False


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


# A tool result showing a genuinely green test run: pytest's own tally line
# ("5 passed", "5 passed in 0.12s"), with no failure/error count alongside it.
_TEST_GREEN_RE = re.compile(r"\b\d+\s+passed\b", re.IGNORECASE)
_TEST_FAIL_RE = re.compile(
    r"\b\d+\s+(?:failed|error(?:s|ed)?)\b"
    r"|={2,}\s*(?:FAILURES|ERRORS)\s*={2,}"
    r"|\bFAILED\b|\bTraceback\b",
    re.IGNORECASE)


def _looks_green_test(content: str) -> bool:
    """True if a tool result reports a passing test run with no failures.

    Pytest prints a tally line — "5 passed in 0.12s" — on success; a mixed run
    prints "3 passed, 1 failed". Requiring a passed-count AND the absence of any
    failure/error token keeps a partial run from reading as green."""
    if not content or not _TEST_GREEN_RE.search(content):
        return False
    return not _TEST_FAIL_RE.search(content)


# A final answer that CLAIMS the tests pass. Deliberately test-specific — "tests"
# within a short window of a pass verb — so a design-doc or plan task, which
# never runs tests, cannot trip the seen-green finish gate; it fires only on
# language asserting a passing test outcome.
_TEST_CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:the\s+)?tests?(?:\s+suite)?\b[^.\n]{0,40}?\b"
    r"(?:pass(?:es|ed|ing)?|are\s+passing|succeed(?:s|ed)?|green)\b",
    re.IGNORECASE)


# A final answer that CLAIMS a NON-test code check succeeded (test claims go
# through _TEST_CLAIM_RE above). Covers the compile/run/import class:
# "compiles cleanly", "py_compile succeeds", "syntactically correct", "no
# syntax error", "runs/imports without error". The precision here matters less
# than for the test regex because the gate is double-locked: it fires only when
# _saw_verify_ok is ALSO False, i.e. the model asserted the check passed having
# never watched any code-checking command exit cleanly this turn — the
# hallucinated-verify signature. A run that actually verified can't trip it
# however it phrases the result.
_VERIFY_CLAIM_RE = re.compile(
    r"(?:"
    r"\bcompiles?\b(?:\s+(?:clean(?:ly)?|success(?:fully)?|correctly|fine|now|ok))?"
    r"|\bpy_?compile\b[^.\n]{0,30}?(?:succe|pass|clean|work|ok)\w*"
    r"|\bsyntactically\s+(?:correct|valid)\b"
    r"|\bno\s+syntax\s+errors?\b"
    r"|\b(?:runs?|imports?|executes?)\b[^.\n]{0,20}?"
    r"\b(?:without\s+(?:error|issue|problem)s?|clean(?:ly)?|success(?:fully)?)\b"
    r")",
    re.IGNORECASE)


# A plan task that a passing test run SATISFIES — the "run the suite and verify
# it's green" kind of task. Requires an action verb (run/verify/confirm/…) near a
# test noun, OR a "…tests pass" phrasing. The verb is what keeps "Create
# test_primes.py with pytest tests" (a test FILE to write, not a run) from
# matching: it has the test noun but no run/verify verb. Used only to credit such
# a task as done when a green pytest result already appeared this turn, so the
# scoping mirrors _saw_green_test — nothing here completes non-test work.
_VERIFY_TASK_RE = re.compile(
    r"\b(?:run|runn|verif|confirm|ensur|check|execut|make)\w*\b[^\n]*?"
    r"\b(?:test\w*|pytest|suite|spec\w*)\b"
    r"|\b(?:test\w*|pytest|suite)\b[^\n]*?"
    r"\b(?:pass(?:es|ed|ing)?|green|succeed(?:s|ed)?)\b",
    re.IGNORECASE)


def _is_verify_task(task) -> bool:
    """Whether `task` is a run/verify-the-tests task a green suite completes."""
    return task is not None and bool(_VERIFY_TASK_RE.search(task.text))


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


def _forgive_rereads(repeat_streaks: dict, nudged_repeat: set,
                     forgiven_counts: dict) -> int:
    """Drop the repeat streaks of read-only batches after compaction. Returns
    how many were forgiven.

    Two guards that are each right on their own were fighting: compaction
    replaces a tool result with "output omitted — re-read or re-run if you need
    it", and the repeat guard then stops the turn for making that identical read
    again. The model does exactly what the context tells it to and gets killed
    for it — observed live on build 58 (a run that had already completed its
    edit correctly, then repeat-stopped re-reading three files whose output had
    been compacted away). The streak is only meaningful while the *result* is
    still in context to have been learned from; once it isn't, the call is new
    information, not a repeat.

    Scoped to _REREADABLE_TOOLS. A repeated mutating edit is never progress no
    matter what the context looks like (build 42's duplicating replace_lines),
    and bash can mutate, so neither is ever forgiven.

    Bounded per signature. Forgiving unconditionally *disarms* the repeat guard
    in exactly the regime it is needed most: when compaction fires often, every
    firing wipes the streaks, and a genuine read loop never accumulates one.
    Measured on the long-context case at a 70k budget — 7 compactions, 18
    forgiven re-reads, 16 repeats, 23 iterations and no answer. Re-reading a
    file whose output was compacted away is legitimate once or twice; a third
    time is a loop, and the guard should be allowed to see it."""
    stale = [sig for sig in repeat_streaks
             if sig and all(name in _REREADABLE_TOOLS for name, _ in sig)
             and forgiven_counts.get(sig, 0) < _MAX_FORGIVEN_REREADS]
    for sig in stale:
        repeat_streaks.pop(sig, None)
        nudged_repeat.discard(sig)  # re-arm the nudge, don't stop on sight
        forgiven_counts[sig] = forgiven_counts.get(sig, 0) + 1
    return len(stale)


def _forgive_nudged_verifies(repeat_streaks: dict, nudged_repeat: set,
                             forgiven_counts: dict) -> int:
    """Drop the repeat streaks of read-only/verify batches after we push the
    model back to its own open plan tasks. Returns how many were forgiven.

    The same two-guards-fighting shape as _forgive_rereads, from the other
    direction. Live transcript (build 65, empty-query-diagnosis): the model
    edited the script, ran `python3 sync.py`, and got the correct output — the
    task was DONE and verified. But its plan still had "test the fix by running
    the script again" open, so the open-tasks nudge fired and told it to finish.
    The only action that closes that task is re-running the script. It did, and
    the repeat guard stopped the turn for "repeating the same tool call without
    making progress". Two of three runs ended that way — reported as a failure,
    on a task whose fix had already landed and passed.

    A nudge that demands more work must not leave the model in a state where the
    only compliant action is punished. So the call we just asked for stops
    counting as a repeat.

    Scoped harder than _forgive_rereads: read-only tools, plus bash ONLY when it
    looks like a verify (_is_verify_bash — runs/compiles/tests, never a mutating
    shell command). And bounded at one forgiveness per signature, tighter than
    the compaction case: there, an external event really had deleted the
    evidence; here nothing was lost, so a second identical re-run after we have
    already excused one is a genuine loop."""
    def _safe(sig) -> bool:
        for name, argjson in sig:
            if name in _REREADABLE_TOOLS:
                continue
            if name == "bash":
                try:
                    cmd = json.loads(argjson).get("cmd", "")
                except (ValueError, AttributeError):
                    return False
                if _is_verify_bash(cmd):
                    continue
            return False
        return True

    stale = [sig for sig in repeat_streaks
             if sig and _safe(sig)
             and forgiven_counts.get(sig, 0) < _MAX_FORGIVEN_NUDGED]
    for sig in stale:
        repeat_streaks.pop(sig, None)
        nudged_repeat.discard(sig)
        forgiven_counts[sig] = forgiven_counts.get(sig, 0) + 1
    return len(stale)


def _render_calls_as_fenced(calls) -> str:
    """Render parsed tool calls back into the fenced ```tool format we teach, so
    a turn that arrived as native tool_calls (empty content) still leaves a
    coherent, self-describing assistant message in the resent history."""
    blocks = []
    for c in calls:
        payload = json.dumps({"name": c.name, "args": c.args}, ensure_ascii=False)
        blocks.append(f"```tool\n{payload}\n```")
    return "\n".join(blocks)


def _ledger_line(edit_tally: dict, read_tally: dict, run_count: int,
                 saw_green: bool) -> str:
    """A terse one-line recap of what this turn has done so far — attached to a
    cycling nudge so a model that has lost track is reminded of its own history.
    Returns "" when there is nothing worth reciting (a single edit, no runs)."""
    items: list[str] = []
    for f, n in edit_tally.items():
        if n >= 2:
            items.append(f"edited {f} {n}×")
    for f, n in read_tally.items():
        if n >= 2:
            items.append(f"re-read {f} {n}×")
    if run_count >= 1:
        note = "" if saw_green else " (still not green)"
        items.append(f"run a check {run_count}×{note}")
    if not items:
        return ""
    return "So far this turn you have: " + ", ".join(items) + ". "


def _is_verify_bash(cmd) -> bool:
    """Does this bash command look like it CHECKS the code (runs/compiles/tests)
    rather than just poking around? Used by the verify-gate to credit the model
    with having closed the loop. Deliberately generous — a false positive only
    delays a nudge, while requiring an exact command would miss legitimate ways
    to verify. Excludes pure inspection (ls/cat/grep) which sees text, not
    behavior.

    `cmd` is normally a string, but a weak model sometimes emits it as a LIST of
    argv tokens (["python3", "-m", "py_compile", "x.py"]) — join those rather
    than crashing on `.lower()`. Measured live (gemmacoder12, verify-gate A/B,
    2026-07-27): the nudge to run py_compile prompted a list-form bash call and
    the bare `.lower()` raised `'list' object has no attribute 'lower'`, killing
    the run. This runs before the tool executes, so it must tolerate whatever the
    parser handed through."""
    if isinstance(cmd, (list, tuple)):
        cmd = " ".join(str(x) for x in cmd)
    c = str(cmd or "").lower()
    return any(tok in c for tok in (
        "pytest", "py_compile", "unittest", "python", "python3",
        "ruff", "mypy", "pyflakes", "pylint", "compileall", "-m compile"))


def _preview(call) -> str:
    if call.name == "bash":
        return call.args.get("cmd", "")
    if call.name in ("write_file", "append_file", "edit_file"):
        return call.args.get("path", "")
    return ", ".join(f"{k}={v!r}" for k, v in call.args.items())[:200]
