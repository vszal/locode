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
Three preconditions cannot be checked into `seed/`, and each cost a draft:

  * The workspace must BE a git repo. Outside one, the same command dies with
    `fatal: not a git repository` -- loud, actionable, no trap. A nested `.git`
    cannot be committed.
  * Upstream must be reachable ONLY as a URL. Draft 2 used a `file://` URL and
    measured nothing: the URL spells out a filesystem path, so qythos9 ran `ls`
    on it, found `upstream.git` sitting there, `cd`'d in and queried it as a
    LOCAL repo -- correct answer, zero URLs, trap never armed. Upstream is now
    served by a `git daemon` on the loopback (port 9419): `git://` carries no
    path to escape into, and it is still entirely offline.
  * There is nothing under `handlers/` in the workspace. Draft 1 vendored a copy
    at `handlers/`, and the pathspec then matched the LOCAL tree, so the trap
    printed the workspace's own files instead of nothing -- a confident wrong
    answer, a different failure. Draft 2 moved it to `vendor/handlers/` and
    asked which file upstream had ADDED; qythos9 answered off the stale copy
    without touching the URL. So the workspace vendors NOTHING, and every
    filename in the expected answer exists only at the far end of the URL.

WHAT THIS CASE MEASURED (2026-08-26) -- READ BEFORE REUSING IT
--------------------------------------------------------------
Against build 136, the build that PREDATES the fix it was built to detect, the
trap fired **0 times in 12 runs** (smoke-giturl-pre3 n=4, smoke-giturl-pre4
n=8). Not once did qythos9 hand a URL to a local-only git command. It does one
of two things instead:

  * clones (`git clone git://... upstream`) and answers perfectly -- 4/12; or
  * takes the BASENAME out of the URL and runs
    `git ls-tree -r upstream.git --name-only | grep '^handlers/'`, which fails
    with `fatal: Not a valid object name upstream.git` (rc 128, LOUD and
    exactly correct), then runs the identical command again and gets
    repeat-stopped -- 6/12, the dominant failure.

So by rule 71 this case does NOT discriminate for the git-URL lever, and by
rule 17 that lever has not been tested. It is kept because the second bullet is
a real and reproducible finding in its own right, and a sharper one: the model
is not ignoring an ambiguous silence, it is ignoring a specific, correct error
message that names the exact problem. Note the sweep-to-sweep variance too --
3/4 clean on n=4 against 1/8 on n=8, same code, same case (rule 57: size a
threshold against that before writing one).

WHAT IS GRADED
WHAT IS GRADED
--------------
The deliverable is prose, not an edit, so the answer is read out of the model's
final turn text -- NOT out of `ctx.stdout`, which also contains tool output. If
the model clones the repo and cats the file, "backoff" appears in stdout whether
or not it ever answered; grading that would score the transcript, not the model.
"""

import re

# The answer: upstream ships handlers/{parse,route,backoff}.py. NOTHING in the
# workspace names any of them, so every one of these is proof that the model
# reached the remote -- there is nothing local to read them off.
ANSWER_FILES = {
    "parse": re.compile(r"\bparse(\.py)?\b", re.I),
    "route": re.compile(r"\broute(\.py)?\b", re.I),
    "backoff": re.compile(r"\bbackoff(\.py)?\b", re.I),
}

# What it does, in words that are NOT the filename. The model has to have read
# the file (or its docstring) to write any of these; none can be guessed from
# the prompt, which says only "a handler".
PURPOSE = re.compile(r"\bretr(y|ies|ying|ied)\b|\bexponential\b|\btransient\b"
                     r"|\bmax_attempts\b|\bbase_delay\b", re.I)

# git subcommands that read the LOCAL object database and cannot take a URL.
# Deliberately short: every one of these was verified to exit 0 (or to fail
# without naming the URL as the cause) when handed one. `log` is NOT here -- it
# dies with `fatal: invalid object name 'file'`, which is a real error message
# and a different situation.
LOCAL_ONLY = ("ls-tree", "cat-file", "show", "grep", "rev-list", "diff-tree")

URLISH = re.compile(r"(?:https?|git|ssh|file)://\S+|\S+@\S+:\S+\.git\b")


def check(ctx):
    final = _final_text(ctx)
    hits = [k for k, rx in ANSWER_FILES.items() if rx.search(final)]
    results = {
        # Did the turn end with the model's own words, rather than a stop?
        "runs_clean": bool(final) and not final.startswith("⏹"),
        # THE OUTCOME. All three files upstream actually ships.
        "named_all_three": len(hits) == 3,
        # Broken out so a partial answer is legible in the results table: a run
        # that reached the remote at all usually gets every name at once, so
        # this moving on its own is worth seeing.
        "named_any_file": bool(hits),
        # ...and says what backoff.py is FOR, in words that are not its name.
        # Only readable from the file's own docstring.
        "described_backoff": bool(PURPOSE.search(final)),
        # The behavioural finding, scored whether or not the answer came out:
        # did the turn die in the empty-results stall?
        "no_empty_stall": not _stalled_on_empty(ctx),
        # Exposure (rule 17): did the model ever hand a URL to a local-only git
        # subcommand at all? If this is True on every run in both arms, the
        # lever never fired and the case graded nothing.
        "avoided_url_trap": not _hit_url_trap(ctx),
    }
    results["fully_fixed"] = (results["named_all_three"]
                              and results["described_backoff"])
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
