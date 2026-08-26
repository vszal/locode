#!/usr/bin/env bash
# Build the workspace for the git-url-empty case.
#
# Two things here cannot live in seed/ and are the reason this case needs a
# setup hook at all:
#
#   1. The workspace must BE a git repo. That is not decoration -- it is the
#      whole trap. `git ls-tree <ref> <url>` inside a repo exits 0 and prints
#      nothing (the URL is parsed as a pathspec, and matches nothing); OUTSIDE
#      a repo the same command dies with `fatal: not a git repository`, which
#      is a loud, actionable error and no trap at all. A nested `.git` cannot
#      be committed into seed/, so it is built here.
#
#   2. The upstream URL is a file:// path into a scratch dir whose name is
#      only known at run time. Everything is local: no network, deterministic,
#      and `git clone` works offline against it.
set -euo pipefail

git_c() { git -c user.email=eval@locode -c user.name=eval -c commit.gpgsign=false "$@"; }

WS="$PWD"

# ---- upstream: the bare repo the vendored copy is behind -------------------
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
# The answer. Distinctive enough that a grader can look for it in plain text
# without matching prose the model could have written from the prompt alone.
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
(cd "$build" && git init -q -b main . && git add -A && git_c commit -qm "handlers: parse, route, backoff")

# The remote lives in its OWN temp dir, not in the workspace. Two reasons: a
# bare repo sitting in the workspace can be answered with `--git-dir` and never
# exercises a URL at all, and a fixed path beside the workspace would collide
# between concurrent runs of the sweep. The model is given the URL and nothing
# else, which is the live shape.
REMOTE_PARENT="$(mktemp -d)"
git clone -q --bare "$build" "$REMOTE_PARENT/upstream.git"
rm -rf "$build"
UPSTREAM="file://$REMOTE_PARENT/upstream.git"

# ---- the workspace: a service with a STALE vendored copy -------------------
mkdir -p vendor/handlers
cat > vendor/handlers/parse.py <<'EOF'
"""Parse an inbound payload into a Request."""


def parse(raw):
    return {"path": raw.get("path", "/"), "body": raw.get("body")}
EOF
cat > vendor/handlers/route.py <<'EOF'
"""Dispatch a parsed Request to a handler."""

ROUTES = {}


def route(req):
    return ROUTES.get(req["path"])
EOF
cat > vendor_sync.py <<EOF
"""Refresh the vendored copy of the upstream handler modules.

Run this after upstream cuts a release. It does NOT resolve which files are
new -- that is still done by hand before the copy step.
"""

UPSTREAM = "$UPSTREAM"
VENDOR_DIR = "vendor/handlers"
UPSTREAM_DIR = "handlers"


def sync():
    raise NotImplementedError("copy step is still manual")
EOF
cat > README.md <<'EOF'
# payload-service

The modules under `vendor/handlers/` are copied from the `handlers/`
directory of the upstream repo; see `vendor_sync.py` for where that is. Do
not edit them here -- changes belong upstream.
EOF

git init -q -b main .
git add -A
git_c commit -qm "vendor: handlers @ parse, route"
