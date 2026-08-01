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
    # Bound, not an exact length: the summary carries wording that steers the
    # model away from reflexively re-reading (see _shrink_tool_result), which
    # costs a couple of lines. The point is 10k chars -> a sentence.
    assert len(shrunk["content"]) < 260


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
    # Bound, not an exact length: the summary carries wording that steers the
    # model away from reflexively re-reading (see _shrink_tool_result), which
    # costs a couple of lines. The point is 10k chars -> a sentence.
    assert len(shrunk["content"]) < 260


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


# --- the ratchet ------------------------------------------------------------
# A wrong conclusion must never outlive, or outnumber, the evidence it rested
# on. See the module docstring in agent/compact.py for the reported session.

STALE = ("The staging logic is already implemented in the file. The stage_change "
         "function handles new, modified and deleted files by creating diff "
         "files in the _staged-diffs directory. The script is complete.")


def _ratchet_history(copies=6):
    """The reported shape: a real question, big tool dumps, and the model
    restating one stale conclusion after each of them."""
    history = [_sys(), _user("the repos differ but the script doesn't detect it")]
    for _ in range(copies):
        history.append(_tool_result("Tool results:\n\n[bash]\n" + ("out\n" * 400)))
        history.append(_asst(STALE))
    return history


def test_repeated_prose_reply_collapses_to_one_annotated_copy():
    new, _ = compact_history(_ratchet_history(6), keep_recent=8)
    survivors = [m for m in new if m.get("kind") == "assistant"]
    assert len(survivors) == 1
    assert "sent 6 times" in survivors[0]["content"]
    assert survivors[0]["content"].startswith("The staging logic")


def test_dedupe_reaches_into_the_recent_window():
    # Confining it to the pre-window region capped growth but still handed the
    # model keep_recent/2 fresh copies of the stale claim -- the failure itself.
    new, _ = compact_history(_ratchet_history(4), keep_recent=8)
    assert sum(1 for m in new if m.get("kind") == "assistant") == 1


def test_distinct_replies_are_never_collapsed():
    history = [_sys(), _user("go")] + [_asst(STALE + f" Variation {i}.")
                                       for i in range(5)]
    new, _ = compact_history(history, keep_recent=2)
    assert sum(1 for m in new if m.get("kind") == "assistant") == 5
    assert not any("was sent" in (m.get("content") or "") for m in new)


def test_short_acknowledgements_are_not_collapsed():
    # "Done." repeated is noise, not a stale conclusion -- annotating it would
    # be noise-for-noise and the marker's wording would be wrong.
    history = [_sys(), _user("go")] + [_asst("Done.") for _ in range(5)]
    new, _ = compact_history(history, keep_recent=2)
    assert sum(1 for m in new if m.get("kind") == "assistant") == 5


def test_replies_carrying_a_tool_call_are_never_collapsed():
    # Dropping one would break the call/result pairing the history is threaded
    # on; a genuinely repeated CALL is loop.py's repeat guard, not compaction's.
    call = _write_call(path="a.py", size=10)
    history = [_sys(), _user("go")] + [dict(call) for _ in range(5)]
    new, _ = compact_history(history, keep_recent=2)
    assert sum(1 for m in new if m.get("kind") == "assistant") == 5


def test_repeat_count_merges_across_passes():
    once, _ = compact_history(_ratchet_history(6), keep_recent=8)
    twice, _ = compact_history(once + [_asst(STALE)] * 3, keep_recent=2)
    survivors = [m for m in twice if m.get("kind") == "assistant"]
    assert len(survivors) == 1
    assert "sent 9 times" in survivors[0]["content"]


def test_second_pass_over_a_deduped_history_is_a_noop():
    once, _ = compact_history(_ratchet_history(6), keep_recent=8)
    twice, report = compact_history(once, keep_recent=8)
    assert twice == once
    assert "nothing to compact" in report


def test_repeat_marker_survives_truncation_on_a_later_pass():
    # The marker is appended AFTER shrinking, so a naive second pass would
    # truncate it back off and silently un-annotate a still-stale claim.
    long_stale = STALE + (" padding." * 200)
    history = ([_sys(), _user("go")]
               + [_asst(long_stale) for _ in range(4)]
               + [_asst(f"filler {i}") for i in range(4)])
    once, _ = compact_history(history, keep_recent=2)
    twice, _ = compact_history(once, keep_recent=2)
    kept = [m for m in twice if "was sent" in (m.get("content") or "")]
    assert len(kept) == 1
    assert "sent 4 times" in kept[0]["content"]


def test_collapsed_tool_output_does_not_endorse_the_stale_conclusion():
    big_read = "Tool results:\n\n[read_file]\n" + ("x" * 2000)
    history = ([_sys(), _user("look at a.py")] + [_tool_result(big_read)]
               + [_asst(f"step {i}") for i in range(10)])
    new, _ = compact_history(history, keep_recent=3)
    shrunk = next(m for m in new if m.get("kind") == "tool_result"
                  and "compacted" in m["content"])
    assert "already used earlier" not in shrunk["content"]
    assert "conclusion" in shrunk["content"]
