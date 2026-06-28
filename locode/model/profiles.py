"""Per-model capability profiles.

Ports the model-specific knowledge from the offload repo's `mlx-server.sh`:
the `enable_thinking` chat-template kwarg, per-model prompt-cache (wired memory)
budgets, and — new for an agentic tool — how reliably each model tool-calls,
which decides how hard the harness leans on native tool_calls vs. the fenced
fallback (see model/toolparse.py).

Profiles are matched against the *resolved* full model id by substring, so they
apply whether the user passed an alias or a full id.
"""

from __future__ import annotations

from dataclasses import dataclass

GB = 1024 ** 3
GB_1_5 = 3 * GB // 2  # 1.5GB as an int (byte counts must not be floats)


@dataclass(frozen=True)
class Profile:
    native_tools: bool       # trust server-rendered tool_calls for this model?
    thinking_arg: bool       # send {"enable_thinking": false} chat-template arg?
    prompt_cache_bytes: int  # per-model wired-memory budget (Apple single-GPU)
    tool_reliability: str    # "good" | "fair" | "poor"
    notes: str = ""


# Matched in order; first substring hit wins. Keep most-specific first.
_RULES: list[tuple[str, Profile]] = [
    # Qwen3 Instruct-2507 dropped the thinking toggle -> must NOT send the kwarg.
    ("Instruct-2507", Profile(True, False, GB_1_5, "fair",
                              "fast 4B first-pass; strict format")),
    ("Qwen2.5-Coder", Profile(True, False, GB_1_5, "good",
                              "code / structured output; tool-calls well")),
    # Qwen3-Coder MoE (30B total / ~3B active): agentic-coding tuned, strong
    # tool-caller, non-thinking. ~17GB of 4-bit weights -> tight cache budget.
    # Must precede the generic "Qwen3" rule below.
    ("Qwen3-Coder", Profile(True, False, 1 * GB, "good",
                            "agentic coding MoE (30B/3B active); strong tool-caller")),
    # Qwen3.6-27b-coder (e.g. chaddy81 / OptiQ 4-bit, ~14GB): a SHARP diagnoser —
    # caught the subtle tangled-state bug devstral missed twice. But like Devstral
    # its template carries a native (Hermes-style XML) tool protocol that COLLIDES
    # with our fenced prompt when the `tools` param is passed: it emits a garbled
    # hybrid of fenced JSON + <tool_call>/<function>/<parameter=> and loses edits.
    # native_tools off -> fenced only -> clean. Must precede the generic "Qwen3".
    ("Qwen3.6", Profile(False, True, 1 * GB, "good",
                        "27B Qwen3.6 coder; strong diagnoser, clean fenced "
                        "JSON (flat arg schema — needs the toolparse flat-key "
                        "lift); native template conflicts so fenced only")),
    # Mistral's coding-agent model (24B dense, non-thinking). Do NOT pass the
    # OpenAI `tools` param: mlx_lm renders it into Mistral's [AVAILABLE_TOOLS]
    # protocol, which conflicts with our fenced ```tool prompt and makes the
    # model return EMPTY. With native_tools off it uses the fenced format — and
    # emits clean, correctly-escaped JSON (verified live).
    ("Devstral", Profile(False, False, GB_1_5, "good",
                         "Mistral agentic coder; fenced only (native tools "
                         "conflict w/ its template); clean JSON")),
    ("Qwen3-14B", Profile(True, True, GB_1_5, "good",
                          "best dense Qwen; reliable tool-caller")),
    ("Qwen3-0.6B", Profile(False, True, GB_1_5, "poor",
                           "tiny; trivial tasks only")),
    ("Qwen3", Profile(True, True, GB_1_5, "fair", "Qwen3 family")),
    ("phi-4", Profile(True, False, GB_1_5, "fair",
                      "fast non-Qwen lineage")),
    # gemma-3-text-27b: 15GB weights -> tighter 1GB cache reservation.
    ("gemma-3-text-27b", Profile(False, False, 1 * GB, "poor",
                                 "27B; strong reasoner, weak tool-caller")),
    ("gemma-4-12b", Profile(False, True, GB_1_5, "poor",
                            "reasoning model; may not tool-call cleanly")),
]

# Used when no rule matches: conservative — don't trust native tools, no
# thinking kwarg (sending it to a model that rejects it fails the request).
DEFAULT_PROFILE = Profile(
    native_tools=False, thinking_arg=False, prompt_cache_bytes=GB_1_5,
    tool_reliability="poor", notes="unknown model; conservative defaults",
)


def profile_for(model_id: str) -> Profile:
    """Return the capability profile for a resolved full model id."""
    for needle, profile in _RULES:
        if needle in model_id:
            return profile
    return DEFAULT_PROFILE
