from locode.model import profiles


def test_qwen_coder_is_tool_reliable():
    p = profiles.profile_for("mlx-community/Qwen2.5-Coder-14B-Instruct-4bit")
    assert p.native_tools is True
    assert p.tool_reliability == "good"
    assert p.thinking_arg is False  # Qwen2.5 is not a thinking model


def test_qwen3_14b_sends_thinking_arg():
    p = profiles.profile_for("mlx-community/Qwen3-14B-4bit")
    assert p.thinking_arg is True
    assert p.tool_reliability == "good"


def test_instruct_2507_does_not_send_thinking_arg():
    # The 2507 Instruct variant rejects the enable_thinking kwarg.
    p = profiles.profile_for("mlx-community/Qwen3-4B-Instruct-2507-4bit")
    assert p.thinking_arg is False


def test_qwen3_coder_30b_wins_over_generic_qwen3():
    # The Qwen3-Coder rule must match before the generic "Qwen3" rule.
    p = profiles.profile_for("mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit")
    assert p.native_tools is True
    assert p.thinking_arg is False           # Qwen3-Coder is non-thinking
    assert p.prompt_cache_bytes == profiles.GB  # tight: ~17GB MoE weights


def test_devstral_uses_fenced_not_native_tools():
    # Passing the OpenAI tools param makes mlx_lm render Mistral's tool protocol,
    # which conflicts with our fenced prompt and returns EMPTY — so native_tools
    # must be OFF; devstral emits clean fenced JSON on its own. (Verified live.)
    p = profiles.profile_for("lmstudio-community/Devstral-Small-2507-MLX-4bit")
    assert p.native_tools is False
    assert p.thinking_arg is False
    assert p.tool_reliability == "good"


def test_gemma27_gets_tight_cache_budget():
    p = profiles.profile_for("mlx-community/gemma-3-text-27b-it-4bit")
    assert p.prompt_cache_bytes == profiles.GB  # 1GB, tighter than the 1.5GB default
    assert p.tool_reliability == "poor"


def test_unknown_model_is_conservative():
    p = profiles.profile_for("some/Unknown-Model")
    assert p is profiles.DEFAULT_PROFILE
    assert p.native_tools is False
