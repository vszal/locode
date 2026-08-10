import pytest
from pathlib import Path

from locode.tools import fs
from locode.tools.base import ToolContext


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path))


async def test_read_file_line_numbered(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    res = await fs.ReadFile().run({"path": "a.txt"}, ctx)
    assert res.ok
    assert "1\tone" in res.content
    assert "3\tthree" in res.content


async def test_read_file_offset_limit(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"L{i}" for i in range(1, 11)))
    res = await fs.ReadFile().run({"path": "a.txt", "offset": 3, "limit": 2}, ctx)
    assert "L3" in res.content and "L4" in res.content
    assert "L5" not in res.content


async def test_read_missing_file_errors(ctx):
    res = await fs.ReadFile().run({"path": "nope.txt"}, ctx)
    assert res.is_error and "no such file" in res.content


async def test_write_then_read(ctx, tmp_path):
    res = await fs.WriteFile().run({"path": "sub/b.txt", "content": "hi\nthere"}, ctx)
    assert res.ok
    assert (tmp_path / "sub" / "b.txt").read_text() == "hi\nthere"


async def test_append_adds_to_the_end(ctx, tmp_path):
    (tmp_path / "doc.md").write_text("# Title\n")
    res = await fs.AppendFile().run(
        {"path": "doc.md", "content": "## Section\nbody\n"}, ctx)
    assert res.ok
    assert (tmp_path / "doc.md").read_text() == "# Title\n## Section\nbody\n"


async def test_append_chains_across_calls(ctx, tmp_path):
    (tmp_path / "doc.md").write_text("one\n")
    for part in ("two\n", "three\n"):
        assert (await fs.AppendFile().run(
            {"path": "doc.md", "content": part}, ctx)).ok
    assert (tmp_path / "doc.md").read_text() == "one\ntwo\nthree\n"


async def test_append_to_missing_file_errors_and_creates_nothing(ctx, tmp_path):
    res = await fs.AppendFile().run({"path": "gone.md", "content": "x"}, ctx)
    assert res.is_error
    assert "write_file" in res.content
    assert not (tmp_path / "gone.md").exists()


async def test_append_reports_lines_added_and_total(ctx, tmp_path):
    (tmp_path / "doc.md").write_text("a\nb\n")
    res = await fs.AppendFile().run({"path": "doc.md", "content": "c\nd\n"}, ctx)
    assert "2 lines" in res.content
    assert "5 lines total" in res.content


async def test_edit_file_unique_match(ctx, tmp_path):
    (tmp_path / "c.py").write_text("x = 1\ny = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "y = 2", "new": "y = 3"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "x = 1\ny = 3\n"


async def test_edit_file_ambiguous_refused(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\na\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "a", "new": "b"}, ctx)
    assert res.is_error and "appears 2 times" in res.content
    # unchanged
    assert (tmp_path / "c.py").read_text() == "a\na\n"


async def test_edit_file_ambiguous_lists_match_locations(ctx, tmp_path):
    # The dominant recoverable failure: a non-unique `old`. The error must show
    # WHERE the matches are so the model can add context to pin the right one.
    (tmp_path / "c.py").write_text("x = 1\ny = 2\nx = 1\nz = 3\nx = 1\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    assert res.is_error
    assert ("match at line 1" in res.content and "match at line 3" in res.content
            and "match at line 5" in res.content)
    assert (tmp_path / "c.py").read_text() == "x = 1\ny = 2\nx = 1\nz = 3\nx = 1\n"


# --- an ambiguous match must show what SEPARATES the sites (build 97) ---
#
# 125 ambiguous-match events across 37% of b87+ runs; 43 were answered by
# resending the identical `old`. The old message listed `line N: <first line of
# old>` per site, which renders byte-identically for every site by
# construction. See ROADMAP 5.20.

async def test_ambiguous_shows_the_lines_around_each_match(ctx, tmp_path):
    (tmp_path / "c.py").write_text(
        "before_one\nx = 1\nafter_one\n\nbefore_two\nx = 1\nafter_two\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    assert res.is_error
    # The distinguishing context, not just the model's own search text echoed back.
    for ctxline in ("before_one", "after_one", "before_two", "after_two"):
        assert ctxline in res.content, ctxline


async def test_ambiguous_sites_are_not_byte_identical(ctx, tmp_path):
    # The actual defect: every listed site used to render the same characters.
    (tmp_path / "c.py").write_text(
        "alpha\nx = 1\nbravo\n\ncharlie\nx = 1\ndelta\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    blocks = res.content.split("── match at line ")[1:]
    assert len(blocks) == 2
    assert blocks[0] != blocks[1]


async def test_ambiguous_renders_blocks_verbatim_with_no_gutter(ctx, tmp_path):
    # Build 119: the `NN |` gutter and the `>` marker are GONE. The model is
    # now told to copy these lines into `old`, and b97 proved it cannot tell a
    # gutter's padding from the code's own indentation when it strips one —
    # 8 of its 20 syntax rejections came off this message (5.28).
    (tmp_path / "c.py").write_text(
        "alpha\n    x = 1\nbravo\ncharlie\n    x = 1\ndelta\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "    x = 1", "new": "    x = 9"}, ctx)
    assert "|>" not in res.content
    # the code's OWN indentation survives, at column 0 of the message
    assert "\n    x = 1\n" in res.content
    assert "match at line 2" in res.content and "lines 1-3" in res.content


async def test_ambiguous_widens_a_window_that_is_not_yet_unique(ctx, tmp_path):
    # A +/-1 window that still occurs twice would send the model straight back
    # into the same error, so it expands until the block is unique.
    (tmp_path / "c.py").write_text(
        "head\npad\nx = 1\npad\nmid\npad\nx = 1\npad\ntail\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    blocks = res.content.split("── match at line ")[1:]
    assert len(blocks) == 2 and blocks[0] != blocks[1]
    body = res.content
    assert "head" in body and "tail" in body  # widened past the identical pads


async def test_ambiguous_tells_the_model_not_to_resend(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nx = 1\nb\nc\nx = 1\nd\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    assert "same way" in res.content
    assert "replace_lines" in res.content and "replace_all" in res.content


async def test_ambiguous_caps_the_number_of_sites_shown(ctx, tmp_path):
    (tmp_path / "c.py").write_text("".join(f"pad{i}\nx = 1\n" for i in range(9)))
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    assert res.content.count("── match at line ") == fs._AMBIG_SITES
    assert f"and {9 - fs._AMBIG_SITES} more" in res.content


async def test_ambiguous_handles_a_multiline_old(ctx, tmp_path):
    (tmp_path / "c.py").write_text(
        "head\na = 1\nb = 2\ntail\n\nhead2\na = 1\nb = 2\ntail2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "a = 1\nb = 2", "new": "a = 9\nb = 9"}, ctx)
    assert res.is_error
    # both lines of the match rendered, at both sites, with their own context
    assert res.content.count("a = 1") == 2
    assert res.content.count("b = 2") == 2
    assert "head" in res.content and "tail2" in res.content


async def test_ambiguous_near_the_file_edges_does_not_crash(ctx, tmp_path):
    (tmp_path / "c.py").write_text("x = 1\nx = 1\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 9"}, ctx)
    assert res.is_error and "match at line 1" in res.content


# --- old == new splits two ways (build 110, ROADMAP 5.34) -------------------
#
# One message used to cover both, headed "you drafted your replacement into both
# fields". Reconstructed against the edits already landed in the same run, 18 of
# 20 were re-sends of a change that HAD already been applied — so `old` was
# still in the file, the "malformed edit" diagnosis was false, and the
# replace_lines suffix invited the model to force in text the file already had.

async def test_edit_file_noop_with_old_present_reads_as_already_done(ctx, tmp_path):
    # `old` is in the file: the edit is redundant, not broken. Non-error, in the
    # same "already done" family as the two re-submit paths above, so the model
    # moves on instead of hunting for a way to re-apply it.
    (tmp_path / "c.py").write_text("a = 0\nvalue = compute()\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "value = compute()", "new": "value = compute()"}, ctx)
    assert res.ok and res.no_change
    assert "already done" in res.content.lower()
    assert "line 2" in res.content            # names where it already reads that way
    assert "replace_lines" not in res.content  # must not offer a way to force it in
    assert (tmp_path / "c.py").read_text() == "a = 0\nvalue = compute()\n"


async def test_edit_file_already_done_asks_for_a_CALL_not_a_paragraph(ctx, tmp_path):
    # Build 111. Build 110 got the diagnosis right and the shape wrong: it named
    # no tool, put its action seventh behind three prohibitions, and hedged it
    # as "if something is still failing, run the tests again". All four
    # candidate responses in b110-alreadydone answered with `update_plan` — the
    # cheapest thing in the toolset, because "run the tests again" is not a call
    # the model can emit. 5.32's recipe: the call first, named, and an explicit
    # ban on replying with prose. ROADMAP 5.36.
    (tmp_path / "c.py").write_text("a = 0\nvalue = compute()\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "value = compute()", "new": "value = compute()"}, ctx)
    body = res.content
    assert "Call bash now" in body
    assert "the next thing you send must be that bash call" in body
    # the call precedes every prohibition
    assert body.index("Call bash") < body.index("Do NOT resend")


async def test_edit_file_noop_already_done_is_found_at_a_shifted_indent(ctx, tmp_path):
    # The re-send usually comes back dedented (the model retypes the line rather
    # than copying it), so presence has to be whitespace-tolerant or the run
    # falls into the wrong branch on the commonest shape of the right case.
    (tmp_path / "c.py").write_text("class A:\n    def f(self):\n        return 42\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "return 42", "new": "return 42"}, ctx)
    assert res.ok and res.no_change
    assert "already done" in res.content.lower() and "line 3" in res.content


async def test_edit_file_noop_with_old_absent_still_names_the_drafting_error(ctx, tmp_path):
    # `old` is nowhere in the file, so the model really did put its intended
    # replacement in both fields. This one stays an error and keeps the
    # replace_lines route, which is a genuine way out here.
    (tmp_path / "c.py").write_text("value = compute()\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "value = recompute()", "new": "value = recompute()"},
        ctx)
    assert res.is_error and res.no_change
    assert "does nothing" in res.content.lower()
    assert "both fields" in res.content.lower()
    assert "replace_lines" in res.content
    assert (tmp_path / "c.py").read_text() == "value = compute()\n"


async def test_edit_file_indent_only_change_is_reported_as_noop(ctx, tmp_path):
    # old != new as strings (they differ only in leading indentation), so the
    # exact `old == new` guard does NOT fire. `old` lacks the file's indent so the
    # exact tier misses; the whitespace-tolerant tier matches but STRIPS new's
    # first-line indent and preserves the file's original -> the result is
    # byte-for-byte identical. This must be an error, not a false "edited"
    # success, or a model trying to fix indentation loops forever thinking it won.
    (tmp_path / "c.py").write_text("class A:\n    x = 1\n    y = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "x = 1\ny = 2", "new": "x = 1\n    y = 2"}, ctx)
    assert res.is_error
    assert "nothing" in res.content.lower() or "identical" in res.content.lower()
    assert "indent" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "class A:\n    x = 1\n    y = 2\n"


async def test_edit_file_tolerant_content_change_still_succeeds(ctx, tmp_path):
    # Guard the fix's blast radius: a whitespace-tolerant match that DOES change
    # content (not just indent) must still report success and write. `old` lacks
    # the file's indentation so it misses the exact tier and lands in tier 2.
    (tmp_path / "c.py").write_text("class A:\n    x = 1\n    y = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "x = 1\ny = 2", "new": "x = 99\n    y = 2"}, ctx)
    assert res.ok and "whitespace-tolerant" in res.content
    assert (tmp_path / "c.py").read_text() == "class A:\n    x = 99\n    y = 2\n"


async def test_edit_file_success_echoes_changed_region(ctx, tmp_path):
    # A successful edit echoes the new state of the changed region, numbered like
    # read_file, so the model isn't blind to what landed and can build an accurate
    # follow-up `old`. The NEW content must appear; a couple context lines too.
    (tmp_path / "c.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "c = 3", "new": "c = 30"}, ctx)
    assert res.ok
    assert "c = 30" in res.content            # the new content is shown
    assert "\t" in res.content and "3\tc = 30" in res.content  # line-numbered (line 3)
    assert "b = 2" in res.content             # a context line above
    assert (tmp_path / "c.py").read_text() == "a = 1\nb = 2\nc = 30\nd = 4\ne = 5\n"


async def test_edit_snippet_caps_large_changes(ctx, tmp_path):
    # A big edit must not flood the reply: the echoed region is capped.
    (tmp_path / "c.py").write_text("x = 0\n")
    big_new = "\n".join(f"line{i}" for i in range(100))
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 0", "new": big_new}, ctx)
    assert res.ok
    assert "more lines)" in res.content       # truncation marker present
    assert res.content.count("\n") < 40       # not the whole 100-line body


async def test_edit_file_replace_all(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\na\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "a", "new": "b", "replace_all": True}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "b\nb\n"


async def test_edit_file_empty_old_is_refused_not_exploded(ctx, tmp_path):
    """An empty `old` used to be the worst input edit_file accepted.

    `"".count(text)` is len(text)+1, so it read as "ambiguous" and the error told
    the model to pass replace_all — at which point `text.replace("", new)` splices
    `new` between every character. Observed in eval r11: one file went 867 chars ->
    273,105 -> 79,746,660 across three obeyed retries, and the run died when pytest
    could no longer parse it. The harness was instructing the model to do this."""
    (tmp_path / "c.py").write_text("x = 1\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "", "new": "zzz"}, ctx)
    assert res.is_error
    assert "`old` is empty" in res.content
    # Point it at the tool that actually does what it was reaching for.
    assert "append_file" in res.content
    # And crucially: no "pass replace_all" advice, which is the destructive path.
    assert "replace_all" not in res.content
    assert (tmp_path / "c.py").read_text() == "x = 1\n"


async def test_edit_file_empty_old_stays_refused_under_replace_all(ctx, tmp_path):
    """replace_all is the flag that turned the bug destructive — guard it too."""
    (tmp_path / "c.py").write_text("x = 1\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "", "new": "zzz", "replace_all": True}, ctx)
    assert res.is_error
    assert (tmp_path / "c.py").read_text() == "x = 1\n"


def test_try_edit_refuses_an_empty_old_at_the_matcher(tmp_path):
    """Guard the shared matcher, not just the tool: the ASK diff preview calls
    try_edit directly, so an unguarded matcher would render a 300x blowup as the
    change the user is being asked to approve."""
    for replace_all in (False, True):
        updated, _note, status, count = fs.try_edit("abc", "", "Z", replace_all)
        assert status == "empty_old"
        assert updated is None and count == 0


async def test_edit_file_not_found_string(ctx, tmp_path):
    (tmp_path / "c.py").write_text("hello\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "zzz", "new": "q"}, ctx)
    assert res.is_error and "not found" in res.content


async def test_edit_file_tolerant_indentation_multiline(ctx, tmp_path):
    # `old` reproduced WITHOUT the file's indentation across multiple lines —
    # exact match fails; tolerant per-line match locates it and the original
    # indentation is preserved on the replacement.
    (tmp_path / "c.py").write_text("class A:\n    x = 1\n    y = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "x = 1\ny = 2", "new": "x = 10\n    y = 20"}, ctx)
    assert res.ok and "whitespace-tolerant" in res.content
    assert (tmp_path / "c.py").read_text() == "class A:\n    x = 10\n    y = 20\n"


async def test_edit_file_tolerates_copied_lineno_prefix(ctx, tmp_path):
    # Model pasted read_file's "     2\t" line-number prefix into `old`.
    (tmp_path / "c.py").write_text("x = 1\ny = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "     2\ty = 2", "new": "y = 3"}, ctx)
    assert res.ok and "whitespace-tolerant" in res.content
    assert (tmp_path / "c.py").read_text() == "x = 1\ny = 3\n"


async def test_edit_file_tolerant_multiline(ctx, tmp_path):
    (tmp_path / "c.py").write_text("def f():\n    a = 1\n    b = 2\n    return a\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "a = 1\nb = 2", "new": "a = 10\n    b = 20"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == \
        "def f():\n    a = 10\n    b = 20\n    return a\n"


async def test_edit_file_tolerant_ambiguous_refused(ctx, tmp_path):
    # Two indentation-insensitive matches and no replace_all -> refuse, unchanged.
    (tmp_path / "c.py").write_text("    foo()\nfoo()\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "foo()", "new": "bar()"}, ctx)
    assert res.is_error
    assert (tmp_path / "c.py").read_text() == "    foo()\nfoo()\n"


async def test_edit_file_fuzzy_matches_minor_drift(ctx, tmp_path):
    # `old` differs from the file by more than whitespace (a paraphrased token):
    # exact + whitespace-tolerant fail, fuzzy locates the clear best block.
    (tmp_path / "c.py").write_text(
        "def on_button_press(self, instance):\n    self.update_board(instance)\n")
    res = await fs.EditFile().run(
        {"path": "c.py",
         "old": "def on_button_press(self, inst):",        # 'inst' vs 'instance'
         "new": "def on_button_press(self, instance, x):"}, ctx)
    assert res.ok and "fuzzy" in res.content
    assert "instance, x" in (tmp_path / "c.py").read_text()


async def test_edit_file_fuzzy_tab_to_spaces_lineno(ctx, tmp_path):
    # Model converted read_file's "  12\t" tab prefix to spaces — not stripped by
    # the exact line-number regex, but fuzzy still finds the line.
    (tmp_path / "c.py").write_text("alpha = 1\nbeta = 2\ngamma = 3\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "    2 beta = 2", "new": "beta = 22"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "alpha = 1\nbeta = 22\ngamma = 3\n"


async def test_edit_file_fuzzy_refuses_when_ambiguous(ctx, tmp_path):
    # Two near-identical candidate blocks -> no clear winner -> not applied.
    (tmp_path / "c.py").write_text("value = 1\nvalue = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "valu = 9", "new": "value = 99"}, ctx)
    assert res.is_error
    assert (tmp_path / "c.py").read_text() == "value = 1\nvalue = 2\n"


async def test_edit_file_fuzzy_not_used_for_replace_all(ctx, tmp_path):
    (tmp_path / "c.py").write_text("foo()\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "fooo()", "new": "bar()", "replace_all": True}, ctx)
    assert res.is_error and "not found" in res.content


async def test_edit_file_genuinely_absent_errors(ctx, tmp_path):
    # Nothing in the file is similar enough for fuzzy -> not-found error.
    (tmp_path / "c.py").write_text("alpha = 1\nbeta = 2\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "qqqq zzzz wwww vvvv", "new": "y"}, ctx)
    assert res.is_error and "not found" in res.content


async def test_edit_file_already_applied_is_nonerror_noop(ctx, tmp_path):
    # build 55: the model applied a fix, verified it, then re-submitted the same
    # edit; `old` is gone but `new` is already in the file. Answer "already
    # applied" (non-error no_change) so the model doesn't read a not-found error
    # as fixable and revert its own working change.
    (tmp_path / "c.py").write_text("def f():\n    return 42\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "return 0", "new": "return 42"}, ctx)
    assert res.ok and res.no_change
    assert "already done" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "def f():\n    return 42\n"


async def test_edit_file_already_applied_via_noop_path(ctx, tmp_path):
    # The re-submit often lands in the noop branch, not not_found: `old` (the OLD
    # buggy line) fuzzy-matches the already-fixed line, and replacing it with `new`
    # is byte-identical. That must ALSO read as already-applied (non-error), not as
    # the indent-only "changed nothing" error that would drive a revert.
    (tmp_path / "c.py").write_text("timeout = 60\nretries = 3\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "timeout = 30", "new": "timeout = 60"}, ctx)
    assert res.ok and res.no_change
    assert "already done" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "timeout = 60\nretries = 3\n"


async def test_edit_file_short_new_still_not_found(ctx, tmp_path):
    # A trivial `new` (<3 chars) that coincidentally occurs must NOT be masked as
    # already-applied — it stays a genuine not-found so the model can correct it.
    (tmp_path / "c.py").write_text("a = 1\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "zzz", "new": "1"}, ctx)
    assert res.is_error and "replace_lines" in res.content


async def test_edit_file_redelete_already_gone_is_nonerror(ctx, tmp_path):
    # build 57: the deletion arm of already-done. The model deleted a line, then
    # re-submits the same delete (old=<gone line>, new=""). `old` no longer matches
    # (not even fuzzily → the content is truly gone), so a plain not-found would
    # fire AND suggest replace_lines — a line-number re-delete lands on shifted
    # lines and corrupts the file (the remove-block over-delete). Must be a
    # NON-error "already done" that explicitly says NOT to switch to line numbers.
    (tmp_path / "app.py").write_text("def main():\n    x = 1\n    return x\n")
    res = await fs.EditFile().run(
        {"path": "app.py", "old": "    print('DEBUG: entering main')\n", "new": ""},
        ctx)
    assert res.ok and res.no_change
    assert "already done" in res.content.lower()
    assert "line-number" in res.content.lower()  # steers OFF replace_lines
    assert (tmp_path / "app.py").read_text() == "def main():\n    x = 1\n    return x\n"


async def test_edit_file_first_delete_still_works(ctx, tmp_path):
    # Guard: extending already-done to deletions must NOT break a real first-time
    # delete — old present, new="" removes it (status ok, not the already-done arm).
    (tmp_path / "app.py").write_text("def main():\n    print('DEBUG')\n    return 1\n")
    res = await fs.EditFile().run(
        {"path": "app.py", "old": "    print('DEBUG')\n", "new": ""}, ctx)
    assert res.ok and not res.no_change
    assert (tmp_path / "app.py").read_text() == "def main():\n    return 1\n"


def test_already_applied_helper():
    assert fs._already_applied("def f():\n    return 42\n", "return 42")
    assert not fs._already_applied("x = 1\n", "y")          # too short
    assert not fs._already_applied("x = 1\n", "return 42")  # absent
    # present at a different indent than `new` carries -> tolerant match catches it
    assert fs._already_applied("class A:\n        if x:\n            y = 1\n",
                               "if x:\n    y = 1")


def test_same_content_distinguishes_indent_from_real_change():
    assert fs._same_content("x = 1", "    x = 1")       # indent-only
    assert fs._same_content("     2\ty = 2", "y = 2")   # lineno prefix only
    assert not fs._same_content("return 0", "return 42")  # different content


def test_not_found_help_shows_verbatim_snippet_and_caveat():
    from pathlib import Path
    msg = fs._not_found_help("import os\ndef handle_click(self):\n    pass\n",
                             "def handle_clik(self):", Path("c.py"))
    assert "line-number prefixes" in msg
    assert "lines 1-3" in msg                  # the window, not just one line
    assert "def handle_click(self):" in msg   # verbatim, copyable
    assert "    pass" in msg                   # includes a line of context


def test_best_block_scores_the_whole_block_not_just_the_first_line():
    # "    return None" appears twice, so anchoring on `old`'s FIRST line lands
    # on the earlier one. Only the second line of the block disambiguates.
    text = ("def alpha():\n    return None\n\n"
            "def beta():\n    return None\n    log('beta done')\n")
    old = ["    return None", "    log('beta dnoe')"]   # typo in line 2
    start, ratio, _second = fs._best_block(text.split("\n"), old)
    assert start == 4          # the beta block, not the alpha one at index 1
    assert ratio > 0.8


def test_not_found_help_points_at_the_block_matched_region():
    from pathlib import Path
    text = ("def alpha():\n    return None\n\n"
            "def beta():\n    return None\n    log('beta done')\n")
    msg = fs._not_found_help(text, "    return None\n    log('beta dnoe')",
                             Path("c.py"))
    assert "log('beta done')" in msg      # the real text, ready to copy
    # Build 105: the confident path leads with the filled-in replace_lines call
    # instead of "The closest match is". The region it names is still the beta
    # block (lines 4-7 with the window), not the alpha one.
    assert "start=4, end=7" in msg


def test_not_found_help_window_is_conservative_by_default():
    # Build 90 widened this to 12 and it measured HARMFUL -- the model
    # surrendered rather than copied. Pin the narrow default so a future
    # widening has to be a deliberate, re-measured change.
    assert fs._HELP_WINDOW == 1


def test_not_found_help_can_show_a_wide_window_on_request():
    from pathlib import Path
    lines = ["pass"] * 60
    lines[5], lines[19] = "MARKER_FAR", "MARKER_ABOVE"
    lines[30] = "def compute_totals(rows):"
    lines[41] = "MARKER_BELOW"
    msg = fs._not_found_help("\n".join(lines), "def compute_totls(rows):",
                             Path("c.py"), window=12)
    assert "MARKER_ABOVE" in msg
    assert "MARKER_BELOW" in msg
    assert "MARKER_FAR" not in msg         # ... but not the whole file


def test_not_found_help_caps_a_huge_window():
    from pathlib import Path
    lines = [f"line_{i}" for i in range(200)]
    old = "\n".join(f"line_{i}" for i in range(50, 130)).replace("line_60",
                                                                 "line_6O")
    msg = fs._not_found_help("\n".join(lines), old, Path("c.py"))
    assert "more lines)" in msg
    assert msg.count("\nline_") <= fs._HELP_MAX_LINES


def test_not_found_help_says_so_when_nothing_resembles_the_target():
    # Zero similarity anywhere is a different failure from "close but drifted":
    # it means the wrong file, so the model is told to re-read rather than
    # handed a region that would only mislead it.
    from pathlib import Path
    msg = fs._not_found_help("alpha\nbravo\ncharlie\n", "zzzzzzzz", Path("c.py"))
    assert "NOTHING in this file resembles" in msg
    assert "read_file" in msg


async def test_move_file_renames(ctx, tmp_path):
    (tmp_path / "src.txt").write_text("payload")
    res = await fs.MoveFile().run({"src": "src.txt", "dst": "dst.txt"}, ctx)
    assert res.ok
    assert (tmp_path / "dst.txt").read_text() == "payload"
    assert not (tmp_path / "src.txt").exists()


async def test_move_file_creates_dest_parents(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    res = await fs.MoveFile().run({"src": "a.txt", "dst": "sub/dir/a.txt"}, ctx)
    assert res.ok
    assert (tmp_path / "sub" / "dir" / "a.txt").read_text() == "x"


async def test_move_file_missing_source_errors_not_raises(ctx):
    res = await fs.MoveFile().run({"src": "nope.txt", "dst": "out.txt"}, ctx)
    assert res.is_error and "no such file" in res.content


async def test_ls_and_glob(ctx, tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "pkg").mkdir()
    ls = await fs.Ls().run({}, ctx)
    assert "pkg/" in ls.content and "a.py" in ls.content
    g = await fs.Glob().run({"pattern": "*.py"}, ctx)
    assert g.content.endswith("a.py")


async def test_grep(ctx, tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    res = await fs.Grep().run({"pattern": r"def \w+", "glob": "*.py"}, ctx)
    assert "a.py:1:def foo():" in res.content


def test_write_file_states_the_size_cap_as_a_flat_number():
    # Measured twice, in both directions. "Keep content under about 6000
    # characters" took design-doc/qythos9 0.38 -> 0.98. Softening it to
    # "write COMPLETE content ... if it would run past 8000, use append_file"
    # took the same row to 0.07, with 33-41k-char replies and not one
    # successful write_file in three runs. The flat low number is the brake;
    # it works by pulling the target down, not by being obeyed.
    desc = fs.WriteFile.description
    assert "6000" in desc
    assert "COMPLETE" not in desc
    assert "append_file" in desc


# --- inline SyntaxError feedback on .py writes (3.1: make qythos9's syntax
# deaths legible where they happen, not later as a pytest collection traceback) ---

async def test_write_py_with_syntax_error_warns_but_still_saves(ctx, tmp_path):
    res = await fs.WriteFile().run(
        {"path": "envcfg.py", "content": "def load(:\n    pass\n"}, ctx)
    assert res.ok  # advisory only — the file IS written
    assert (tmp_path / "envcfg.py").exists()
    assert "SyntaxError" in res.content
    assert "line 1" in res.content


async def test_write_valid_py_has_no_warning(ctx, tmp_path):
    res = await fs.WriteFile().run(
        {"path": "envcfg.py", "content": "def load():\n    return {}\n"}, ctx)
    assert res.ok
    assert "SyntaxError" not in res.content


async def test_write_broken_non_py_is_not_syntax_checked(ctx, tmp_path):
    # A .md/.txt file is never Python; malformed "code" in it must not warn.
    res = await fs.WriteFile().run(
        {"path": "DESIGN.md", "content": "def load(:  # prose, not code\n"}, ctx)
    assert res.ok
    assert "SyntaxError" not in res.content


async def test_edit_that_introduces_a_syntax_error_is_rejected(ctx, tmp_path):
    # build 47: was warn-and-apply; now a valid→invalid .py edit is REFUSED so
    # the corruption never lands and the file stays in its last-good state.
    (tmp_path / "envcfg.py").write_text("def load():\n    return 1\n")
    res = await fs.EditFile().run(
        {"path": "envcfg.py", "old": "return 1", "new": "return ("}, ctx)
    assert res.is_error and "NOT applied" in res.content
    assert "SyntaxError" in res.content
    assert (tmp_path / "envcfg.py").read_text() == "def load():\n    return 1\n"


async def test_edit_that_fixes_a_syntax_error_has_no_warning(ctx, tmp_path):
    (tmp_path / "envcfg.py").write_text("x = (\n")
    res = await fs.EditFile().run(
        {"path": "envcfg.py", "old": "x = (", "new": "x = 1"}, ctx)
    assert res.ok
    assert "SyntaxError" not in res.content


async def test_append_that_breaks_python_warns_on_full_file(ctx, tmp_path):
    # The break spans the append boundary: each half is fine, the whole is not.
    (tmp_path / "m.py").write_text("x = (1 +\n")
    res = await fs.AppendFile().run({"path": "m.py", "content": "def bad(:\n"}, ctx)
    assert res.ok
    assert "SyntaxError" in res.content


# --- replace_lines: line-number editing (edit_file's byte-match fallback) -----
async def test_replace_lines_swaps_a_single_line(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a = 1\nb = 2\nc = 3\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 2, "new": "b = 22"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "a = 1\nb = 22\nc = 3\n"
    assert "2\tb = 22" in res.content  # echoes the changed region, line-numbered


async def test_replace_lines_fixes_a_malformed_line_edit_cant_match(ctx, tmp_path):
    # The transcript case: a docstring line ends in a stray ' instead of \"\"\".
    # The model can SEE line 2 but can't reproduce its exact bytes for edit_file.
    q3 = '"' * 3
    (tmp_path / "s.py").write_text(f"def main():\n    {q3}Run it.'\n    pass\n")
    res = await fs.ReplaceLines().run(
        {"path": "s.py", "start": 2, "end": 2, "new": f"    {q3}Run it.{q3}"}, ctx)
    assert res.ok
    assert (tmp_path / "s.py").read_text() == f"def main():\n    {q3}Run it.{q3}\n    pass\n"


async def test_replace_lines_spanning_range(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nb\nc\nd\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 3, "new": "X\nY\nZ"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "a\nX\nY\nZ\nd\n"


async def test_replace_lines_empty_new_deletes(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nb\nc\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 2, "new": ""}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "a\nc\n"


async def test_replace_lines_preserves_missing_final_newline(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nb")  # no trailing newline
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 2, "new": "B"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "a\nB"


async def test_replace_lines_out_of_range_errors_and_leaves_file(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nb\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 5, "end": 5, "new": "z"}, ctx)
    assert res.is_error
    assert "2 lines" in res.content and "re-read" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "a\nb\n"


async def test_replace_lines_identical_is_a_nonerror_noop(ctx, tmp_path):
    # build 55: an identical replace is "already in place" — a NON-error no_change
    # so the model reads it as done, not as a fixable error it should revert.
    (tmp_path / "c.py").write_text("a\nb\nc\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 2, "new": "b"}, ctx)
    assert res.ok and res.no_change
    assert "already in place" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "a\nb\nc\n"


async def test_replace_lines_coerces_string_line_numbers(ctx, tmp_path):
    # Weak models often send "2" rather than 2.
    (tmp_path / "c.py").write_text("a\nb\nc\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": "2", "end": "2", "new": "B"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "a\nB\nc\n"


async def test_replace_lines_missing_file_errors(ctx):
    res = await fs.ReplaceLines().run(
        {"path": "nope.py", "start": 1, "end": 1, "new": "x"}, ctx)
    assert res.is_error and "no such file" in res.content


async def test_replace_lines_that_breaks_valid_python_is_rejected(ctx, tmp_path):
    # build 47: a valid→invalid replace_lines is refused, same as edit_file.
    (tmp_path / "m.py").write_text("x = 1\ny = 2\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 1, "end": 1, "new": "x = ("}, ctx)
    assert res.is_error and "NOT applied" in res.content
    assert (tmp_path / "m.py").read_text() == "x = 1\ny = 2\n"


def test_try_replace_lines_bad_range_returns_none(tmp_path):
    # The ASK preview calls this directly — a bad range must yield no diff, not raise.
    updated, status = fs.try_replace_lines("a\nb\n", 9, 9, "z")
    assert status == "bad_range" and updated is None


# --- edit_file failures steer toward replace_lines / clear missing-arg errors -
async def test_edit_file_missing_new_gives_clear_error_not_keyerror(ctx, tmp_path):
    # A model sending `old` but no `new` used to raise KeyError('new'), surfaced
    # as an opaque "edit_file failed: KeyError: 'new'". Name the field instead.
    (tmp_path / "c.py").write_text("x = 1\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1"}, ctx)
    assert res.is_error
    assert "KeyError" not in res.content
    assert "`new`" in res.content and "missing" in res.content.lower()


async def test_edit_file_not_found_points_at_replace_lines(ctx, tmp_path):
    (tmp_path / "c.py").write_text("hello\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "zzz", "new": "q"}, ctx)
    assert res.is_error and "replace_lines" in res.content


async def test_edit_file_noop_old_equals_new_points_at_replace_lines(ctx, tmp_path):
    # The exact transcript stall: a literal backslash on the line makes the
    # model's `old` and `new` collapse to identical. Route it to replace_lines.
    (tmp_path / "c.py").write_text('"""doc.""\\"\n')
    res = await fs.EditFile().run(
        {"path": "c.py", "old": '"""doc."""', "new": '"""doc."""'}, ctx)
    assert res.is_error and "replace_lines" in res.content


async def test_replace_lines_missing_new_is_not_a_silent_delete(ctx, tmp_path):
    (tmp_path / "c.py").write_text("a\nb\nc\n")
    res = await fs.ReplaceLines().run({"path": "c.py", "start": 2, "end": 2}, ctx)
    assert res.is_error and "`new`" in res.content
    assert (tmp_path / "c.py").read_text() == "a\nb\nc\n"  # untouched


def test_edit_file_description_routes_indent_fixes_to_replace_lines():
    # Lever 1, corrected (build 46): weak models pick tools by their
    # descriptions. edit_file is content-anchored but CANNOT do an indent-only
    # change (it preserves indentation → no-op), so its description must say so
    # and point indentation fixes at replace_lines — the b45 sweep showed the
    # old "PREFER edit_file / LAST-RESORT" steering drove the model off the tool
    # an indentation bug actually needs, and it fixed 0/5 vs 5/5 on control.
    desc = fs.EditFile.description
    assert "ontent-anchored" in desc
    assert "indentation-only" in desc and "replace_lines" in desc


def test_replace_lines_description_is_the_tool_for_indent_and_warns_on_stale():
    # replace_lines must read as the RIGHT tool for an indentation/whitespace fix
    # (not a demoted last resort) AND still warn that a stale re-issue duplicates
    # content — the failure the build-42 loop guard caught.
    desc = fs.ReplaceLines.description
    assert "indentation" in desc
    assert "STALE" in desc and "DUPLICATE" in desc


def test_descriptions_steer_deletion_to_edit_file():
    # Lever 2 (build 56): number-anchored deletion is the remove-block trap —
    # each delete renumbers the lines below, so deleting several blocks by number
    # over-deletes/duplicates (gemmacoder12 ~80% fail). edit_file with new="" is
    # shift-immune. Both descriptions must steer deletion to edit_file, WITHOUT
    # demoting replace_lines for its legitimate indentation use (the b45 lesson).
    edit_desc = fs.EditFile.description
    rl_desc = fs.ReplaceLines.description
    assert "DELETE" in edit_desc and "empty string" in edit_desc
    assert "PREFERRED way to DELETE" in edit_desc
    assert "PREFER edit_file" in rl_desc
    # replace_lines is still presented as the RIGHT tool for indentation, not a
    # blanket last resort (guards against re-breaking the indent path).
    assert "RIGHT tool" in rl_desc


async def test_edit_file_deletes_with_empty_new(ctx, tmp_path):
    # The steered path must actually work: old=the exact line(s), new="" removes
    # them, content-anchored so a second delete still matches after the first.
    (tmp_path / "app.py").write_text(
        "def main():\n"
        "    print('DEBUG: a')\n"
        "    x = 1\n"
        "    print('DEBUG: b')\n"
        "    return x\n")
    r1 = await fs.EditFile().run(
        {"path": "app.py", "old": "    print('DEBUG: a')\n", "new": ""}, ctx)
    r2 = await fs.EditFile().run(
        {"path": "app.py", "old": "    print('DEBUG: b')\n", "new": ""}, ctx)
    assert r1.ok and r2.ok
    assert (tmp_path / "app.py").read_text() == \
        "def main():\n    x = 1\n    return x\n"


async def test_edit_file_rejects_edit_that_introduces_syntax_error(ctx, tmp_path):
    # build 47: an edit that turns PARSEABLE .py into a SyntaxError (here an
    # unclosed paren) must NOT land — a warned-but-applied corrupt edit is what
    # sent gemmacoder12 into an unrecoverable flail (2026-07-26).
    (tmp_path / "c.py").write_text("x = (1 + 2)\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "x = (1 + 2)", "new": "x = (1 + 2"}, ctx)
    assert res.is_error and "NOT applied" in res.content
    assert "SyntaxError" in res.content
    # File left in the last-good state the model already read.
    assert (tmp_path / "c.py").read_text() == "x = (1 + 2)\n"


async def test_edit_file_allows_fixing_an_already_broken_file(ctx, tmp_path):
    # The guard must never block a model FIXING syntax: if the file didn't parse
    # before, any edit is allowed (even one that leaves it still broken).
    (tmp_path / "c.py").write_text("x = (1 + 2\n")  # already broken
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "x = (1 + 2", "new": "x = (1 + 2)"}, ctx)
    assert res.ok
    assert (tmp_path / "c.py").read_text() == "x = (1 + 2)\n"


async def test_edit_file_normal_valid_change_still_lands(ctx, tmp_path):
    (tmp_path / "c.py").write_text("x = 1\n")
    res = await fs.EditFile().run({"path": "c.py", "old": "x = 1", "new": "x = 2"}, ctx)
    assert res.ok and (tmp_path / "c.py").read_text() == "x = 2\n"


async def test_edit_file_syntax_guard_ignores_non_python(ctx, tmp_path):
    (tmp_path / "c.txt").write_text("x = (1 + 2)\n")
    res = await fs.EditFile().run(
        {"path": "c.txt", "old": "x = (1 + 2)", "new": "x = (1 + 2"}, ctx)
    assert res.ok and (tmp_path / "c.txt").read_text() == "x = (1 + 2\n"


async def test_replace_lines_rejects_edit_that_breaks_syntax(ctx, tmp_path):
    (tmp_path / "c.py").write_text("x = (1)\n")
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 1, "end": 1, "new": "x = (1"}, ctx)
    assert res.is_error and "NOT applied" in res.content
    assert (tmp_path / "c.py").read_text() == "x = (1)\n"


async def test_replace_lines_still_fixes_a_broken_file(ctx, tmp_path):
    # The empty-with-block class: file doesn't parse; replace_lines fixes it.
    (tmp_path / "c.py").write_text("def f():\n    x=(\n")  # broken before
    res = await fs.ReplaceLines().run(
        {"path": "c.py", "start": 2, "end": 2, "new": "    x = 1"}, ctx)
    assert res.ok and (tmp_path / "c.py").read_text() == "def f():\n    x = 1\n"


# --- read-before-edit gate (build 93) --------------------------------------
#
# The gate refuses a content-anchored edit to a file the model has never read.
# It is opt-in at the ToolContext level: `seen_files=None` (the `ctx` fixture
# above, and every tool constructed without a loop) behaves exactly as before,
# which is what keeps the ~770 tests preceding this section honest.


@pytest.fixture
def gated(tmp_path):
    """A ToolContext with the gate ARMED and nothing seen yet."""
    return ToolContext(cwd=str(tmp_path), seen_files=set())


async def test_gate_is_off_when_seen_files_is_none(ctx, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "x = 1", "new": "x = 2"}, ctx)
    assert res.ok and (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_edit_file_blocked_on_an_unread_file(gated, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "x = 1", "new": "x = 2"}, gated)
    assert res.is_error and "have NOT read" in res.content
    assert "read_file" in res.content
    # And critically: nothing happened to the file.
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


async def test_read_file_unlocks_the_edit(gated, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert (await fs.ReadFile().run({"path": "a.py"}, gated)).ok
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "x = 1", "new": "x = 2"}, gated)
    assert res.ok and (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_a_windowed_read_still_unlocks_the_edit(gated, tmp_path):
    # The gate is a floor against editing text the model never saw, not a
    # guarantee it saw the right part — a partial read counts.
    (tmp_path / "a.py").write_text("\n".join(f"L{i}" for i in range(1, 50)))
    assert (await fs.ReadFile().run(
        {"path": "a.py", "offset": 1, "limit": 2}, gated)).ok
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "L40", "new": "L99"}, gated)
    assert res.ok


async def test_write_file_counts_as_having_seen_it(gated, tmp_path):
    assert (await fs.WriteFile().run(
        {"path": "a.py", "content": "x = 1\n"}, gated)).ok
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "x = 1", "new": "x = 2"}, gated)
    assert res.ok and (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_append_file_does_not_count_as_having_seen_it(gated, tmp_path):
    # Appending tells the model what it added and nothing about the lines
    # above, so it must not unlock an anchored edit.
    (tmp_path / "a.py").write_text("x = 1\n")
    assert (await fs.AppendFile().run(
        {"path": "a.py", "content": "y = 2\n"}, gated)).ok
    res = await fs.EditFile().run(
        {"path": "a.py", "old": "x = 1", "new": "x = 3"}, gated)
    assert res.is_error and "have NOT read" in res.content


async def test_gate_defers_to_plain_no_such_file(gated):
    # A nonexistent path gets the clearer error, not "go read it first".
    res = await fs.EditFile().run(
        {"path": "nope.py", "old": "a", "new": "b"}, gated)
    assert res.is_error and "no such file" in res.content
    assert "have NOT read" not in res.content


async def test_gate_runs_after_missing_argument_validation(gated, tmp_path):
    # A malformed call should be told it is malformed; "read the file first"
    # would send the model off to fix the wrong thing.
    (tmp_path / "a.py").write_text("x = 1\n")
    res = await fs.EditFile().run({"path": "a.py", "old": "x = 1"}, gated)
    assert res.is_error and "have NOT read" not in res.content


async def test_replace_lines_blocked_on_an_unread_file(gated, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    res = await fs.ReplaceLines().run(
        {"path": "a.py", "start": 1, "end": 1, "new": "x = 2"}, gated)
    assert res.is_error and "have NOT read" in res.content
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


async def test_replace_lines_unlocked_by_read(gated, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert (await fs.ReadFile().run({"path": "a.py"}, gated)).ok
    res = await fs.ReplaceLines().run(
        {"path": "a.py", "start": 1, "end": 1, "new": "x = 2"}, gated)
    assert res.ok and (tmp_path / "a.py").read_text() == "x = 2\n"


async def test_seen_is_keyed_per_file(gated, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    assert (await fs.ReadFile().run({"path": "a.py"}, gated)).ok
    res = await fs.EditFile().run(
        {"path": "b.py", "old": "y = 1", "new": "y = 2"}, gated)
    assert res.is_error and "have NOT read" in res.content


# --- the syntax guard points at the right text (build 95) ---
#
# The guard used to end every rejection with "Your `new` text is malformed".
# In 31 of the 58 archived rejections whose span is knowable the break was
# OUTSIDE the supplied text — always at the first line after it — so the model
# was sent to re-inspect text that was fine and resent it byte-identical until
# the repeat guard killed the turn.

def test_changed_span_finds_the_supplied_lines():
    before = "a\nb\nc\nd\n"
    after = "a\nX\nY\nc\nd\n"
    assert fs._changed_span(before, after) == (2, 3)


def test_changed_span_of_a_pure_deletion_is_empty():
    lo, hi = fs._changed_span("a\nb\nc\n", "a\nc\n")
    assert hi < lo and lo == 2


def test_changed_span_of_an_append():
    assert fs._changed_span("a\n", "a\nb\n") == (2, 2)


def test_stranded_run_stops_when_indent_returns():
    lines = ["def f(x):", "    return 0", "        b = 2", "        c = 3",
             "    return a"]
    assert fs._stranded_run(lines, 3) == 4


def test_stranded_run_of_a_single_line():
    lines = ["def f(x):", "    return 0", "        b = 2", "    return a"]
    assert fs._stranded_run(lines, 3) == 3


async def test_break_after_the_supplied_text_blames_the_seam(ctx, tmp_path):
    # The real b87 shape: a complete replacement that ends mid-block, stranding
    # the tail of the code it replaced.
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x:\n        a = 1\n        b = 2\n    return a\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    assert res.is_error and "NOT applied" in res.content
    assert "NOT inside the text you supplied" in res.content
    assert "just after it" in res.content
    # and it must NOT send the model back to text that is fine
    assert "`new` text is malformed" not in res.content


async def test_the_seam_message_shows_the_junction(ctx, tmp_path):
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x:\n        a = 1\n        b = 2\n    return a\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    assert "<- your text ends here" in res.content
    assert "<- SyntaxError here" in res.content
    assert "b = 2" in res.content


async def test_the_seam_message_names_the_leftover_extent(ctx, tmp_path):
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x:\n        a = 1\n        b = 2\n        c = 3\n"
        "    return a\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    assert "Lines 3-4 look like the leftover tail" in res.content


async def test_the_seam_message_says_resending_will_not_help(ctx, tmp_path):
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x:\n        a = 1\n        b = 2\n    return a\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    assert "do not resend the same edit" in res.content.lower()


async def test_a_genuinely_malformed_new_still_blames_new(ctx, tmp_path):
    # The other half must not regress: when the break IS in the supplied text,
    # the old message is the correct one.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    res = await fs.EditFile().run(
        {"path": "m.py", "old": "return 1", "new": "return ("}, ctx)
    assert res.is_error
    assert "`new` text is malformed" in res.content
    assert "NOT inside the text you supplied" not in res.content


async def test_the_seam_message_never_shows_a_phantom_trailing_line(ctx, tmp_path):
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x:\n        a = 1\n        b = 2\n    return a\n")
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    # 5 real lines; a 6th numbered empty line would be the trailing-newline artifact
    assert "\n     6 |" not in res.content


async def test_seam_diagnosis_leaves_the_file_untouched(ctx, tmp_path):
    src = "def f(x):\n    if x:\n        a = 1\n        b = 2\n    return a\n"
    (tmp_path / "m.py").write_text(src)
    await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 3, "new": "    return 0"}, ctx)
    assert (tmp_path / "m.py").read_text() == src


# --- `old` is a search key, not a draft of `new` (build 96) ---
#
# 87 of 87 single-line `old`-not-found cases in the b87+ corpus had `old` closer
# to the model's own `new` (median 0.97) than to any line in the file it had
# just read (0.67). The model writes its intended replacement into both fields.
# The no-op edit is the same bug with an empty tweak. See ROADMAP 5.17.

def test_quoted_fraction_counts_lines_present_in_the_file():
    text = "def f():\n    return 1\n"
    assert fs._quoted_fraction("    return 1", text) == 1.0
    assert fs._quoted_fraction("    return 2", text) == 0.0
    assert fs._quoted_fraction("def f():\n    return 2", text) == 0.5


def test_quoted_fraction_of_blank_old_is_zero():
    assert fs._quoted_fraction("   \n\n", "x = 1\n") == 0.0


def test_authored_note_fires_when_old_is_a_draft_of_new():
    text = "def f(w):\n    elif current_len + 1 + len(w) < w:\n"
    note = fs._authored_old_note(
        "current = [w] if current_len + len(w) < w else [w]",
        "current = [w] if current_len + len(w) + 1 <= w else [w]", text)
    assert "identical" in note and "search key" in note


def test_authored_note_is_silent_on_an_elision():
    # Every line real and in order, middle dropped — the model quoted, it did
    # not invent, and it must not be told otherwise.
    text = "a = 1\nb = 2\nc = 3\nd = 4\n"
    assert fs._authored_old_note("a = 1\nd = 4", "a = 1\nd = 5", text) == ""


def test_authored_note_is_silent_when_old_and_new_differ_a_lot():
    # Invented `old`, but not a draft of `new` — that is the wrong-file shape,
    # which the existing "nothing resembles `old`" branch already answers.
    text = "alpha\nbravo\n"
    assert fs._authored_old_note("zzzzzzzz", "completely different text", text) == ""


def test_authored_note_is_silent_without_a_new():
    text = "alpha\nbravo\n"
    assert fs._authored_old_note("zzzz", "", text) == ""


async def test_not_found_reply_names_the_old_is_new_confusion(ctx, tmp_path):
    (tmp_path / "m.py").write_text(
        "def f(width):\n    if current_len + 1 + len(word) < width:\n        pass\n")
    # The corpus shape: `old` is ~0.6 from the closest real line (so no tier
    # matches it) but ~0.97 from the model's own `new`.
    res = await fs.EditFile().run(
        {"path": "m.py",
         "old": "    current = [word] if current_len + len(word) < width else [word]",
         "new": "    current = [word] if current_len + len(word) + 1 <= width else [word]"},
        ctx)
    assert res.is_error
    assert "search key" in res.content
    assert "RIGHT NOW" in res.content


async def test_the_elision_reply_does_not_accuse_the_model_of_inventing(ctx, tmp_path):
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    res = await fs.EditFile().run(
        {"path": "m.py", "old": "a = 1\nd = 4", "new": "a = 1\nd = 5"}, ctx)
    assert res.is_error
    assert "search key" not in res.content


async def test_the_copy_me_block_is_not_run_into_by_the_advice(ctx, tmp_path):
    # 5.16 defect 1: advice was concatenated with a leading SPACE onto the last
    # line of a block the model is told to copy verbatim, so the block's final
    # line read `    bravo = 2 If the target text is hard to reproduce…`.
    # Build 105 replaced that trailer on this path; the invariant it protects
    # did not change — whatever follows the block must start on its own line.
    (tmp_path / "m.py").write_text(
        "def f():\n    alpha = 1\n    bravo = 2\n    return alpha\n")
    res = await fs.EditFile().run(
        {"path": "m.py", "old": "    total_width = 1",
         "new": "    total_width = 2"}, ctx)
    assert res.is_error
    block_end = res.content.index("    bravo = 2") + len("    bravo = 2")
    assert res.content[block_end] == "\n"
    assert " If the target text is hard to reproduce" not in res.content


async def test_no_op_message_names_the_same_misconception(ctx, tmp_path):
    # The degenerate case of 5.17 — the tweak between the two fields came out
    # empty, so the key matches and the edit changes nothing. Build 110: this
    # diagnosis only holds when `old` is NOT in the file; when it is, the edit
    # is redundant rather than malformed (see the pair of tests below).
    (tmp_path / "m.py").write_text("x = 1\n")
    res = await fs.EditFile().run(
        {"path": "m.py", "old": "y = 2", "new": "y = 2"}, ctx)
    assert res.is_error
    assert "both fields" in res.content.lower()


# --- build 119 REVERSES build 98's ordering. 98 put replace_lines first
#     because b97's extend-`old` route caused 20 syntax refusals — but 5.52
#     measured what winning looks like: the runs that verify rewrite whole
#     blocks (median `new` 673 chars) and the runs that die do single-line
#     surgery (p90 79 chars across 346 edits) and never re-derive the logic.
#     replace_lines converts at 76/76 and is the LOSING branch's route, because
#     what it prescribes ("only the replacement line as `new`") is the losing
#     strategy. b97's syntax disaster is guarded against by removing the gutter
#     the model used to strip, per 5.28's stated precondition. ROADMAP 5.53.

async def test_ambiguous_leads_with_copying_the_block_not_replace_lines(ctx, tmp_path):
    (tmp_path / "m.py").write_text("a = 1\nb = 2\na = 1\n")
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.EditFile().run({"path": "m.py", "old": "a = 1", "new": "a = 3"}, ctx)
    assert res.is_error
    body = res.content
    assert body.index("VERBATIM into `old`") < body.index("replace_all")
    assert body.index("VERBATIM into `old`") < body.index("replace_lines")


async def test_ambiguous_says_the_bug_may_be_a_neighbouring_line(ctx, tmp_path):
    # The 5.52 mechanism: read-first runs mis-localize, patching a plausible
    # line one below the real defect. For the exec-bugfix `old` they actually
    # send, the buggy comparison is IN the +/-1 window.
    (tmp_path / "m.py").write_text("a = 1\nb = 2\na = 1\n")
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.EditFile().run({"path": "m.py", "old": "a = 1", "new": "a = 3"}, ctx)
    assert "one of THEM rather than the line you first picked" in res.content


async def test_ambiguous_promises_each_block_is_unique(ctx, tmp_path):
    # The message tells the model to copy a block into `old`; that advice has
    # to be true or it fails ambiguously a second time.
    (tmp_path / "m.py").write_text("a = 1\nb = 2\na = 1\n")
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.EditFile().run({"path": "m.py", "old": "a = 1", "new": "a = 3"}, ctx)
    assert "exactly ONCE" in res.content


async def test_ambiguous_still_shows_the_surrounding_lines(ctx, tmp_path):
    # Build 97's rendering is kept — not-found went to zero in its sweep.
    (tmp_path / "m.py").write_text("a = 1\nb = 2\na = 1\n")
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.EditFile().run({"path": "m.py", "old": "a = 1", "new": "a = 3"}, ctx)
    assert "match at line 1" in res.content and "match at line 3" in res.content


# --- replace_lines rescues a block sent at the wrong column (build 98 / 5.18) ---

def test_reindent_shifts_a_block_right():
    assert fs._reindent_to("a = 1\nb = 2", 4) == "    a = 1\n    b = 2"


def test_reindent_preserves_relative_structure():
    assert fs._reindent_to("if x:\n    y = 1", 8) == "        if x:\n            y = 1"


def test_reindent_leaves_blank_lines_empty():
    assert fs._reindent_to("a = 1\n\nb = 2", 2) == "  a = 1\n\n  b = 2"


def test_reindent_refuses_a_left_shift_that_would_cut_code():
    assert fs._reindent_to("        a = 1\nb = 2", 4) is None


def test_reindent_right_shift_is_not_blocked_by_a_shallow_line():
    assert fs._reindent_to("  a = 1\nb = 2", 4) == "    a = 1\n  b = 2"


def test_reindent_of_blank_new_is_none():
    assert fs._reindent_to("\n  \n", 4) is None


def test_reindent_that_changes_nothing_is_none():
    assert fs._reindent_to("    a = 1", 4) is None


def test_indent_of_out_of_range_is_zero():
    assert fs._indent_of("a = 1\n", 99) == 0


async def test_replace_lines_rescues_a_column_zero_block(ctx, tmp_path):
    src = ("def f(words):\n"
           "    out = []\n"
           "    for word in words:\n"
           "        out.append(word)\n"
           "        total = len(word)\n"
           "    return out\n")
    (tmp_path / "m.py").write_text(src)
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 4, "end": 5,
         "new": "out.append(word)\ntotal = len(word) + 1"}, ctx)
    assert res.ok, res.content
    assert "Indentation adjusted" in res.content
    body = (tmp_path / "m.py").read_text()
    assert "        total = len(word) + 1" in body
    compile(body, "m.py", "exec")


async def test_replace_lines_still_refuses_a_genuinely_broken_block(ctx, tmp_path):
    src = "def f():\n    a = 1\n    return a\n"
    (tmp_path / "m.py").write_text(src)
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 2, "new": "    a = (1"}, ctx)
    assert res.is_error
    assert (tmp_path / "m.py").read_text() == src


async def test_a_correctly_indented_replace_lines_is_untouched(ctx, tmp_path):
    src = "def f():\n    a = 1\n    return a\n"
    (tmp_path / "m.py").write_text(src)
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.ReplaceLines().run(
        {"path": "m.py", "start": 2, "end": 2, "new": "    a = 2"}, ctx)
    assert res.ok
    assert "Indentation adjusted" not in res.content
    assert (tmp_path / "m.py").read_text() == "def f():\n    a = 2\n    return a\n"


def test_the_column_hint_names_the_mismatch():
    src = "def f():\n    if x:\n        a = 1\n    return a\n"
    assert "column 8" in fs._column_hint(src, 3, "a = (1")


def test_the_column_hint_is_silent_when_the_column_is_right():
    src = "def f():\n    a = 1\n"
    assert fs._column_hint(src, 2, "    a = (1") == ""


# --- the not-found routes, reordered by landing rate (build 105 / ROADMAP 5.25)
# 5.22a measured what a model does after a miss: `old` rewritten from memory
# lands 1/41 (2%), a retry after re-reading 31/46 (67%), `replace_lines` 16/16,
# `write_file` 17/17. The message led with the 2% route and gated the 100% one
# behind "if the target text is hard to reproduce EXACTLY" — a condition a model
# that believes its `old` WAS exact will never match. 5.20b: first is taken.

async def test_not_found_leads_with_replace_lines_and_real_numbers(ctx, tmp_path):
    src = ("def wrap(text, width):\n"
           "    out = []\n"
           "    current = []\n"
           "    for word in text.split():\n"
           "        if len(word) < width:\n"
           "            current.append(word)\n"
           "    return out\n")
    (tmp_path / "m.py").write_text(src)
    await fs.ReadFile().run({"path": "m.py"}, ctx)
    res = await fs.EditFile().run(
        {"path": "m.py", "old": "        if len(word) + len(current) <= width:",
         "new": "        if len(word) + len(current) <= width - 1:"}, ctx)
    assert res.is_error                       # too far off for the fuzzy tier
    assert "`replace_lines` with start=" in res.content
    # It leads the ROUTES; build 96's authored-old diagnosis still precedes the
    # whole message when it fires, which is deliberate (it is a diagnosis, not
    # a route) and flagged in 5.25 as its own open question.
    assert (res.content.index("replace_lines` with start=")
            < res.content.index("copy your `old`"))


def test_the_100pct_route_precedes_the_67pct_route():
    from pathlib import Path
    body = "def f():\n    a = 1\n    return a\n"
    out = fs._not_found_help(body, "    a = 2", Path("m.py"), new="    a = 3")
    assert out.index("replace_lines` with start=") < out.index("copy your `old`")


def test_the_header_no_longer_leads_with_the_2pct_route():
    from pathlib import Path
    out = fs._not_found_help("def f():\n    a = 1\n    return a\n", "    a = 2",
                             Path("m.py"), new="    a = 3")
    assert "Copy the target text EXACTLY" not in out


def test_the_route_states_absolute_indentation():
    assert "indentation they should have in the file" in fs._replace_lines_route(4, 6)


def test_a_multi_line_route_says_new_replaces_all_of_them():
    # The range is the displayed block, window included, so a `new` holding only
    # the changed line would silently delete its neighbours.
    assert "carry the unchanged lines across" in fs._replace_lines_route(4, 6)
    assert "carry the unchanged lines across" not in fs._replace_lines_route(7, 7)


def test_the_route_helper_formats_a_single_line_span():
    out = fs._replace_lines_route(7, 7)
    assert "start=7, end=7" in out and "that line" in out


def test_the_stated_range_is_the_block_that_follows_it():
    # A number that does not match the printed block would send a correct edit
    # to the wrong place — worse than giving no number at all.
    from pathlib import Path
    body = "\n".join(f"line_{i}" for i in range(20))
    out = fs._not_found_help(body, "line_9x", Path("m.py"))
    start = int(out.split("start=")[1].split(",")[0])
    end = int(out.split("end=")[1].split(" ")[0])
    shown = out.split(f"at lines {start}-{end}:\n")[1].split("\n\nOr copy")[0]
    assert shown.split("\n") == body.split("\n")[start - 1:end]


def test_no_numbered_route_when_nothing_resembles_old():
    from pathlib import Path
    out = fs._not_found_help("alpha\nbravo\ncharlie\n", "zzzzzzzz", Path("m.py"))
    assert "replace_lines` with start=" not in out


def test_the_route_is_silent_on_a_truncated_block():
    # _HELP_MAX_LINES truncation means the stated end line would be wrong.
    from pathlib import Path
    body = "\n".join(f"line_{i}" for i in range(200))
    old = "\n".join(f"line_{i}" for i in range(50, 130)).replace("line_60",
                                                                 "line_6O")
    out = fs._not_found_help(body, old, Path("m.py"))
    assert "more lines)" in out
    assert "replace_lines` with start=" not in out


def test_the_memory_warning_closes_every_not_found():
    from pathlib import Path
    for text, old in (("def f():\n    a = 1\n    return a\n", "    a = 2"),
                      ("alpha\nbravo\ncharlie\n", "zzzzzzzz")):
        out = fs._not_found_help(text, old, Path("m.py"), new="x")
        assert out.rstrip().endswith("lands 1 time in 41.")


def test_the_unconfident_branch_keeps_the_gated_hatch():
    # No trustworthy numbers to offer there, so the old conditional advice is
    # still the only replace_lines pointer that path can honestly give.
    from pathlib import Path
    out = fs._not_found_help("alpha\nbravo\ncharlie\n", "zzzzzzzz", Path("m.py"))
    assert "use replace_lines" in out


def test_the_noop_paths_are_untouched():
    # _TRY_REPLACE_LINES is dropped from the CONFIDENT not-found path only; the
    # two no-op call sites have no located block and nothing to fill in.
    assert "hard to reproduce EXACTLY" in fs._TRY_REPLACE_LINES


# --- build 106 / 5.29: a multi-line `new` keeps its shape when spliced -------

def test_a_relative_multiline_new_is_rescued():
    # The reproduced bug: `new` written relative from column 0, spliced into an
    # indented block, lost its shape and the model was told ITS text was bad.
    text = ("def wrap(text, width):\n"
            "    lines = []\n"
            "    if current:\n"
            '        lines.append(" ".join(current))\n'
            "    return lines\n")
    old = "if current:\n    lines.append(' '.join(current))\nreturn lines"
    new = "if current:\n    lines.append(x)\n    lines.append(y)\nreturn lines"
    upd, _note, status, _n = fs.try_edit(text, old, new, False, Path("m.py"))
    assert status == "ok"
    compile(upd, "m.py", "exec")          # the whole point


def test_an_absolute_later_line_is_left_alone():
    # The OTHER real shape: first line dedented, later lines already at the
    # file's own columns. The strip-only splice is correct here, so the rescue
    # must not fire — this is the regression the anchor could have caused.
    text = "class A:\n    x = 1\n    y = 2\n"
    upd, _n, status, _c = fs.try_edit(text, "x = 1\ny = 2",
                                      "x = 99\n    y = 2", False, Path("m.py"))
    assert status == "ok" and upd == "class A:\n    x = 99\n    y = 2\n"


def test_the_rescue_needs_a_python_path():
    # No path (the diff preview's old signature, or a non-.py file): status quo.
    text = "def f():\n    if x:\n        p()\n"
    old, new = "if x:\n    p()", "if x:\n    p()\n    q()"
    plain, _n, status, _c = fs.try_edit(text, old, new, False)
    assert status == "ok" and "    if x:\n    p()" in plain   # unrescued
    txt, _n2, _s2, _c2 = fs.try_edit(text, old, new, False, Path("m.txt"))
    assert txt == plain


def test_the_rescue_stays_out_of_an_already_broken_file():
    # The file did not parse before the edit, so we cannot read a SyntaxError
    # after it as evidence of anything. Leave the model's text alone.
    text = "def f(:\n    if x:\n        p()\n"
    upd, _n, status, _c = fs.try_edit(text, "if x:\n    p()",
                                      "if x:\n    p()\n    q()", False,
                                      Path("m.py"))
    assert status == "ok" and "    if x:\n    p()\n    q()" in upd


def test_the_rescue_reaches_the_fuzzy_tier():
    text = ("def f():\n"
            "    if ready:\n"
            "        run()\n")
    # `old` drifts enough to miss the tolerant tier but still match fuzzily.
    upd, note, status, _c = fs.try_edit(text, "if ready :\n    run( )",
                                        "if ready:\n    run()\n    log()",
                                        False, Path("m.py"))
    assert status == "ok" and "fuzzy" in note
    compile(upd, "m.py", "exec")


def test_an_absolute_multiline_new_is_unchanged_by_the_anchor():
    # A model that already wrote the file's own columns must get byte-identical
    # output to the pre-106 behaviour.
    assert fs._anchor_new("    a = 1\n    b = 2", 4) == "a = 1\n    b = 2"


def test_the_anchor_shifts_the_later_lines_onto_the_base():
    assert fs._anchor_new("a = 1\n    b = 2", 8) == "a = 1\n            b = 2"


def test_the_anchor_keeps_blank_lines_empty():
    assert fs._anchor_new("a = 1\n\n    b = 2", 4) == "a = 1\n\n        b = 2"


def test_the_anchor_declines_on_tabs():
    assert fs._anchor_new("a = 1\n\tb = 2", 4) == "a = 1\n\tb = 2"


def test_the_anchor_declines_when_a_later_line_is_shallower():
    # Shifting would have to cut into real characters, so leave it alone.
    assert fs._anchor_new("    a = 1\nb = 2", 8) == "a = 1\nb = 2"


def test_the_anchor_is_a_no_op_on_a_single_line():
    assert fs._anchor_new("    a = 1", 8) == "a = 1"


def test_span_base_reports_the_indent_column():
    text = "def f():\n    a = 1\n"
    assert fs._span_base(text, text.index("a = 1")) == 4


def test_span_base_declines_mid_line():
    text = "def f():\n    a = 1\n"
    assert fs._span_base(text, text.index("= 1")) is None


def test_the_exact_tier_is_untouched():
    text = "def f():\n    a = 1\n    b = 2\n"
    upd, _n, status, _c = fs.try_edit(text, "    a = 1\n    b = 2",
                                      "    a = 9\n    b = 8", False,
                                      Path("m.py"))
    assert status == "ok" and upd == "def f():\n    a = 9\n    b = 8\n"


def test_replace_all_rescues_each_span_at_its_own_depth():
    text = ("def f():\n    if x:\n        p()\n"
            "def g():\n        if x:\n            p()\n")
    upd, _n, status, _c = fs.try_edit(text, "if x:\n    p()",
                                      "if x:\n    p()\n    q()", True,
                                      Path("m.py"))
    assert status == "ok"
    compile(upd, "m.py", "exec")


def test_the_rescued_edit_actually_lands_through_edit_file(tmp_path):
    import asyncio
    (tmp_path / "w.py").write_text("def f():\n    if x:\n        p()\n")
    ctx = ToolContext(cwd=str(tmp_path))
    res = asyncio.run(fs.EditFile().run(
        {"path": "w.py", "old": "if x:\n    p()",
         "new": "if x:\n    p()\n    q()"}, ctx))
    assert res.ok, res.content
    compile((tmp_path / "w.py").read_text(), "w.py", "exec")


# --- build 107 / 5.30: the EXACT tier splices mid-line too --------------------

def test_a_dedented_old_matches_mid_line_and_is_still_rescued():
    # `old` written without the file's indentation is a SUBSTRING of the
    # indented line, so text.count() finds it and the "exact" tier splices into
    # the middle of that line. This was the entire population of b106-indent.
    text = ("def truncate(text, limit, suffix):\n"
            "    cut = limit - len(suffix)\n"
            "    return text[:cut] + suffix\n")
    new = ("if cut > 0:\n    return text[:cut] + suffix\n"
           "else:\n    return suffix")
    upd, note, status, _c = fs.try_edit(text, "return text[:cut] + suffix", new,
                                        False, Path("m.py"))
    assert status == "ok" and "re-indented" in note
    compile(upd, "m.py", "exec")
    assert "    if cut > 0:\n        return text[:cut] + suffix\n" in upd


def test_a_byte_exact_old_still_replaces_verbatim():
    # The exact tier must NOT strip `new` the way the span tiers do: here `old`
    # carries the file's indentation, so `new` is already in its coordinates.
    text = "def f():\n    a = 1\n"
    upd, note, status, _c = fs.try_edit(text, "    a = 1", "    a = 2", False,
                                        Path("m.py"))
    assert status == "ok" and note == "" and upd == "def f():\n    a = 2\n"


def test_the_exact_tier_does_not_anchor_at_column_zero():
    # Nothing to anchor onto — the match starts the line. Status quo.
    text = "a = 1\nb = 2\n"
    upd, note, status, _c = fs.try_edit(text, "a = 1", "a = 1\nc = 3", False,
                                        Path("m.py"))
    assert status == "ok" and note == "" and upd == "a = 1\nc = 3\nb = 2\n"


def test_a_genuinely_malformed_new_is_still_left_broken():
    # `else` shallower than its own `if`: the model's text really is wrong, and
    # no shift fixes it. Rescue declines and the syntax guard gets to speak.
    text = "def f():\n    return 1\n"
    new = "if x:\n            return 1\n        else:\n            return 2"
    upd, note, status, _c = fs.try_edit(text, "return 1", new, False,
                                        Path("m.py"))
    assert status == "ok" and note == ""
    assert not fs._parses_py(upd, Path("m.py"))


def test_replace_all_through_the_exact_tier_keeps_its_count():
    text = "x = 1\ny = 1\n"
    upd, _n, status, count = fs.try_edit(text, "= 1", "= 2", True, Path("m.py"))
    assert status == "ok" and count == 2 and upd == "x = 2\ny = 2\n"
