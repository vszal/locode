#!/bin/zsh
# n runs of aider on locode's exec-bugfix case, same model/server locode uses.
SEED=/Users/vszalvay/Code/locode/evals/cases/exec-bugfix/seed
BASE=/private/tmp/claude-502/-Users-vszalvay-Code-locode/096fe96c-b030-456e-b55c-6a12dcae631c/scratchpad/aider-cmp/runs-diff
MODEL=openai/sahilchachra/Qwythos-9B-Claude-Mythos-5-1M-mxfp8-mlx
PROMPT="The tests in this directory are failing. Fix the bugs in textkit.py so that every test passes. Do not change the tests — they describe the intended behavior correctly."
mkdir -p $BASE
for i in $(seq 1 ${1:-6}); do
  W=$BASE/r$i; rm -rf $W; mkdir -p $W; cp $SEED/*.py $W/
  cd $W
  S=$(date +%s)
  OPENAI_API_BASE=http://127.0.0.1:8081/v1 OPENAI_API_KEY=dummy \
  ~/.aider-venv/bin/aider --model $MODEL \
    --no-git --yes-always --no-check-update --no-analytics --no-stream --edit-format diff \
    --test-cmd "python3 -m pytest -q" --auto-test \
    --message "$PROMPT" textkit.py test_textkit.py > aider.log 2>&1
  E=$(date +%s)
  # grade with the same criteria as locode's check.py
  PYOUT=$(python3 -m pytest -q 2>&1 | tail -3)
  DIFF=$(diff -q $SEED/test_textkit.py $W/test_textkit.py >/dev/null 2>&1 && echo same || echo MODIFIED)
  echo "r$i secs=$((E-S)) tests=[$(echo $PYOUT | tr '\n' ' ')] tests_file=$DIFF"
done
