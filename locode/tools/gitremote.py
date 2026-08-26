"""Name the one empty result that is a broken command rather than a true answer.

A local model asked what a remote repository contains reaches for the git it
knows and writes `git ls-tree -r --name-only main <URL> <path>`. That command
is not merely wrong, it is wrong in the worst available way: `ls-tree` reads the
LOCAL object database, so the URL is parsed as a **pathspec**, matches nothing,
and the command exits 0 having printed nothing. It is byte-for-byte
indistinguishable from the same query correctly answering "nothing matched".

Observed live (2026-08-15): eight iterations, seven tool calls, four nudges,
turn dead. The model dropped the pathspec — the only remedy the empty-results
nudge offered it — which could never have worked, because the fault was never
the pathspec. Nothing in the harness misfired; the empty-results nudge names
"the path, the ref, the directory, the pattern or the repo", every one of them
a claim about the TARGET, and here the target was fine and the VERB was wrong.

WHAT IS ACTUALLY IN THIS CLASS (measured 2026-08-26, not assumed)
-----------------------------------------------------------------
The failure is not "local-only git subcommands accept URLs". Handed a URL where
a *revision* goes, git is loud and helpful:

    git show|log|rev-list|diff-tree main <URL>  -> fatal: invalid object name 'file'.
    git cat-file -p main <URL>                  -> fatal: too many arguments

Those need nothing from us. The silent class is exactly **a URL landing in a
PATHSPEC position**, where matching nothing is a legitimate answer:

    git ls-tree -r --name-only main <URL> [path]   -> exit 0, no output
    git ls-files <URL> / git ls-files -- <URL>     -> exit 0, no output
    git log --oneline [main] -- <URL>              -> exit 0, no output
    git diff main -- <URL>                         -> exit 0, no output

`git grep -- <URL>` prints nothing too but exits 1, so it is already an error
and the loop's error-side guards see it.

WHY THIS IS SAFE TO BE BROAD
----------------------------
`hint()` is only ever consulted for a command that ALREADY exited 0 with no
output whatsoever. A false positive therefore requires a local-only git command
that legitimately matched nothing *and* carried a URL-shaped argument — which is
the bug itself. So the subcommand list can afford to be generous rather than
model git's argument grammar.

Pure function of the command string; returns None — no hint, this empty result
is the model's ordinary business — for everything else.
"""

from __future__ import annotations

import re
import shlex

_SEGMENT_RE = re.compile(r"&&|\|\||;|\||\n")

# Subcommands that read only the repository in the current directory AND take
# pathspecs, so a URL among their arguments is silently matched against nothing.
_LOCAL_ONLY = frozenset({
    "ls-tree", "ls-files", "log", "diff", "grep", "show", "rev-list",
    "diff-tree", "cat-file", "blame", "shortlog", "whatchanged",
})

# git options that swallow the next token, which must not be read as the
# subcommand (`git -C repo ls-tree ...`, `git -c k=v log ...`).
_OPTS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                              "--namespace", "--exec-path"})

_URLISH = re.compile(
    r"""^(?:(?:https?|git|ssh|ftps?|file|rsync)://\S*     # scheme://...
        |[\w.+-]+@[\w.-]+:[^\s:]+                          # scp-style git@host:path
        )$""",
    re.X | re.I)


def _url_in(tokens: list[str]) -> str:
    for tok in tokens:
        if _URLISH.match(tok):
            return tok
    return ""


def _parse_git(segment: str) -> tuple[str, list[str]] | None:
    """`(subcommand, tokens)` for a segment that invokes git, else None."""
    try:
        tokens = shlex.split(segment)
    except ValueError:          # unbalanced quotes — not ours to interpret
        return None
    # Step past leading `VAR=value` assignments and `sudo`/`command`.
    i = 0
    while i < len(tokens) and (re.match(r"^\w+=", tokens[i])
                               or tokens[i] in ("sudo", "command", "env")):
        i += 1
    if i >= len(tokens) or tokens[i].rsplit("/", 1)[-1] != "git":
        return None
    i += 1
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] in _OPTS_WITH_VALUE:
            i += 1              # skip its value too
        i += 1
    if i >= len(tokens):
        return None
    return tokens[i], tokens[i + 1:]


def hint(cmd: str) -> str | None:
    """The replacement result for `cmd`, or None to leave the empty result alone.

    Only called when `cmd` exited 0 and printed nothing at all.
    """
    for segment in _SEGMENT_RE.split(cmd or ""):
        parsed = _parse_git(segment)
        if not parsed:
            continue
        sub, rest = parsed
        if sub not in _LOCAL_ONLY:
            continue
        url = _url_in(rest)
        if url:
            return _message(sub, url)
    return None


def _message(sub: str, url: str) -> str:
    """Lead with the call that works, then say why this one never can.

    Rule 9: name the required command and put it first — this model acts on
    whichever demand a message opens with. The explanation is second because it
    is what stops the model rephrasing, not what tells it where to go. And the
    one hypothesis the empty-results nudge cannot reach is stated flatly: the
    output is empty because of the COMMAND, not because of what it asked for.
    """
    return (f"(exit 0, no output — and this is NOT the answer to your question. "
            f"Clone the repository first, then ask the clone:\n"
            f"    git clone {url} upstream\n"
            f"then run your query with `-C upstream` or inside `upstream/`. "
            f"(`git ls-remote {url}` lists its branches and tags without a clone.)\n"
            f"Why this call printed nothing: `git {sub}` reads only the "
            f"repository in the current directory — it cannot take a URL. "
            f"`{url}` was parsed as a FILENAME to match, no such file exists "
            f"here, so it matched nothing. That is why the output is empty, and "
            f"no change to the ref, the path or the flags will change it.)")
