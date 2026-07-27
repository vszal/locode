#!/bin/zsh
# Run a locode task headless, then replay it as the user would see it on screen
# (tool calls, results, nudges) with pathology flags + a verdict. This is the
# "let me observe a real session" tool: one command = run + full readout.
#
#   evals/watch.sh "<task prompt>" [model] [workdir]
#
# Defaults: model=gemmacoder12, workdir=a fresh temp dir. Point workdir at a repo
# to run the task against real files. Extra locode flags via LOCODE_FLAGS.
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
echo "▶ log:    $LOG"
echo "────────────────────────────────────────"

( cd "$WORK" && "$ROOT/.venv/bin/locode" -p "$TASK" -m "$MODEL" \
    --no-splash --no-markdown $=FLAGS --log-events "$LOG" >/dev/null 2>&1 ) || true

"$ROOT/.venv/bin/python" "$HERE/replay.py" "$LOG"
echo "────────────────────────────────────────"
echo "workdir kept at: $WORK"
