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


def test_single_quoted_value_decodes_escapes():
    # qythos9 switches to Python-style single quotes whenever the value contains
    # a " (so it can avoid escaping) — and then its \n are ESCAPES meaning
    # newlines. Left as a bare token they landed as a literal '…\n…' string and
    # corrupted every multi-line write. Recovery must strip the ' delimiters and
    # decode the escapes to real newlines.
    body = ("{\"name\": \"edit_file\", \"args\": {\"path\": \"s.py\", "
            "\"old\": \"x = 1\", "
            "\"new\": '    \"\"\"Run it.\"\"\"\\n    print(\"hi\")'}}")
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert len(out.calls) == 1 and not out.malformed
    assert out.calls[0].args["new"] == '    """Run it."""\n    print("hi")'


def test_single_quoted_value_keeps_interior_apostrophe_literal():
    # An interior ' that is NOT a structural boundary must stay part of the value
    # (mirrors the double-quote handling), so contractions/possessives survive.
    body = ("{\"name\": \"edit_file\", \"args\": {\"path\": \"s.py\", "
            "\"old\": \"a\", \"new\": 'it' + 's fine'}}")
    out = extract({"content": "```tool\n" + body + "\n```"}, KNOWN)
    assert len(out.calls) == 1 and not out.malformed
    assert out.calls[0].args["new"] == "it' + 's fine"


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


def test_second_call_survives_single_quoted_value_with_odd_double_quotes():
    # Live qythos9 flailing bug: it emitted read_file AND edit_file back-to-back
    # in one message, with the edit_file `new` a Python-style SINGLE-quoted value
    # carrying an ODD number of interior double-quotes (triple-quote docstrings,
    # f-strings). _closing_fence tracked only `"`, so string state desynced, the
    # real closing ``` looked like string interior, and the edit_file block was
    # dropped as "truncated" — the fix never ran and the loop stalled.
    read = '{"name": "read_file", "args": {"path": "x.py"}}'
    # single-quoted `new` with an odd count of " (three of them):
    edit = ('{"name": "edit_file", "args": {"path": "x.py", '
            '''"old": '    """doc""""""', "new": '    """doc"""'}}''')
    # back-to-back fences, glued (six backticks), exactly as the model emitted:
    content = "```tool\n" + read + "\n``````tool\n" + edit + "\n```\n"
    out = extract({"content": content},
                  {"read_file", "edit_file"}, {"path", "old", "new"})
    assert not out.malformed
    names = [c.name for c in out.calls]
    assert names == ["read_file", "edit_file"], names
    e = out.calls[1]
    assert e.args["old"] == '    """doc""""""'
    assert e.args["new"] == '    """doc"""'


def test_closing_fence_tracks_single_quote_string_context():
    from locode.model.toolparse import _closing_fence
    # A ``` inside a single-quoted value with interior double-quotes is NOT a
    # closer; the real closer sits after the value ends.
    body = ('''{"name": "write_file", "args": {"path": "a", '''
            '''"content": 'x = """```py"""'}}''')
    text = body + "\n```\ntrailer"
    idx = _closing_fence(text, 0)
    assert idx is not None
    assert text[idx:idx + 3] == "```"
    # the closer is the one AFTER the value, not the interior ```py
    assert "```py" in text[:idx]


def test_closing_fence_recovers_when_unterminated_string_swallows_it():
    # Build 37 root cause: qythos9's single-quoted `new` DROPPED its closing '
    # entirely, leaving trailing `}}`. The unterminated string then ran to EOF
    # and swallowed the real ``` fence, so the whole edit_file call vanished and
    # the turn ended with the fix unexecuted. _closing_fence must remember the
    # first ``` seen inside an unterminated string and hand it back at EOF.
    from locode.model.toolparse import _closing_fence
    body = ('{"name": "edit_file", "args": {"path": "w.py", '
            # note: `new` opens ' but never closes it — the } } that follow are
            # literal, and the ``` below sits INSIDE the still-open string.
            '''"new": 'return f"{a}x{b}"}}''')
    text = body + "\n```\ntrailer"
    idx = _closing_fence(text, 0)
    assert idx is not None
    assert text[idx:idx + 3] == "```"


def test_unterminated_single_quote_edit_recovers_clean_call():
    # End-to-end of the above: the exact failure shape (single-quoted `new`
    # missing its closing ', trailing }}) must recover a usable edit_file whose
    # `new` is the code WITHOUT the leaked structural }} tail.
    edit = ('{"name": "edit_file", "args": {"path": "w.py", '
            '''"old": 'def describe(w):\\n    """doc""""""', '''
            '''"new": 'def describe(w):\\n    """doc"""\\n    return w.name}}''')
    content = "```tool\n" + edit + "\n```\n"
    out = extract({"content": content},
                  {"edit_file"}, {"path", "old", "new"})
    assert not out.malformed
    assert [c.name for c in out.calls] == ["edit_file"]
    new = out.calls[0].args["new"]
    assert not new.endswith("}}")
    assert new == 'def describe(w):\n    """doc"""\n    return w.name'


def test_strip_structural_tail():
    from locode.model.toolparse import _strip_structural_tail
    # leaked closers that unbalance the value's OWN brackets are trimmed...
    assert _strip_structural_tail('return f"{a}x{b}"}}') == 'return f"{a}x{b}"'
    assert _strip_structural_tail('return {"a": 1}}}') == 'return {"a": 1}'
    assert _strip_structural_tail('items = [1, 2]]') == 'items = [1, 2]'
    # ...but balanced content and truncated-open partials are left untouched
    # (the latter matters so salvage_truncated_write still fires on them).
    assert _strip_structural_tail('x = {"a": 1}') == 'x = {"a": 1}'
    assert _strip_structural_tail('def f():\n    x = {') == 'def f():\n    x = {'


# --- salvage_truncated_write: recover a write cut off at the token limit ------

from locode.model.toolparse import salvage_truncated_write

WRITE_KNOWN = {"write_file", "append_file", "edit_file", "read_file", "bash"}
WRITE_ARGS = {"path", "content", "old", "new", "replace_all", "command"}


def test_salvage_recovers_a_truncated_write_file():
    # A big document written in one shot: the content string never closes because
    # the token limit cut it off. extract() recovers nothing; salvage lands it.
    body = ('Here is the design.\n```tool\n{"tool": "write_file", '
            '"path": "design.md", "content": "# Design\\n\\n' + "x" * 3000)
    assert extract({"content": body, "finish_reason": "length"},
                   WRITE_KNOWN, WRITE_ARGS).calls == []
    call = salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS)
    assert call is not None
    assert call.name == "write_file"
    assert call.args["path"] == "design.md"
    assert call.args["content"].startswith("# Design")
    assert len(call.args["content"]) > 3000
    assert call.source == "salvage-truncated"


def test_salvage_recovers_a_truncated_append_file():
    body = '```tool\n{"tool": "append_file", "path": "d.md", "content": "## S2\\n' + "y" * 500
    call = salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS)
    assert call is not None and call.name == "append_file"
    assert call.args["path"] == "d.md"


def test_salvage_ignores_a_complete_write():
    # A CLOSED fence is a normal, complete call — extract() handles it, so salvage
    # must not also fire (that would double-run the write).
    body = '```tool\n{"tool": "write_file", "path": "d.md", "content": "hello"}\n```'
    assert salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_refuses_a_truncated_edit_file():
    # A half-formed edit_file `new` is unsafe to apply — do not salvage it.
    body = '```tool\n{"tool": "edit_file", "path": "d.py", "old": "foo", "new": "' + "z" * 500
    assert salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_refuses_a_truncated_bash():
    body = '```tool\n{"tool": "bash", "command": "rm -rf ' + "a" * 500
    assert salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_refuses_a_tiny_partial():
    # Below the content floor: likely an opened-but-empty fence, not a document.
    body = '```tool\n{"tool": "write_file", "path": "d.md", "content": "hi'
    assert salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_refuses_a_write_without_a_path():
    body = '```tool\n{"tool": "write_file", "content": "' + "q" * 500
    assert salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_returns_none_when_no_fence_is_open():
    assert salvage_truncated_write("just prose, no tool call at all",
                                   WRITE_KNOWN, WRITE_ARGS) is None


def test_salvage_targets_the_truncated_call_after_a_complete_one():
    # A complete write in a closed fence, THEN a second write cut off. Salvage
    # must recover the second (the unclosed one), not the first.
    body = ('```tool\n{"tool": "write_file", "path": "a.md", "content": "done"}\n```\n'
            'Now the big one:\n```tool\n{"tool": "write_file", "path": "b.md", '
            '"content": "' + "w" * 800)
    call = salvage_truncated_write(body, WRITE_KNOWN, WRITE_ARGS)
    assert call is not None and call.args["path"] == "b.md"


# --- name inference from argument keys (build 94) ---------------------------
# All 24 unnamed fenced objects in the b93 sweep were `{"tasks": [...]}` — an
# update_plan missing its name. The parser nudged, the model re-emitted the
# identical bytes, and 8 of 24 runs died "unparseable". These cover the
# recovery and, more importantly, its refusals.

from locode.model.toolparse import infer_tool_name  # noqa: E402
from locode.tools import build_registry  # noqa: E402

SIGS = build_registry().signatures()
ALL = set(SIGS)


def _fenced(body: str) -> dict:
    return {"content": f"```json\n{body}\n```"}


def test_the_update_plan_call_that_killed_eight_runs():
    msg = _fenced('{"tasks": ["[x] run pytest", "[>] fix textkit.py"]}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.malformed
    assert len(out.calls) == 1
    c = out.calls[0]
    assert c.name == "update_plan"
    assert c.args == {"tasks": ["[x] run pytest", "[>] fix textkit.py"]}
    assert c.source == "fenced+inferred"


def test_an_unnamed_edit_is_inferred_from_path_old_new():
    msg = _fenced('{"path": "a.py", "old": "x = 1", "new": "x = 2"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert [c.name for c in out.calls] == ["edit_file"]
    assert out.calls[0].args["old"] == "x = 1"


def test_optional_keys_still_infer():
    msg = _fenced('{"path": "a.py", "old": "x", "new": "y", "replace_all": true}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert [c.name for c in out.calls] == ["edit_file"]


def test_a_nested_args_envelope_without_a_name_is_inferred():
    msg = _fenced('{"args": {"cmd": "pytest -q"}}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert [c.name for c in out.calls] == ["bash"]


def test_an_ambiguous_key_set_is_refused():
    # {"path"} fits both read_file and ls — guessing would run the wrong tool.
    msg = _fenced('{"path": "a.py"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.calls
    assert out.malformed and "missing a name" in out.malformed[0]


def test_write_and_append_stay_ambiguous():
    msg = _fenced('{"path": "a.py", "content": "hello"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.calls


def test_a_missing_required_argument_blocks_inference():
    # `new` alone is in edit_file and replace_lines, but satisfies neither.
    msg = _fenced('{"new": "x = 2"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.calls


def test_a_foreign_key_blocks_inference():
    # Real JSON data that happens to be fenced must not become a call.
    msg = _fenced('{"cmd": "pytest", "shell": "zsh"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.calls


def test_an_empty_object_is_not_a_call():
    out = extract(_fenced("{}"), ALL, tool_signatures=SIGS)
    assert not out.calls


def test_inference_is_off_without_signatures():
    msg = _fenced('{"tasks": ["a"]}')
    out = extract(msg, ALL)
    assert not out.calls and out.malformed


def test_bare_json_in_prose_is_never_inferred():
    # Tier-3 salvage stays name-only: unfenced prose JSON is usually data.
    msg = {"content": 'Here is the plan:\n{"tasks": ["[ ] do it"]}\n'}
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert not out.calls


def test_an_explicit_name_always_wins_over_inference():
    msg = _fenced('{"name": "ls", "path": "src"}')
    out = extract(msg, ALL, tool_signatures=SIGS)
    assert [c.name for c in out.calls] == ["ls"]
    assert out.calls[0].source == "fenced"


def test_the_nudge_says_how_to_fix_it():
    out = extract(_fenced('{"path": "a.py"}'), ALL, tool_signatures=SIGS)
    assert '"name"' in out.malformed[0]


def test_infer_tool_name_directly():
    assert infer_tool_name(["tasks"], SIGS) == "update_plan"
    assert infer_tool_name(["cmd"], SIGS) == "bash"
    assert infer_tool_name(["src", "dst"], SIGS) == "move_file"
    assert infer_tool_name(["pattern"], SIGS) is None      # glob or grep
    assert infer_tool_name([], SIGS) is None
