#!/usr/bin/env bash
# Build the workspace for the git-url-empty case.
#
# Three things here cannot live in seed/, and each of them is load-bearing:
#
#   1. The workspace must BE a git repo. That is not decoration -- it is the
#      whole trap. `git ls-tree <ref> <URL>` inside a repo exits 0 and prints
#      nothing (the URL is parsed as a pathspec and matches nothing); OUTSIDE a
#      repo the same command dies with `fatal: not a git repository`, which is
#      loud, actionable, and no trap at all. A nested `.git` cannot be committed
#      into seed/, so it is built here.
#
#   2. The upstream must be reachable ONLY as a URL. An earlier draft used a
#      `file://` URL and the case measured nothing: the URL spells out a
#      filesystem path, so qwythos9 ran `ls` on it, found `upstream.git` sitting
#      there, `cd`'d in and queried it as a local repo. Correct answer, zero
#      URLs, trap never armed (smoke-giturl-pre2 run 2, 2026-08-26). So
#      upstream is served by a `git daemon` on the loopback: `git://` carries
#      no path to escape into, and it is still entirely offline -- no internet,
#      no nondeterminism, no external dependency.
#
#   3. The daemon is shared and started on demand. It serves a read-only bare
#      repo, so one is enough for a whole sweep, and starting it here keeps the
#      case self-contained rather than depending on a step someone remembered
#      to run first.
set -euo pipefail

PORT=9419                       # not 9418: don't collide with a real git daemon
BASE=/tmp/locode-eval-gitd
URL="git://127.0.0.1:$PORT/upstream.git"

git_c() { git -c user.email=eval@locode -c user.name=eval -c commit.gpgsign=false "$@"; }

# ---- upstream: the bare repo the daemon serves -----------------------------
if [ ! -d "$BASE/upstream.git" ]; then
  mkdir -p "$BASE"
  build="$(mktemp -d)"
  mkdir -p "$build/handlers"
  cat > "$build/handlers/parse.py" <<'EOF'
"""Parse an inbound payload into a Request."""


def parse(raw):
    return {"path": raw.get("path", "/"), "body": raw.get("body")}
EOF
  cat > "$build/handlers/route.py" <<'EOF'
"""Dispatch a parsed Request to a handler."""

ROUTES = {}


def route(req):
    return ROUTES.get(req["path"])
EOF
  # The part of the answer that cannot be guessed from a filename.
  cat > "$build/handlers/backoff.py" <<'EOF'
"""Retry a failed handler call with exponential backoff.

Used by route() when a downstream handler raises TransientError. Sleeps
BASE_DELAY * 2**attempt between tries, giving up after MAX_ATTEMPTS.
"""

MAX_ATTEMPTS = 5
BASE_DELAY = 0.25


class TransientError(Exception):
    pass


def with_backoff(fn, *args):
    import time
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fn(*args)
        except TransientError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(BASE_DELAY * 2 ** attempt)
EOF
  cat > "$build/README.md" <<'EOF'
# payload-handlers

Upstream home of the handler modules vendored by downstream services.
EOF
  (cd "$build" && git init -q -b main . && git add -A \
     && git_c commit -qm "handlers: parse, route, backoff")
  git clone -q --bare "$build" "$BASE/upstream.git"
  rm -rf "$build"
  touch "$BASE/upstream.git/git-daemon-export-ok"
fi

# ---- the daemon ------------------------------------------------------------
if ! git ls-remote "$URL" >/dev/null 2>&1; then
  git daemon --base-path="$BASE" --export-all --reuseaddr \
             --port="$PORT" --detach "$BASE" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    git ls-remote "$URL" >/dev/null 2>&1 && break
    sleep 0.3
  done
fi
# A workspace pointing at a dead daemon is not a hard case, it is no case at
# all. Fail here so the harness marks the run invalid instead of scoring the
# model 0.0 for the rig's mistake.
git ls-remote "$URL" >/dev/null 2>&1 || {
  echo "git daemon not reachable at $URL" >&2; exit 1; }

# ---- the workspace: NOTHING vendored yet, so nothing local can answer -------
# The first draft shipped a stale vendored copy and asked which file upstream
# had ADDED. qwythos9 answered without ever touching the URL: ls, ls, read_file,
# `git log --oneline --all` against the local repo, then "upstream added
# parse.py and route.py" -- read straight off the only thing in front of it,
# confidently wrong (smoke-giturl-pre, 2026-08-26). A question a careful reader
# can answer locally is not a test of fetching. So: nothing is vendored, and
# every filename in the expected answer exists only at the far end of the URL.
mkdir -p vendor
: > vendor/.gitkeep
cat > vendor_sync.py <<EOF
"""Copy the upstream handler modules into vendor/.

Nothing has been vendored yet -- this is the first sync.
"""

UPSTREAM = "$URL"
VENDOR_DIR = "vendor/handlers"
UPSTREAM_DIR = "handlers"


def sync():
    raise NotImplementedError("copy step is still manual")
EOF
cat > README.md <<'EOF'
# payload-service

The handler modules live upstream and get copied into `vendor/` before a
release; see `vendor_sync.py` for where upstream is. Nothing is vendored yet.
EOF

git init -q -b main .
git add -A
git_c commit -qm "vendor_sync: point at upstream, nothing copied yet"
