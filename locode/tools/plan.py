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
        if isinstance(raw, str):
            # Models sometimes send a newline-joined string instead of an array.
            # Recovering it costs three lines and saves an iteration.
            #
            # But a string that OPENS like a JSON array is a different animal: it
            # is a mangled array, not prose, and adopting it whole is worse than
            # rejecting it. Measured 2026-07-22 — a model sent the truncated
            # fragment `["[>] Write DESIGN.md — the approach` and the old code
            # took it as a single task. It had no status marker, so it parsed as
            # open, could never be marked done, and the loop's completion gate
            # then refused every final answer for the rest of the turn. The run
            # produced nothing and scored 0.00. A bad plan that
            # cannot be completed is a turn-killer, so fail loudly and let the
            # model retry with a real array.
            text = raw.strip()
            parsed = None
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
            if isinstance(parsed, list):
                raw = parsed
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
