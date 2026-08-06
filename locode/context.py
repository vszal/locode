"""Project instructions — the repository's own house rules, folded into the
system prompt.

Every comparable tool reads a conventions file (Aider's CONVENTIONS.md, Cline's
.clinerules, Claude Code's CLAUDE.md, and the cross-tool AGENTS.md convention);
locode read none, so it ignored even its own AGENTS.md. For a 9B local model
this is the cheapest steering available: the rules land in the system prompt,
which is stable and sits first, so the server's prompt cache reuses it across
every turn in the session and it costs one prefill, once.

Two deliberate choices, both reversible via config:

- **Which files.** AGENTS.md (the convention several tools now share, and what
  this repo already has) and LOCODE.md (a locode-specific override for a repo
  that wants to say something different to us than to other agents). CLAUDE.md
  is NOT read by default — it belongs to another tool, and silently absorbing
  another vendor's instructions is a surprise, not a feature. Users who want it
  can add it to `context.instruction_files`.

- **A hard character budget.** A local model's context is ~32K tokens, and an
  unbounded instructions file would eat it before the conversation starts. The
  budget is enforced per rendered block, and a truncated file says so rather
  than trailing off mid-sentence.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_FILENAMES = ("AGENTS.md", "LOCODE.md")
DEFAULT_MAX_CHARS = 8000

_HEADER = (
    "# Project instructions\n"
    "House rules for this repository, from its own instruction files. They "
    "describe how work here is expected to be done. Follow them; where they "
    "conflict with a general habit of yours, they win. Where they conflict "
    "with the user's direct request, the user wins."
)


def _repo_root(start: Path) -> Path | None:
    """The nearest ancestor holding a .git entry, or None outside a repo.

    A worktree's .git is a FILE, not a directory, so this tests existence
    rather than is_dir() — locode's own eval harness runs agents inside git
    worktrees, and an is_dir() check would find no root for exactly the runs we
    most want instrumented.
    """
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


def find_instruction_files(cwd: str | Path,
                           filenames=DEFAULT_FILENAMES) -> list[Path]:
    """Instruction files from the repo root down to `cwd`, root first.

    Root first so that the most specific file is read LAST: a package
    subdirectory that overrides a repo-wide rule should have the final word,
    which is the same precedence a reader would assume.

    Outside a git repo only `cwd` itself is consulted — walking to the
    filesystem root would let a stray ~/AGENTS.md leak into every unrelated
    session.
    """
    cwd = Path(cwd).resolve()
    root = _repo_root(cwd)
    if root is None:
        chain = [cwd]
    else:
        rel = cwd.relative_to(root)
        chain = [root]
        for part in rel.parts:
            chain.append(chain[-1] / part)
    found = []
    for d in chain:
        for name in filenames:
            p = d / name
            if p.is_file():
                found.append(p)
    return found


def load_project_instructions(cwd: str | Path,
                              filenames=DEFAULT_FILENAMES,
                              max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """The rendered instructions block for `build_system_prompt(extra=...)`,
    or "" when the repo carries none (the overwhelmingly common case, and it
    must add nothing at all to the prompt).

    The budget is spent in file order, so a repo-wide AGENTS.md is never
    starved by a verbose subdirectory one; the file that overflows is cut with
    a visible marker and the rest are dropped with a count, because a model
    that silently receives half its rules cannot tell that anything is missing.
    """
    files = find_instruction_files(cwd, filenames)
    if not files:
        return ""
    root = _repo_root(Path(cwd).resolve())
    chunks, used, dropped = [], 0, 0
    for p in files:
        try:
            text = p.read_text("utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        label = str(p.relative_to(root)) if root else p.name
        room = max_chars - used
        if room <= 0:
            dropped += 1
            continue
        if len(text) > room:
            text = text[:room] + f"\n… (truncated at {max_chars} characters)"
        chunks.append(f"## From {label}\n{text}")
        used += len(text)
    if not chunks:
        return ""
    if dropped:
        chunks.append(f"({dropped} further instruction file(s) omitted — the "
                      f"{max_chars}-character budget was already spent.)")
    return "\n\n".join([_HEADER, *chunks])
