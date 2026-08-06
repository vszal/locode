"""Tests for project-instruction discovery and rendering.

The load path feeds the system prompt, so the two properties that matter most
are negative ones: a repo with no instruction files must contribute EXACTLY
nothing (not a stray header), and discovery must never climb out of the repo
into the user's home directory.
"""

import pytest

from locode.context import (
    DEFAULT_FILENAMES,
    find_instruction_files,
    load_project_instructions,
)


@pytest.fixture
def repo(tmp_path):
    """A git repo with a package subdirectory, and no instruction files yet."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pkg").mkdir()
    return tmp_path


def test_no_files_renders_nothing(repo):
    assert load_project_instructions(repo) == ""


def test_finds_agents_md_at_root(repo):
    (repo / "AGENTS.md").write_text("use tabs")
    assert find_instruction_files(repo) == [repo / "AGENTS.md"]
    out = load_project_instructions(repo)
    assert "use tabs" in out
    assert "AGENTS.md" in out


def test_root_first_so_nearest_file_wins(repo):
    (repo / "AGENTS.md").write_text("repo rule")
    (repo / "pkg" / "AGENTS.md").write_text("pkg rule")
    found = find_instruction_files(repo / "pkg")
    assert found == [repo / "AGENTS.md", repo / "pkg" / "AGENTS.md"]
    out = load_project_instructions(repo / "pkg")
    assert out.index("repo rule") < out.index("pkg rule")


def test_labels_are_repo_relative(repo):
    (repo / "pkg" / "AGENTS.md").write_text("x")
    out = load_project_instructions(repo / "pkg")
    # "pkg/AGENTS.md", not an absolute path leaking the user's home directory
    assert "## From pkg/AGENTS.md" in out
    assert str(repo) not in out


def test_locode_md_is_read_too(repo):
    (repo / "LOCODE.md").write_text("locode-specific")
    assert "locode-specific" in load_project_instructions(repo)


def test_claude_md_is_not_read_by_default(repo):
    (repo / "CLAUDE.md").write_text("another tool's rules")
    assert load_project_instructions(repo) == ""
    # ...but opting in works
    out = load_project_instructions(repo, filenames=("CLAUDE.md",))
    assert "another tool's rules" in out


def test_does_not_escape_the_repo(repo):
    """A file above the repo root must not be picked up."""
    (repo.parent / "AGENTS.md").write_text("stray home-directory rules")
    (repo / "AGENTS.md").write_text("repo rules")
    found = find_instruction_files(repo)
    assert found == [repo / "AGENTS.md"]


def test_outside_a_repo_only_cwd_is_consulted(tmp_path):
    """No .git anywhere: consult cwd alone rather than walking to /."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "AGENTS.md").write_text("parent")
    (tmp_path / "sub" / "AGENTS.md").write_text("here")
    assert find_instruction_files(tmp_path / "sub") == [
        tmp_path / "sub" / "AGENTS.md"]


def test_worktree_dot_git_file_still_finds_the_root(tmp_path):
    """In a git worktree .git is a FILE; the eval harness runs agents there."""
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt")
    (tmp_path / "AGENTS.md").write_text("worktree rules")
    assert find_instruction_files(tmp_path) == [tmp_path / "AGENTS.md"]


def test_empty_file_contributes_nothing(repo):
    (repo / "AGENTS.md").write_text("   \n\n  ")
    assert load_project_instructions(repo) == ""


def test_budget_truncates_and_says_so(repo):
    (repo / "AGENTS.md").write_text("x" * 500)
    out = load_project_instructions(repo, max_chars=100)
    assert "truncated at 100 characters" in out
    assert "x" * 100 in out
    assert "x" * 101 not in out


def test_budget_spent_in_order_root_first(repo):
    """The repo-wide file must not be starved by a verbose subdirectory one."""
    (repo / "AGENTS.md").write_text("R" * 100)
    (repo / "pkg" / "AGENTS.md").write_text("P" * 100)
    out = load_project_instructions(repo / "pkg", max_chars=100)
    assert "R" * 100 in out
    assert "P" * 10 not in out  # not "P" — the header itself says "Project"
    assert "1 further instruction file(s) omitted" in out


def test_default_filenames_are_the_documented_pair():
    assert DEFAULT_FILENAMES == ("AGENTS.md", "LOCODE.md")


# --- wiring: config merge + the system prompt --------------------------------
# The [context] section was silently ignored on the first cut (Config._merge_toml
# has to name every section explicitly), so the defaults looked correct while no
# override took. Both halves are pinned here.

def test_context_section_merges_from_toml(tmp_path):
    from locode.config import Config
    p = tmp_path / "c.toml"
    p.write_text('[context]\ninstruction_files = ["CLAUDE.md"]\n'
                 'max_instruction_chars = 0\n')
    cfg = Config.load(p)
    assert cfg.context.instruction_files == ["CLAUDE.md"]
    assert cfg.context.max_instruction_chars == 0


def test_example_config_matches_the_defaults(tmp_path):
    """config.toml.example is the documented reference; drift is a bug."""
    import pathlib
    from locode.config import Config
    p = tmp_path / "c.toml"
    p.write_text(pathlib.Path("config.toml.example").read_text())
    loaded, default = Config.load(p).context, Config().context
    assert loaded.instruction_files == default.instruction_files
    assert loaded.max_instruction_chars == default.max_instruction_chars


def _loop_at(tmp_path, cfg=None):
    from locode.agent.loop import AgentLoop
    from locode.config import Config
    from locode.permissions import PermissionPolicy
    from locode.tools.base import Registry
    from locode.tools import fs
    reg = Registry()
    for t in fs.all_tools():
        reg.register(t)
    cfg = cfg or Config()
    return AgentLoop(object(), object(), reg, PermissionPolicy(cfg.permissions),
                     cfg, cwd=str(tmp_path))


def test_instructions_reach_the_system_prompt(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("Always run the linter.")
    prompt = _loop_at(tmp_path).history[0]["content"]
    assert "Always run the linter." in prompt
    assert "# Project instructions" in prompt


def test_no_instructions_leaves_the_prompt_untouched(tmp_path):
    (tmp_path / ".git").mkdir()
    prompt = _loop_at(tmp_path).history[0]["content"]
    assert "Project instructions" not in prompt


def test_zero_budget_disables_the_feature(tmp_path):
    from locode.config import Config
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("Always run the linter.")
    cfg = Config()
    cfg.context.max_instruction_chars = 0
    prompt = _loop_at(tmp_path, cfg).history[0]["content"]
    assert "Always run the linter." not in prompt
