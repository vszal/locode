"""Mistral-family templates refuse two same-role messages in a row.

Devstral's chat template raises `TemplateError: After the optional system
message, conversation roles must alternate user and assistant roles...`, and
mlx_lm funnels every generate() exception into a bare HTTP 404. locode sends
tool results AND nudges as role "user", so a nudge landing after a tool result
killed the turn mid-session and surfaced as an API/connectivity error.

Two guards here: the wire-level merge (only for models that need it), and the
client surfacing the server's actual explanation instead of the status line.
"""

import httpx
import pytest

from locode.agent.loop import _merge_consecutive, _wire
from locode.model.client import ModelClient, ModelServerError
from locode.model.profiles import profile_for


def _hist(*pairs):
    return [{"role": r, "content": c, "kind": "x"} for r, c in pairs]


# --- the merge ---------------------------------------------------------------
def test_merge_joins_consecutive_user_messages():
    # The exact reproduced shape: tool result then nudge, both role "user".
    out = _merge_consecutive([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": "reading"},
        {"role": "user", "content": "tool results: ok"},
        {"role": "user", "content": "You replied with an empty message."},
    ])
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[-1]["content"] == ("tool results: ok\n\n"
                                  "You replied with an empty message.")


def test_merge_joins_consecutive_assistant_messages():
    # The truncated-write path appends assistant twice in a row.
    out = _merge_consecutive([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "partial"},
        {"role": "assistant", "content": "rest"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[-1]["content"] == "partial\n\nrest"


def test_merge_collapses_a_long_run():
    out = _merge_consecutive([{"role": "user", "content": c} for c in "abcd"])
    assert out == [{"role": "user", "content": "a\n\nb\n\nc\n\nd"}]


def test_merge_leaves_alternating_untouched():
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u2"}]
    assert _merge_consecutive(msgs) == msgs


def test_merge_never_drops_content():
    src = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    joined = _merge_consecutive(src)[0]["content"]
    for m in src:
        assert m["content"] in joined


def test_merge_does_not_mutate_input():
    src = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    _merge_consecutive(src)
    assert src == [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]


# --- _wire gating ------------------------------------------------------------
def test_wire_default_is_unchanged():
    # Eval baselines depend on this: every non-Mistral model's wire format must
    # be byte-identical to before the fix, nudges still their own user turn.
    h = _hist(("user", "a"), ("user", "b"), ("assistant", "c"))
    assert _wire(h) == [{"role": "user", "content": "a"},
                        {"role": "user", "content": "b"},
                        {"role": "assistant", "content": "c"}]


def test_wire_merges_when_asked():
    h = _hist(("user", "a"), ("user", "b"), ("assistant", "c"))
    assert _wire(h, True) == [{"role": "user", "content": "a\n\nb"},
                              {"role": "assistant", "content": "c"}]


def test_wire_still_strips_the_kind_tag_when_merging():
    for m in _wire(_hist(("user", "a"), ("user", "b")), True):
        assert set(m) == {"role", "content"}


# --- profile gating ----------------------------------------------------------
def test_devstral_requires_alternation():
    p = profile_for("mlx-community/Devstral-Small-2-24B-Instruct-2512-4bit")
    assert p.strict_alternation is True


@pytest.mark.parametrize("model_id", [
    "sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx",
    "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit",
    "mlx-community/Qwen3-14B-4bit",
])
def test_other_models_are_not_gated(model_id):
    # Verified against the real cached templates: these accept consecutive
    # same-role messages, so they must keep the untouched wire format.
    assert profile_for(model_id).strict_alternation is False


# --- the client surfaces WHY -------------------------------------------------
def _client(status, content):
    return ModelClient("http://127.0.0.1:8081",
                       transport=httpx.MockTransport(
                           lambda req: httpx.Response(status, content=content)))


async def test_template_error_reports_the_real_cause():
    # The body mlx_lm actually returned in the reproduced failure.
    msg = ("After the optional system message, conversation roles must "
           "alternate user and assistant roles except for tool calls and results.")
    c = _client(404, b'{"error": "%s"}' % msg.encode())
    with pytest.raises(ModelServerError) as ei:
        await c.complete([{"role": "user", "content": "hi"}], "m")
    assert "roles must alternate" in str(ei.value)
    assert ei.value.status == 404


async def test_error_detail_falls_back_to_raw_text():
    c = _client(500, b"upstream exploded")
    with pytest.raises(ModelServerError) as ei:
        await c.complete([{"role": "user", "content": "hi"}], "m")
    assert "upstream exploded" in str(ei.value)


async def test_empty_body_still_reports_the_status():
    c = _client(404, b"")
    with pytest.raises(ModelServerError) as ei:
        await c.complete([{"role": "user", "content": "hi"}], "m")
    assert ei.value.status == 404 and "404" in str(ei.value)


async def test_nested_openai_error_shape_is_unwrapped():
    c = _client(400, b'{"error": {"message": "context length exceeded"}}')
    with pytest.raises(ModelServerError) as ei:
        await c.complete([{"role": "user", "content": "hi"}], "m")
    assert "context length exceeded" in str(ei.value)


async def test_success_path_is_untouched():
    body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\ndata: [DONE]\n'
    c = _client(200, body)
    msg = await c.complete([{"role": "user", "content": "hi"}], "m")
    assert msg["content"] == "hi"
