#!/bin/zsh
cd /Users/vszalvay/Code/locode
# Wait for the calibration sweep to release the GPU.
while pgrep -f "harness.py run" >/dev/null; do sleep 60; done
echo "CALIB RELEASED $(date)" >> evals/results/rule86-retest.log
.venv/bin/python evals/harness.py run \
  --case multi-defect-suite --case multi-defect-blind \
  --model qwen38 --repeat 6 --label b160-rule86-retest \
  >> evals/results/rule86-retest.log 2>&1
echo "RETEST-DONE $(date)" >> evals/results/rule86-retest.log
