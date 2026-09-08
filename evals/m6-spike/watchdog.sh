#!/bin/bash
# M6.0 spike watchdog. Samples system wired pages; hard-kills every mlx_lm.server
# if GPU-wired (total wired minus the recorded baseline) breaches ABORT_GB.
# The failure mode this guards is a kernel panic, which no in-process handler
# can catch -- so this runs outside the servers and kills without asking.
BASE_GB=${BASE_GB:?}; ABORT_GB=${ABORT_GB:?}; LOG=${LOG:?}
PS=$(vm_stat | awk '/page size/{print $8}')
while :; do
  W=$(vm_stat | awk -v ps="$PS" '/Pages wired down/{gsub(/\./,"",$4); print $4*ps/1073741824}')
  C=$(vm_stat | awk -v ps="$PS" '/occupied by compressor/{gsub(/\./,"",$5); print $5*ps/1073741824}')
  G=$(echo "$W $BASE_GB" | awk '{printf "%.2f", $1-$2}')
  echo "$(date +%H:%M:%S) wired=$W gpu_est=$G compressor=$C" >> "$LOG"
  if awk -v g="$G" -v a="$ABORT_GB" 'BEGIN{exit !(g>a)}'; then
    echo "$(date +%H:%M:%S) *** ABORT gpu_est=$G > $ABORT_GB -- killing servers ***" >> "$LOG"
    pkill -9 -f mlx_lm.server; exit 9
  fi
  sleep 2
done
