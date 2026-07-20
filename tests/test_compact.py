import json

from locode.agent.compact import compact_history, estimate_chars


def _sys():
    return {"role": "system", "content": "sys prompt", "kind": "system"}


def _user(text):
    return {"role": "user", "content": text, "kind": "user_prompt"}


def _asst(text):
    return {"role": "assistant", "content": text, "kind": "assistant"}


def _tool_result(text):
    return {"role": "user", "content": text, "kind": "tool_result"}


def _nudge(text="you replied with an empty message, try again"):
    return {"role": "user", "content": text, "kind": "nudge"}


def _write_call(path="a.py", size=2000):
    payload = json.dumps({"name": "write_file",
                          "args": {"path": path, "content": "y" * size}})
    return {"role": "assistant", "content": f"```tool\n{payload}\n```",
            "kind": "assistant"}


def test_empty_history_is_a_noop():
    new, report = compact_history([])
    assert new == []
    assert "nothing to compact" in report


def test_keep_recent_gte_body_is_a_noop():
    history = [_sys(), _user("hi"), _asst("hello")]
    new, report = compact_history(history, keep_recent=8)
    assert new == history
    assert "nothing to compact" in report


def test_system_message_always_kept_verbatim():
    history = [_sys()] + [_asst(f"step {i}") for i in range(20)]
    new, _ = compact_history(history, keep_recent=2)
    assert new[0] == _sys()


def test_nudges_are_dropped_once_outside_recent_window():
    history = ([_sys(), _user("do the thing"), _nudge()]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=3)
    assert not any(m.get("kind") == "nudge" for m in new)


def test_user_prompts_kept_verbatim():
    prompt = "fix the bug in a.py, it's a subtle indentation issue"
    history = ([_sys(), _user(prompt)]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=2)
    assert any(m["content"] == prompt for m in new)


def test_file_change_receipt_kept_verbatim():
    receipt = "Tool results:\n\n[write_file]\nwrote a.py (12 lines)"
    history = ([_sys(), _user("write a.py")]
               + [_tool_result(receipt)]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=3)
    assert any(m["content"] == receipt for m in new)


def test_stale_read_dump_collapses_to_summary():
    big_read = "Tool results:\n\n[read_file]\n" + ("x" * 2000)
    history = ([_sys(), _user("look at a.py")]
               + [_tool_result(big_read)]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=3)
    shrunk = next(m for m in new if m.get("kind") == "tool_result"
                  and "compacted" in m["content"])
    assert "read_file" in shrunk["content"]
    assert len(shrunk["content"]) < 200


def test_large_write_file_arg_is_shrunk_but_shape_kept():
    history = ([_sys(), _user("write a big file")]
               + [_write_call(size=2000)]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=3)
    shrunk = next(m for m in new if m.get("kind") == "assistant"
                  and "write_file" in m["content"] and "chars omitted" in m["content"])
    assert "a.py" in shrunk["content"]        # shape (tool name, path) kept
    assert "y" * 100 not in shrunk["content"]  # bulk gone


def test_recent_window_left_untouched_below_the_oversize_threshold():
    big_read = "Tool results:\n\n[read_file]\n" + ("x" * 2000)
    history = [_sys(), _user("go"), _tool_result(big_read)]
    new, report = compact_history(history, keep_recent=8)  # window >= body, small -> no-op
    assert new[-1]["content"] == big_read
    assert "nothing to compact" in report


def test_recent_window_still_shrinks_a_single_oversized_dump():
    # The bug this guards against: a fresh session that reads one huge file
    # produces very few messages, so message-count alone put the entire body
    # inside the "recent window" and /compact reported "nothing to compact"
    # no matter how many chars that one dump held.
    huge_read = "Tool results:\n\n[read_file]\n" + ("x" * 10000)
    history = [_sys(), _user("read big.py"), _tool_result(huge_read)]
    new, report = compact_history(history, keep_recent=8)
    assert "nothing to compact" not in report
    shrunk = next(m for m in new if m.get("kind") == "tool_result")
    assert "compacted" in shrunk["content"]
    assert len(shrunk["content"]) < 200


def test_recent_window_shrinks_oversized_assistant_call_too():
    call = _write_call(size=5000)
    history = [_sys(), _user("write it"), call]
    new, report = compact_history(history, keep_recent=8)
    assert "nothing to compact" not in report
    shrunk = next(m for m in new if m.get("kind") == "assistant")
    assert "chars omitted" in shrunk["content"]


def test_recent_file_change_receipt_untouched_regardless_of_window():
    receipt = "Tool results:\n\n[write_file]\nwrote a.py (12 lines)"
    history = [_sys(), _user("write a.py"), _tool_result(receipt)]
    new, _ = compact_history(history, keep_recent=8)
    assert any(m["content"] == receipt for m in new)


def test_idempotent_second_pass_is_a_cheap_noop():
    big_read = "Tool results:\n\n[read_file]\n" + ("x" * 2000)
    history = ([_sys(), _user("go")]
               + [_tool_result(big_read)]
               + [_asst(f"step {i}") for i in range(10)])
    once, _ = compact_history(history, keep_recent=3)
    twice, report2 = compact_history(once, keep_recent=3)
    assert estimate_chars(twice) <= estimate_chars(once)
    assert once[:len(once) - 3] == twice[:len(twice) - 3]


def test_legacy_untagged_messages_classified_structurally():
    # No "kind" tag at all (a session saved before this feature existed).
    history = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "do the thing"},
    ] + [
        {"role": "user", "content": ("You replied with an empty message. "
                                     "Either call a tool...")}
        for _ in range(10)
    ] + [
        {"role": "assistant", "content": f"step {i}"} for i in range(10)
    ]
    new, _ = compact_history(history, keep_recent=3)
    assert not any("empty message" in m["content"] for m in new[:-3])


def test_report_mentions_message_and_char_counts():
    big_read = "Tool results:\n\n[read_file]\n" + ("x" * 2000)
    history = ([_sys(), _user("go")]
               + [_tool_result(big_read)]
               + [_asst(f"step {i}") for i in range(10)])
    _, report = compact_history(history, keep_recent=3)
    assert "->" in report and "messages" in report and "chars" in report
