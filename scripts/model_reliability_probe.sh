#!/usr/bin/env bash
#
# model_reliability_probe.sh — does a local model drive locode's agent loop
# *reliably*? Runs a canonical bug-fix task N times against a model and reports
# the pass rate, per-run latency, and whether any chain-of-thought ("reasoning")
# leaked into the output. This is a LIVE probe (needs the model server) — it's
# how we caught qythos9's reasoning-runaway "hangs"; re-run it after any change
# to a model's capability profile.
#
# Usage:   scripts/model_reliability_probe.sh <model-alias> [runs]
# Example: scripts/model_reliability_probe.sh qythos9 5
#
# Exit 0 if every run passed (and nothing hung); non-zero otherwise.

set -euo pipefail

MODEL="${1:?usage: model_reliability_probe.sh <model-alias> [runs]}"
RUNS="${2:-3}"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOCODE="$REPO/.venv/bin/locode"
PY="$REPO/.venv/bin/python"
DIR="$REPO/sandbox/.reliability-probe"
LOG="$(mktemp)"
trap 'rm -rf "$DIR" "$LOG"' EXIT

mkdir -p "$DIR"
cat > "$DIR/test_strutil.py" <<'PYEOF'
from strutil import word_count, reverse_words


def test_word_count():
    assert word_count("the quick brown fox") == 4
    assert word_count("") == 0


def test_reverse_words():
    assert reverse_words("one two three") == "three two one"
PYEOF

reset_fixture() {
  cat > "$DIR/strutil.py" <<'PYEOF'
def word_count(s):
    return len(s)          # BUG: counts characters, not words


def reverse_words(s):
    return s[::-1]         # BUG: reverses characters, not word order
PYEOF
}

REL="sandbox/.reliability-probe"
PROMPT="Fix the two bugs in $REL/strutil.py so the tests pass. word_count must \
count words (split on whitespace); reverse_words must reverse the ORDER of the \
words, not the characters. Then run: $PY -m pytest $REL/test_strutil.py -q"

passes=0
slow=0
echo "Reliability probe: model=$MODEL runs=$RUNS"
echo "----------------------------------------------"
for i in $(seq 1 "$RUNS"); do
  reset_fixture
  t0=$(date +%s)
  "$LOCODE" -p "$PROMPT" -m "$MODEL" \
    --allow-tool edit_file,write_file,bash --no-splash --no-markdown > "$LOG" 2>&1 || true
  dt=$(( $(date +%s) - t0 ))
  if "$PY" -m pytest "$DIR/test_strutil.py" -q >/dev/null 2>&1; then
    verdict="PASS"; passes=$((passes + 1))
  else
    verdict="FAIL"
  fi
  leak=""
  grep -qiE "reasoning|<think>|analyze the prompt" "$LOG" && leak=" [reasoning-leak]"
  [ "$dt" -ge 90 ] && { slow=$((slow + 1)); leak="$leak [SLOW ${dt}s — possible hang]"; }
  printf "  run %2d: %-4s %3ds%s\n" "$i" "$verdict" "$dt" "$leak"
done

echo "----------------------------------------------"
echo "passed $passes/$RUNS, slow/hung $slow/$RUNS"
[ "$passes" -eq "$RUNS" ] && [ "$slow" -eq 0 ] && { echo "RELIABLE ✓"; exit 0; }
echo "UNRELIABLE ✗"; exit 1
