"""System-prompt assembly and conversation context.

The system prompt is kept stable and first so the server's prompt cache reuses
its prefix across turns. It always teaches the fenced ```tool fallback format —
even for native-tool-capable models — so the tolerant parser has a second
channel to fall back on when native calling misbehaves.
"""

from __future__ import annotations

from typing import Any

from locode.tools.base import Registry

_FENCE_INSTRUCTIONS = """\
When you need to use a tool, emit EXACTLY ONE fenced block per call:

```tool
{"name": "<tool_name>", "args": { ... }}
```

Rules:
- Emit the block and nothing else when calling a tool; wait for the result.
- Use only the tools listed below, with the exact argument names given.
- After you have everything you need, reply normally with no tool block.
- Do not invent tools or call a tool that isn't listed."""


def build_system_prompt(registry: Registry, cwd: str, extra: str = "") -> str:
    lines = [
        "You are locode, a concise agentic coding assistant running on a local "
        "model. You help with software tasks in the user's working directory.",
        f"Working directory: {cwd}",
        "",
        "# Tools",
        _tool_catalog(registry),
        "",
        _FENCE_INSTRUCTIONS,
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def _tool_catalog(registry: Registry) -> str:
    out = []
    for spec in registry.specs():
        fn = spec["function"]
        params = fn.get("parameters", {}).get("properties", {})
        req = set(fn.get("parameters", {}).get("required", []))
        argbits = ", ".join(
            f"{k}{'*' if k in req else ''}" for k in params
        )
        out.append(f"- {fn['name']}({argbits}): {fn['description']}")
    return "\n".join(out) or "(no tools)"


def tool_results_block(results: list[tuple[str, str]]) -> str:
    """Render tool results as a user-turn block (uniform for native + fenced
    paths, and template-safe for local chat templates)."""
    chunks = ["Tool results:"]
    for name, content in results:
        chunks.append(f"\n[{name}]\n{content}")
    return "\n".join(chunks)
