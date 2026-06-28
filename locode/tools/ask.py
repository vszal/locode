"""ask_user — model-initiated multiple-choice question to the human.

The local analogue of Claude Code's AskUserQuestion: the model proposes a
question + options, the harness renders a selector (ctx.select), and the choice
returns as the tool result. In headless mode (no selector) the tool declines.
"""

from __future__ import annotations

from locode.tools.base import ToolContext, ToolResult


class AskUser:
    name = "ask_user"
    description = ("Ask the user a multiple-choice question and get their "
                   "selection. Use when you need a decision only the user can make.")
    permission = "auto"
    schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 choices to present.",
            },
        },
        "required": ["question", "options"],
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        question = args.get("question", "")
        options = [str(o) for o in args.get("options", []) if str(o).strip()]
        if not question or not options:
            return ToolResult("ask_user needs a question and options", is_error=True)
        if ctx.select is None:
            return ToolResult("no interactive user available to answer", is_error=True)
        choice = await ctx.select(question, options)
        return ToolResult(f"User selected: {choice}")
