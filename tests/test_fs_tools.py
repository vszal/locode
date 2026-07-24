import pytest

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
    assert "line 1:" in res.content and "line 3:" in res.content and "line 5:" in res.content
    assert (tmp_path / "c.py").read_text() == "x = 1\ny = 2\nx = 1\nz = 3\nx = 1\n"


async def test_edit_file_noop_is_refused_with_actionable_help(ctx, tmp_path):
    # old == new does nothing; the biggest unrecovered edit failure in eval. The
    # message must push the model to a corrected `new` or to look elsewhere,
    # not just restate the rejection.
    (tmp_path / "c.py").write_text("value = compute()\n")
    res = await fs.EditFile().run(
        {"path": "c.py", "old": "value = compute()", "new": "value = compute()"}, ctx)
    assert res.is_error
    assert "identical" in res.content.lower() or "does nothing" in res.content.lower()
    assert "different" in res.content.lower()
    assert (tmp_path / "c.py").read_text() == "value = compute()\n"


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


def test_not_found_help_shows_verbatim_snippet_and_caveat():
    from pathlib import Path
    msg = fs._not_found_help("import os\ndef handle_click(self):\n    pass\n",
                             "def handle_clik(self):", Path("c.py"))
    assert "line-number prefixes" in msg
    assert "around line 2" in msg
    assert "def handle_click(self):" in msg   # verbatim, copyable
    assert "    pass" in msg                   # includes a line of context


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


async def test_edit_that_introduces_a_syntax_error_warns(ctx, tmp_path):
    (tmp_path / "envcfg.py").write_text("def load():\n    return 1\n")
    res = await fs.EditFile().run(
        {"path": "envcfg.py", "old": "return 1", "new": "return ("}, ctx)
    assert res.ok  # the edit applied
    assert "edited" in res.content
    assert "SyntaxError" in res.content


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
