"""update_plan — the model's own checklist for the current turn.

The tool is a thin door onto `agent.plan.Plan`; the reasoning for why the plan
exists at all lives there. What matters here is the *shape* of the call, which
is chosen for the weakest model that has to emit it: one argument, a flat array
of strings, no IDs, no nesting, no partial updates. Every call replaces the
whole list.

The description below is prompt engineering as much as documentation — it is
what a 9B model reads when deciding whether this turn needs a plan at all.
"""

from __future__ import annotations

import json

from locode.agent.plan import MAX_TASKS, Plan, has_status_marker
from locode.tools.base import ToolContext, ToolResult


class UpdatePlan:
    name = "update_plan"
    description = (
        "Record or update your task list for this request. Call it FIRST on any "
        "request with more than one step (e.g. 'design it, plan it, then build "
        "it'), then call it again each time a task's state changes.\n"
        "Pass the COMPLETE list every time — it replaces the previous one. Mark "
        "each task with a leading status: '[x] ' finished, '[>] ' currently "
        "working on it, '[ ] ' not started. Keep tasks concrete and verifiable "
        "('write DESIGN.md', 'make test_stats.py pass'), not vague ('improve "
        "things'). Exactly one task should be '[>]' at a time."
    )
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": ("The full task list, in order, each prefixed "
                                "with [x], [>] or [ ]."),
            },
        },
        "required": ["tasks"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        plan: Plan | None = getattr(ctx, "plan", None)
        if plan is None:
            return ToolResult("no plan is available in this context",
                              is_error=True)
        raw = args.get("tasks")
        # Double-wrap: some models nest the whole call shape inside the argument
        # and send {"tasks": {"tasks": [...]}} (or with a string inside).
        # Measured 2026-07-25 (qythos9 exec-bugfix) — the nested dict was
        # hard-rejected, the model resent the identical shape, and the run
        # stall-died *after already solving the task*. Unwrap a single-key
        # {"tasks": X} dict to X and carry on with the value.
        if isinstance(raw, dict) and set(raw) == {"tasks"}:
            raw = raw["tasks"]
        if isinstance(raw, str):
            # Models sometimes send a newline-joined string instead of an array.
            # Recovering it costs a few lines and saves an iteration.
            #
            # But a string that OPENS like JSON is a different animal: it is a
            # mangled array/object, not prose, and adopting it whole is worse
            # than rejecting it. Measured 2026-07-22 — a model sent the truncated
            # fragment `["[>] Write DESIGN.md — the approach` and the old code
            # took it as a single task. It had no status marker, so it parsed as
            # open, could never be marked done, and the loop's completion gate
            # then refused every final answer for the rest of the turn. The run
            # produced nothing and scored 0.00. Same failure mode measured
            # 2026-07-25 with the object form `{"tasks": "[ ] run tests"` (the
            # inner half of a double-wrap the model then truncated): it fell
            # through to the newline split and became one bogus task. A plan that
            # cannot be completed is a turn-killer, so recover what parses and
            # fail loudly on the rest.
            text = raw.strip()
            parsed = None
            if text[:1] in "[{":
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
            # A parsed object is the double-wrap again, one layer down as a
            # string: {"tasks": [...]} or {"tasks": "..."}. Pull the value out.
            if isinstance(parsed, dict) and "tasks" in parsed:
                parsed = parsed["tasks"]
            if isinstance(parsed, list):
                raw = parsed
            elif isinstance(parsed, str):
                raw = [p for p in parsed.replace("\r", "").split("\n")
                       if p.strip()]
            elif text.startswith("[") and not has_status_marker(text):
                # Opens like a JSON array, didn't parse as one, and isn't a task
                # line either. `has_status_marker` rather than the marker regex:
                # the regex matches `["[>] Write…` with a marker group of `"[>`,
                # which is exactly how the fragment got adopted in the first
                # place.
                return ToolResult(
                    "`tasks` looks like a JSON array but did not parse — it may "
                    "have been cut off. Send it as a real array of strings, each "
                    "starting with [x], [>] or [ ].", is_error=True)
            elif text.startswith("{"):
                # Opens like a JSON object but we couldn't recover a task list
                # from it. Don't adopt the raw JSON as a single bogus task (that
                # poisons the completion gate); tell the model the real shape.
                return ToolResult(
                    "`tasks` looks like a JSON object, but it must be a plain "
                    "array of task strings — e.g. [\"[>] first task\", "
                    "\"[ ] second task\"]. Do not wrap it in another object.",
                    is_error=True)
            else:
                raw = [p for p in text.replace("\r", "").split("\n") if p.strip()]
        if not isinstance(raw, list) or not raw:
            return ToolResult(
                "update_plan needs a non-empty `tasks` array of strings, each "
                "starting with [x], [>] or [ ]", is_error=True)

        before = plan.signature()
        plan.replace(raw)
        if not plan.tasks:
            return ToolResult("no usable tasks in that list", is_error=True)

        lines = [f"Plan updated ({plan.summary()}):", plan.render()]
        if len(raw) > MAX_TASKS:
            lines.append(f"(kept the first {MAX_TASKS} tasks)")
        # Say what to do next explicitly. A bare "ok" invites the model to
        # narrate the plan back at the user and stop, which is the exact
        # dead-end the plan was added to prevent.
        current = plan.current
        if plan.complete:
            lines.append("All tasks are done. Give your final answer now.")
        elif current is not None:
            if before == plan.signature() and plan.revisions > 1:
                lines.append("Nothing changed status since the last update — "
                             "stop revising the plan and do the work.")
            lines.append(f"Next: {current.text}. Do it now — do not reply with "
                         "the plan.")
        return ToolResult("\n".join(lines))
