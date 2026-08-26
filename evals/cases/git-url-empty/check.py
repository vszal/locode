"""Checks for the git-url-empty case.

The case exists because of a live failure. Asked what a remote repo contained,
qythos9 ran `git ls-tree -r --name-only main <URL> <path>`, got exit 0 and no
output, ran it again without the pathspec, got exit 0 and no output, and the
turn ended with "every tool call kept coming back empty". Every piece of the
harness behaved correctly. The command was the thing that was wrong: `ls-tree`
reads the LOCAL object database and cannot take a URL at all, so the URL was
parsed as a pathspec and matched nothing.

That failure has a property nothing else in this suite has: the wrong command
is INDISTINGUISHABLE FROM THE RIGHT ONE returning a true empty answer. Not an
error, not a stack trace -- exit 0 and silence. It is the worst possible signal,
and the model cannot read its way out of it, which is the point (three earlier
cases failed as levers because qythos9 reads code well enough to find the defect
without running anything; here there is nothing to read).

WHY THE WORKSPACE IS BUILT BY setup.sh
--------------------------------------
Two preconditions cannot be checked into `seed/`:

  * The workspace must BE a git repo. Outside one, the same command dies with
    `fatal: not a git repository` -- loud, actionable, no trap. A nested `.git`
    cannot be committed.
  * The upstream URL is a `file://` path into a scratch dir named at run time.

Verified 2026-08-26 in the built workspace: with the vendored copy at
`vendor/handlers/`, both `git ls-tree -r --name-only main <URL> handlers/` and
the same command with no pathspec exit 0 with zero output, while `git clone`
and `git ls-remote` against the same URL work offline. The vendored copy is at
`vendor/handlers/` and NOT `handlers/` on purpose: an earlier draft put it at
`handlers/`, and the pathspec then matched the LOCAL tree, so the trap printed
the workspace's own two files instead of nothing. That is a different failure
(a confident wrong answer) and it is not the one under study.

WHAT IS GRADED
--------------
The deliverable is prose, not an edit, so the answer is read out of the model's
final turn text -- NOT out of `ctx.stdout`, which also contains tool output. If
the model clones the repo and cats the file, "backoff" appears in stdout whether
or not it ever answered; grading that would score the transcript, not the model.
"""

import re

# The answer: upstream ships handlers/backoff.py, the workspace vendored only
# parse.py and route.py.
ANSWER_FILE = re.compile(r"\bbackoff(\.py)?\b", re.I)

# What it does, in words that are NOT the filename. The model has to have read
# the file (or its docstring) to write any of these; none can be guessed from
# the prompt, which says only "a handler".
PURPOSE = re.compile(r"\bretr(y|ies|ying|ied)\b|\bexponential\b|\btransient\b"
                     r"|\bmax_attempts\b|\bbase_delay\b", re.I)

# The two ways a run can name the wrong file, i.e. answer from the vendored copy
# it can already see instead of from upstream.
DECOY = re.compile(r"\b(parse|route)(\.py)?\b", re.I)

# git subcommands that read the LOCAL object database and cannot take a URL.
# Deliberately short: every one of these was verified to exit 0 (or to fail
# without naming the URL as the cause) when handed one. `log` is NOT here -- it
# dies with `fatal: invalid object name 'file'`, which is a real error message
# and a different situation.
LOCAL_ONLY = ("ls-tree", "cat-file", "show", "grep", "rev-list", "diff-tree")

URLISH = re.compile(r"(?:https?|git|ssh|file)://\S+|\S+@\S+:\S+\.git\b")


def check(ctx):
    final = _final_text(ctx)
    named = bool(ANSWER_FILE.search(final))
    results = {
        # Did the turn end with the model's own words, rather than a stop?
        "runs_clean": bool(final) and not final.startswith("⏹"),
        # THE OUTCOME. Names the file upstream added.
        "named_the_file": named,
        # ...and says what it does, from having read it.
        "described_it": bool(PURPOSE.search(final)),
        # Answering "parse.py" or "route.py" is answering from the stale local
        # copy -- the specific wrong answer this workspace invites. Scored apart
        # so a run that names the right file AND hedges with the decoys is not
        # silently credited.
        "no_decoy_answer": named and not DECOY.search(final),
        # The behavioural finding, scored whether or not the answer came out:
        # did the turn die in the empty-results stall?
        "no_empty_stall": not _stalled_on_empty(ctx),
        # Exposure (rule 17): did the model ever hand a URL to a local-only git
        # subcommand at all? If this is True on every run in both arms, the
        # lever never fired and the case graded nothing.
        "avoided_url_trap": not _hit_url_trap(ctx),
    }
    results["fully_fixed"] = results["named_the_file"] and results["described_it"]
    return results


def _final_text(ctx):
    """The model's last turn result. `turn_end` carries what the turn returned:
    the final assistant reply, or a stop marker beginning with the stop glyph."""
    out = ""
    for ev in getattr(ctx, "events", None) or []:
        if ev.get("phase") == "turn_end" and ev.get("result"):
            out = str(ev["result"])
    return out.strip()


def _bash_cmds(ctx):
    for ev in getattr(ctx, "events", None) or []:
        if ev.get("phase") == "run" and ev.get("name") == "bash":
            cmd = (ev.get("args") or {}).get("cmd")
            if cmd:
                yield str(cmd)


def _hit_url_trap(ctx):
    for cmd in _bash_cmds(ctx):
        if not URLISH.search(cmd):
            continue
        # Segment on shell separators so `git clone <url> && git ls-tree HEAD`
        # is not read as one command that did both.
        for part in re.split(r"[;&|]+|\n", cmd):
            if "git" in part and URLISH.search(part) and any(
                    re.search(rf"\b{re.escape(sub)}\b", part) for sub in LOCAL_ONLY):
                return True
    return False


def _stalled_on_empty(ctx):
    for ev in getattr(ctx, "events", None) or []:
        if ev.get("phase") == "stopped" and "coming back empty" in str(ev.get("reason", "")):
            return True
    return False
