"""Tool data model + registry.

A Tool is a small async-capable unit the agent can invoke. ToolCall is the
normalized representation the harness produces from either the native
`tool_calls` channel or the fenced-block fallback (see model/toolparse.py);
ToolResult is what comes back and is appended to the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = ""
    source: str = "native"  # "native" | "fenced" | "salvage"


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # Optional richer display for the UI (e.g. a diff); falls back to content.
    display: str | None = None
    # An edit that ran but left the file byte-for-byte unchanged (old==new, an
    # indent-only match, an identical replace_lines). It IS an error the model
    # must react to, but a *distinct kind*: not "the edit hit a code error" —
    # "no edit happened at all". The loop tracks these on their own fast streak
    # (a blind guess at a line that's actually fine) rather than lumping them
    # into the same-error stall, so the redirect comes sooner and says the right
    # thing. See loop.py's no-change handling.
    no_change: bool = False

    @property
    def ok(self) -> bool:
        return not self.is_error


@dataclass
class ToolContext:
    """Ambient state handed to every tool invocation."""
    cwd: str
    cancel: Any = None             # agent.cancel.CancelToken (avoid import cycle)
    confirm: Callable[..., Any] | None = None  # permission/editor hook
    select: Callable[..., Any] | None = None   # model-initiated multiple-choice
    plan: Any = None               # agent.plan.Plan — the turn's task list
    # Paths whose contents the model has actually seen this session, owned by
    # the loop so it survives across iterations. `None` disables the
    # read-before-edit gate entirely — which is the default here, so a tool
    # constructed without a loop (every unit test) behaves as it always did.
    seen_files: set[str] | None = None


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    schema: dict[str, Any]
    permission: str  # default gate: "auto" | "ask" | "deny"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


@dataclass
class Registry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def arg_names(self) -> set[str]:
        """Every argument key across all tool schemas. The tolerant parser uses
        these as anchors to recover tool calls whose JSON the model mis-escaped
        (e.g. code with unescaped quotes/newlines in `old`/`new`/`content`)."""
        keys: set[str] = set()
        for t in self._tools.values():
            keys.update((t.schema.get("properties") or {}).keys())
        return keys

    def signatures(self) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
        """Per-tool ``(required, accepted)`` argument-key sets.

        The tolerant parser uses these to recover a tool call whose JSON is
        well-formed but carries no `name` — a real and dominant weak-model
        failure (all 24 unnamed fenced objects in the b93 sweep were
        `{"tasks": [...]}`, an `update_plan` missing its name, which killed 8
        of 24 runs). A key set that only ONE tool can accept identifies it.
        """
        sigs: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
        for t in self._tools.values():
            props = frozenset((t.schema.get("properties") or {}).keys())
            sigs[t.name] = (frozenset(t.schema.get("required") or ()), props)
        return sigs

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI-style `tools` array for the model request / system prompt."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in self._tools.values()
        ]
