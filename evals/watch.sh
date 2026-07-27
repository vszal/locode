#!/bin/zsh
# Run a locode task headless and SEE it as the user sees it on screen: the
# model's prose interleaved with tool calls, results, and nudges (via
# --show-events), followed by a pathology VERDICT (repeats / no-ops / fails).
# One command = run + full readout.
#
#   evals/watch.sh "<task prompt>" [model] [workdir]
#
# Defaults: model=gemmacoder12, workdir=a fresh temp dir. Point workdir at a repo
# to run against real files. Extra locode flags via LOCODE_FLAGS.
set -e
HERE="${0:A:h}"
ROOT="${HERE:h}"
TASK="${1:?usage: watch.sh \"<task>\" [model] [workdir]}"
MODEL="${2:-gemmacoder12}"
WORK="${3:-$(mktemp -d)}"
LOG="$(mktemp -t locode-watch-XXXX).jsonl"
FLAGS="${LOCODE_FLAGS:---allow-tool edit_file,write_file,replace_lines,read_file,bash --max-iterations 18 --max-wallclock 240}"

echo "▶ task:   $TASK"
echo "▶ model:  $MODEL"
echo "▶ workdir:$WORK"
echo "──────────────────────────── live transcript (prose + tools) ────"

# --show-events prints the on-screen transcript (prose + tool/result/nudge lines)
# to stdout; --log-events records the structured stream the verdict is built from.
( cd "$WORK" && "$ROOT/.venv/bin/locode" -p "$TASK" -m "$MODEL" \
    --no-splash --no-markdown --show-events $=FLAGS --log-events "$LOG" 2>&1 ) || true

echo "\n──────────────────────────── verdict ────"
"$ROOT/.venv/bin/python" "$HERE/replay.py" "$LOG" --quiet
echo "──────────────────────────────────────────"
echo "workdir kept at: $WORK   full log: $LOG"
