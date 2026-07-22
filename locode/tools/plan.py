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

from locode.agent.plan import MAX_TASKS, Plan
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
            # Models sometimes send a newline- or comma-joined string instead of
            # an array. Recovering it costs three lines and saves an iteration.
            raw = [p for p in raw.replace("\r", "").split("\n") if p.strip()]
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
