#!/usr/bin/env bash
#
# locode installer — installs locode into an isolated environment and records
# *how*, so `locode upgrade` can update it the same way later. See architecture.md
# §10 (Install & upgrade).
#
#   End users:   curl -fsSL <raw-url>/install.sh | bash   (installs from the git repo)
#   Developers:  ./install.sh --dev                        (editable install from a checkout)
#
# NOTE: PyPI publishing is deferred (the `locode` name on PyPI belongs to an
# unrelated project), so BOTH paths install from the git repo. The end-user
# default clones it to ~/.local/share/locode/src and installs from there; once
# locode is published to PyPI this default flips back to a PyPI install.
#
# Flags:
#   --dev            editable install from the current checkout (for contributors)
#   --ref <rev>      branch/tag/commit to clone for the end-user install (default: default branch)
#   --dry-run        print what would happen; change nothing
#   -h, --help       this help
#
# The install method is recorded at $XDG_DATA_HOME/locode/install-method (default
# ~/.local/share/locode/install-method) in the format written by locode/install.py:
# a single line "<method>\t<detail>".

set -euo pipefail

DEV=0
REF=""
DRY=0
LOCODE_REPO="${LOCODE_REPO:-https://github.com/vszal/locode.git}"

log()  { printf '%s\n' "$*" >&2; }
run()  { if [ "$DRY" = 1 ]; then log "  + $*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dev)      DEV=1 ;;
    --ref)      REF="${2:-}"; shift ;;
    --ref=*)    REF="${1#--ref=}" ;;
    --dry-run)  DRY=1 ;;
    -h|--help)  usage; exit 0 ;;
    *) log "install.sh: unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/locode"
MARKER="$DATA_DIR/install-method"
BIN_DIR="$HOME/.local/bin"

# --- prerequisites -----------------------------------------------------------
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { log "install.sh: python3 not found (locode needs Python >=3.10)."; exit 1; }
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  log "install.sh: Python >=3.10 required (found $("$PY" -V 2>&1))."; exit 1
fi

if [ "$(uname -s)" = "Darwin" ]; then
  if ! have mlx_lm.server; then
    log "note: mlx_lm not found. locode serves local models with mlx_lm on Apple"
    log "      Silicon. Install it with:  $PY -m pip install 'mlx-lm>=0.31'"
  fi
else
  log "note: on Linux, point locode at any OpenAI-compatible server on :8081"
  log "      (configure [server] in ~/.config/locode/config.toml)."
fi

write_marker() {  # method detail
  if [ "$DRY" = 1 ]; then log "  + record install method: $1${2:+ ($2)} -> $MARKER"; return; fi
  mkdir -p "$DATA_DIR"
  printf '%s\t%s\n' "$1" "${2:-}" > "$MARKER"
}

# --- resolve install source --------------------------------------------------
# PyPI publishing is deferred, so BOTH paths install from the git checkout and
# record the "git" install method (-> `locode upgrade` does git pull + reinstall).
# --dev installs editable from the current checkout (contributors); the default
# clones the repo to a persistent dir under the data dir so upgrades can pull it.
if [ "$DEV" = 1 ] && [ -f pyproject.toml ] && grep -q 'name = "locode"' pyproject.toml; then
  SRC="$(pwd)"
  log "Developer install (editable) from current checkout: $SRC"
else
  SRC="$DATA_DIR/src"
  if [ -d "$SRC/.git" ]; then
    log "Updating existing locode checkout at $SRC"
    run git -C "$SRC" pull --ff-only
  else
    log "Installing locode from $LOCODE_REPO -> $SRC"
    run mkdir -p "$DATA_DIR"
    if [ -n "$REF" ]; then run git clone --branch "$REF" "$LOCODE_REPO" "$SRC"
    else run git clone "$LOCODE_REPO" "$SRC"; fi
  fi
fi

# --- install (editable) from the resolved source -----------------------------
if have pipx; then
  run pipx install --force --editable "$SRC"
elif have uv; then
  run uv tool install --force --editable "$SRC"
else
  run "$PY" -m pip install --user -e "$SRC"
fi
write_marker git "$SRC"

# --- PATH advisory + next steps ----------------------------------------------
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) log "note: $BIN_DIR is not on your PATH — add it to run 'locode'." ;;
esac

log ""
log "locode installed. Run 'locode' to start (first run writes a starter config"
log "at ~/.config/locode/config.toml). Update later with 'locode upgrade'."
