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
