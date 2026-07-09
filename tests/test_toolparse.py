from locode.model.toolparse import extract

KNOWN = {"read_file", "edit_file", "bash", "ls"}


def test_native_tool_calls_with_string_arguments():
    msg = {
        "content": "",
        "tool_calls": [
            {"id": "c1", "function": {"name": "read_file",
                                      "arguments": '{"path": "a.py"}'}}
        ],
    }
    out = extract(msg, KNOWN)
    assert len(out.calls) == 1
    c = out.calls[0]
    assert c.name == "read_file" and c.args == {"path": "a.py"}
    assert c.id == "c1" and c.source == "native"


def test_native_with_dict_arguments():
    msg = {"tool_calls": [{"function": {"name": "ls", "arguments": {"path": "."}}}]}
    out = extract(msg, KNOWN)
    assert out.calls[0].args == {"path": "."}


def test_native_takes_priority_over_fence():
    msg = {
        "content": '```tool\n{"name": "bash", "args": {"cmd": "ls"}}\n```',
        "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path":"x"}'}}],
    }
    out = extract(msg, KNOWN)
    # Only the native call — the fence is not also executed.
    assert [c.name for c in out.calls] == ["read_file"]


def test_fenced_tool_block():
    msg = {"content": 'Sure.\n```tool\n{"name": "edit_file", '
                      '"args": {"path": "a", "old": "x", "new": "y"}}\n```'}
    out = extract(msg, KNOWN)
    assert len(out.calls) == 1
    assert out.calls[0].name == "edit_file"
    assert out.calls[0].source == "fenced"


def test_fenced_json_array_of_calls():
    msg = {"content": '```json\n[{"name":"ls","args":{}}, '
                      '{"name":"read_file","args":{"path":"a"}}]\n```'}
    out = extract(msg, KNOWN)
    assert [c.name for c in out.calls] == ["ls", "read_file"]


def test_arguments_key_alias_accepted():
    # Some models emit "arguments" instead of "args" in a fence.
    msg = {"content": '```tool\n{"tool": "bash", "arguments": {"cmd": "pwd"}}\n```'}
    out = extract(msg, KNOWN)
    assert out.calls[0].name == "bash" and out.calls[0].args == {"cmd": "pwd"}


def test_flat_schema_args_inlined_next_to_name():
    # Some models (e.g. Qwen3.6 coder) inline args at the top level instead of
    # nesting under "args": {"name":"read_file","path":"a"}. Lift them in.
    msg = {"content": '```tool\n{"name": "read_file", "path": "a"}\n```'}
    out = extract(msg, KNOWN)
    assert len(out.calls) == 1
    assert out.calls[0].name == "read_file"
    assert out.calls[0].args == {"path": "a"}


def test_flat_schema_multiple_args_and_id_excluded():
    msg = {"content": '```tool\n{"name": "edit_file", "id": "1", '
                      '"path": "f", "old": "x", "new": "y"}\n```'}
    out = extract(msg, KNOWN)
    assert out.calls[0].args == {"path": "f", "old": "x", "new": "y"}
    assert out.calls[0].id == "1"


def test_nested_args_still_win_over_stray_top_level_keys():
    # A real envelope must take precedence; stray top-level keys are ignored.
    msg = {"content": '```tool\n{"name": "edit_file", "args": {"path": "f"}, '
                      '"path": "WRONG"}\n```'}
    out = extract(msg, KNOWN)
    assert out.calls[0].args == {"path": "f"}


def test_salvage_bare_json_object():
    msg = {"content": 'I will run {"name": "ls", "args": {"path": "."}} now.'}
    out = extract(msg, KNOWN)
    assert len(out.calls) == 1
    assert out.calls[0].name == "ls" and out.calls[0].source == "salvage"


def test_salvage_ignores_unknown_names():
    msg = {"content": 'random {"name": "not_a_tool", "args": {}} text'}
    out = extract(msg, KNOWN)
    assert out.calls == []
    assert out.malformed == []  # silent: not a real call


def test_malformed_fence_is_reported_not_raised():
    msg = {"content": '```tool\n{"name": "bash", "args": {oops not json}\n```'}
    out = extract(msg, KNOWN)
    assert out.calls == []
    assert out.malformed and "unparseable" in out.malformed[0]


def test_bare_code_fence_is_not_a_tool_call():
    # A code model illustrating a change in a plain ``` fence must NOT be parsed
    # as a botched tool call (this was the "Expecting value char 0" noise).
    msg = {"content": "Let's apply this change:\n```\n"
                      "if is_full(self.game.board) and not self.winner:\n```"}
    out = extract(msg, KNOWN)
    assert out.calls == [] and out.malformed == []


def test_illustration_fence_plus_real_tool_call(tmp_path):
    # Prose + a ``` illustration + a real ```tool call -> just the call, no noise.
    msg = {"content": "Here's the change:\n```\nx = 2\n```\n"
                      '```tool\n{"name": "edit_file", "args": '
                      '{"path": "a.py", "old": "x = 1", "new": "x = 2"}}\n```'}
    out = extract(msg, KNOWN)
    assert [c.name for c in out.calls] == ["edit_file"]
    assert out.malformed == []


def test_tool_fence_with_prefix_is_salvaged():
    # Tool name / comment before the JSON inside a ```tool fence is salvaged.
    msg = {"content": '```tool\nedit_file\n{"name": "edit_file", "args": '
                      '{"path": "a", "old": "x", "new": "y"}}\n```'}
    out = extract(msg, KNOWN)
    assert [c.name for c in out.calls] == ["edit_file"]


def test_plain_text_yields_no_calls():
    out = extract({"content": "Here is the answer, no tools needed."}, KNOWN)
    assert not out.found_anything


def test_unknown_native_name_is_malformed():
    msg = {"tool_calls": [{"function": {"name": "frobnicate", "arguments": "{}"}}]}
    out = extract(msg, KNOWN)
    assert out.calls == []
    assert any("frobnicate" in m for m in out.malformed)


def test_without_known_names_skips_salvage():
    # No known set -> can't safely salvage bare JSON.
    msg = {"content": '{"name": "ls", "args": {}}'}
    out = extract(msg, known_names=None)
    assert out.calls == []


# --- relaxed recovery: weak models that mis-escape code in tool JSON ---------
# These are the dominant qwencoder30 / weak-local-model failure: `old`/`new`/
# `content` carry code with UNESCAPED interior quotes (and sometimes newlines),
# which breaks strict JSON. The key-anchored recovery reads each value up to the
# next known argument key, keeping interior quotes literal.

def test_recovers_unescaped_interior_quotes():
    # `new` contains  return " " not in self.board  — the bare quotes around the
    # space break strict JSON; recovery must keep them and parse cleanly.
    body = ('{"name": "edit_file", "args": {"path": "t.py", '
            '"old": "    return self.board == 9", '
            '"new": "    return " " not in self.board", '
            '"replace_all": false}}')
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert len(out.calls) == 1 and not out.malformed
    c = out.calls[0]
    assert c.name == "edit_file" and c.source == "salvage"
    assert c.args["path"] == "t.py"
    assert c.args["new"] == '    return " " not in self.board'
    assert c.args["replace_all"] is False


def test_recovers_multiple_interior_quotes_and_escaped_newlines():
    body = ('{"name": "edit_file", "args": {"path": "g.py", '
            '"old": "x = 1", '
            '"new": "    self.turn = "O" if self.turn == "X" else "X"\\n    return True"}}')
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert len(out.calls) == 1 and not out.malformed
    assert out.calls[0].args["new"] == (
        '    self.turn = "O" if self.turn == "X" else "X"\n    return True')


def test_recovery_requires_a_known_tool_name():
    # Mis-escaped JSON naming an unknown tool must NOT be conjured into a call.
    body = '{"name": "frobnicate", "args": {"x": "a "b" c"}}'
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert out.calls == []
    assert out.malformed  # reported, not silently dropped


def test_strict_json_still_preferred_over_recovery():
    # Well-formed JSON must parse as "fenced", not fall through to salvage.
    body = '{"name": "read_file", "args": {"path": "ok.py"}}'
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert out.calls[0].source == "fenced"


def test_write_file_with_interior_code_fence_not_truncated():
    # A write_file whose `content` is a Markdown doc containing its OWN ```python
    # code fence must round-trip as a single call. The interior ``` must not be
    # read as the end of the ```tool block — the "DESIGN.md stops at 22 lines"
    # bug, where the non-greedy fence regex truncated the write at the first
    # interior fence and the model then flailed, chunking into smaller writes.
    doc = ("# Design\\n\\n## Overview\\n\\nThe scraper does X.\\n\\n"
           "```python\\ndef fetch(url):\\n    return get(url)\\n```\\n\\n"
           "## Notes\\n\\nDone.")
    body = ('{"name": "write_file", "args": '
            '{"path": "DESIGN.md", "content": "' + doc + '"}}')
    out = extract({"content": "```tool\n" + body + "\n```"}, {"write_file"})
    assert len(out.calls) == 1 and not out.malformed
    c = out.calls[0]
    assert c.name == "write_file" and c.source == "fenced"
    # The full body survived, interior fence and all.
    assert "```python" in c.args["content"]
    assert c.args["content"].endswith("Done.")


def test_interior_json_fence_inside_content_not_a_block_boundary():
    # Harder variant: the file content contains a ```json block (same tag family
    # our fence uses). String-aware scanning must still keep it inside the value.
    doc = ('Config example:\\n\\n```json\\n'
           '{\\"port\\": 8081}\\n```\\n\\nEnd.')
    body = ('{"name": "write_file", "args": '
            '{"path": "README.md", "content": "' + doc + '"}}')
    out = extract({"content": "```tool\n" + body + "\n```"}, {"write_file"})
    assert len(out.calls) == 1 and not out.malformed
    assert out.calls[0].args["content"].endswith("End.")
