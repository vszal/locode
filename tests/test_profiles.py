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


def test_qwythos_suppresses_thinking():
    # Qwythos-9B is a reasoning model: without enable_thinking=false it emits a
    # long `reasoning` field before any content, which locode can't see and which
    # read as multi-minute hangs. The profile MUST send the thinking kwarg so the
    # server launches with it disabled. (Regression guard — see profiles.py.)
    p = profiles.profile_for("sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx")
    assert p.thinking_arg is True
    assert p.native_tools is False   # fenced-only (template tool protocol conflicts)


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


# --- per-model thinking override (config [thinking] table) -----------------

def test_thinking_override_matches_alias_exactly():
    ov = {"qythos9": "on", "gpt-oss": "off"}
    assert profiles.lookup_thinking_override(ov, "org/Qwythos-9B", "qythos9") == "on"


def test_thinking_override_matches_id_substring():
    ov = {"gpt-oss": "off"}
    assert profiles.lookup_thinking_override(ov, "org/gpt-oss-20b-mlx") == "off"


def test_thinking_override_prefers_longest_id_key():
    # A specific id fragment should win over a broad one.
    ov = {"Qwen3": "on", "Qwen3-14B": "off"}
    got = profiles.lookup_thinking_override(ov, "mlx-community/Qwen3-14B-4bit")
    assert got == "off"


def test_thinking_override_none_when_no_match():
    assert profiles.lookup_thinking_override({"foo": "off"}, "org/Bar", "bar") is None
    assert profiles.lookup_thinking_override({}, "org/Bar") is None


def test_resolve_thinking_override_forces_value():
    # An on/off override fully decides, regardless of the profile default.
    suppress = profiles.profile_for("org/Qwythos-9B")  # thinking_arg=True
    assert profiles.resolve_thinking(suppress, "on") is True
    assert profiles.resolve_thinking(suppress, "off") is False


def test_resolve_thinking_auto_omits_overriding_profile():
    # "auto" suppresses the kwarg even when the profile would have sent false —
    # the user's escape hatch to fall back to the template default.
    suppress = profiles.profile_for("org/Qwythos-9B")
    assert suppress.thinking_arg is True
    assert profiles.resolve_thinking(suppress, "auto") is None


def test_resolve_thinking_unset_falls_back_to_profile():
    suppress = profiles.profile_for("org/Qwythos-9B")        # -> send false
    plain = profiles.profile_for("mlx-community/Qwen2.5-Coder-14B-Instruct-4bit")
    assert profiles.resolve_thinking(suppress, None) is False
    assert profiles.resolve_thinking(plain, None) is None
