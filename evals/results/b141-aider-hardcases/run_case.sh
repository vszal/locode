#!/bin/zsh
# usage: run_case.sh <case-id> <n> <edit-format> [--no-autotest]
set -u
CASE=$1; N=${2:-6}; FMT=${3:-diff}; AUTOTEST=${4:-yes}
REPO=/Users/vszalvay/Code/locode
SEED=$REPO/evals/cases/$CASE/seed
BASE=/private/tmp/claude-502/-Users-vszalvay-Code-locode/096fe96c-b030-456e-b55c-6a12dcae631c/scratchpad/aider-cmp/case-$CASE-$FMT
MODEL=openai/sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx
PROMPT=$(cat $REPO/evals/cases/$CASE/prompt.md)
mkdir -p $BASE
for i in $(seq 1 $N); do
  W=$BASE/r$i; rm -rf $W; mkdir -p $W; cp -R $SEED/. $W/
  cd $W
  FILES=(${(f)"$(ls *.py 2>/dev/null)"})
  TESTARGS=()
  if [ "$AUTOTEST" = "yes" ]; then TESTARGS=(--test-cmd "python3 -m pytest -q" --auto-test); fi
  S=$(date +%s)
  OPENAI_API_BASE=http://127.0.0.1:8081/v1 OPENAI_API_KEY=dummy \
  ~/.aider-venv/bin/aider --model $MODEL --edit-format $FMT \
    --no-git --yes-always --no-check-update --no-analytics --no-stream \
    $TESTARGS --message "$PROMPT" $FILES > aider.log 2>&1
  E=$(date +%s)
  G=$($REPO/.venv/bin/python $REPO/evals/grade_external.py --case $CASE --workdir $W --json 2>&1)
  echo "$CASE r$i secs=$((E-S)) $G"
done
