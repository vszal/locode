#!/usr/bin/env bash
#
# locode installer — installs locode into an isolated environment and records
# *how*, so `locode upgrade` can update it the same way later. See architecture.md
# §10 (Install & upgrade).
#
#   End users:   curl -fsSL <raw-url>/install.sh | bash
#   Power users: pipx install locode
#   Developers:  ./install.sh --dev                 (editable install from a checkout)
#
# Flags:
#   --dev            install editable from source (cwd checkout, or clone LOCODE_REPO)
#   --ref <rev>      with --dev, the branch/tag/commit to clone (default: default branch)
#   --pre            allow pre-release versions (PyPI methods)
#   --dry-run        print what would happen; change nothing
#   -h, --help       this help
#
# The install method is recorded at $XDG_DATA_HOME/locode/install-method (default
# ~/.local/share/locode/install-method) in the format written by locode/install.py:
# a single line "<method>\t<detail>".

set -euo pipefail

DEV=0
REF=""
PRE=0
DRY=0
LOCODE_REPO="${LOCODE_REPO:-https://github.com/vszal/locode.git}"

log()  { printf '%s\n' "$*" >&2; }
run()  { if [ "$DRY" = 1 ]; then log "  + $*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dev)      DEV=1 ;;
    --ref)      REF="${2:-}"; shift ;;
    --ref=*)    REF="${1#--ref=}" ;;
    --pre)      PRE=1 ;;
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

pre_pip_args() { [ "$PRE" = 1 ] && printf -- '--pip-args=--pre' || true; }

# --- developer (editable) install --------------------------------------------
if [ "$DEV" = 1 ]; then
  if [ -f pyproject.toml ] && grep -q 'name = "locode"' pyproject.toml; then
    SRC="$(pwd)"
    log "Developer install (editable) from current checkout: $SRC"
  else
    SRC="${TMPDIR:-/tmp}/locode-src"
    log "Developer install (editable): cloning $LOCODE_REPO -> $SRC"
    run rm -rf "$SRC"
    if [ -n "$REF" ]; then run git clone --branch "$REF" "$LOCODE_REPO" "$SRC"
    else run git clone "$LOCODE_REPO" "$SRC"; fi
  fi
  if have pipx; then
    run pipx install --editable "$SRC"
  elif have uv; then
    run uv tool install --editable "$SRC"
  else
    run "$PY" -m pip install --user -e "$SRC"
  fi
  write_marker git "$SRC"

# --- end-user (PyPI) install -------------------------------------------------
else
  if have pipx; then
    log "Installing locode with pipx..."
    if [ "$PRE" = 1 ]; then run pipx install --pip-args=--pre locode
    else run pipx install locode; fi
    write_marker pipx ""
  elif have uv; then
    log "Installing locode with uv..."
    if [ "$PRE" = 1 ]; then run uv tool install --prerelease=allow locode
    else run uv tool install locode; fi
    write_marker uv ""
  else
    VENV="$DATA_DIR/venv"
    log "Installing locode into a dedicated venv at ${VENV}"
    run "$PY" -m venv "$VENV"
    if [ "$PRE" = 1 ]; then run "$VENV/bin/pip" install -U --pre locode
    else run "$VENV/bin/pip" install -U locode; fi
    run mkdir -p "$BIN_DIR"
    run ln -sf "$VENV/bin/locode" "$BIN_DIR/locode"
    write_marker venv ""
  fi
fi

# --- PATH advisory + next steps ----------------------------------------------
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) log "note: $BIN_DIR is not on your PATH — add it to run 'locode'." ;;
esac

log ""
log "locode installed. Run 'locode' to start (first run writes a starter config"
log "at ~/.config/locode/config.toml). Update later with 'locode upgrade'."
