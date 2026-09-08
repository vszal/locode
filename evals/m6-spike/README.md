# M6.0 — Metal co-residency spike

Answers one question: can two `mlx_lm.server` processes hold wired buffers on
one Apple GPU? **Yes** — see ROADMAP §5.147 and rule 93 for the result and its
31% throughput cost. This directory is the raw evidence.

The failure mode is a **kernel panic, not an exception**, so the abort path
lives outside the servers: `watchdog.sh` samples `vm_stat` every 2s and
`pkill -9`s every `mlx_lm.server` if GPU-wired memory (total wired minus a
recorded baseline) breaches `ABORT_GB`. Start it *before* the second server.

## Reproducing

    BASE_GB=$(vm_stat | awk '/page size/{ps=$8} /Pages wired down/{gsub(/\./,"",$4); printf "%.2f", $4*ps/1073741824}')
    BASE_GB=$BASE_GB ABORT_GB=16.5 LOG=$PWD/watchdog.log ./watchdog.sh &

Launch each server with the argv `locode` would use — get it from
`build_launch_argv(..., cache_bytes=mgr._live_cache_bytes(mid, profile))` — on
distinct ports, then alternate load across them:

    python load.py '[{"name":"a","port":8082,"pid":<pid>,"tokens":30000}, ...]' <minutes> out.jsonl

`load.py` records per-request wallclock, prompt tokens, system wired GB, and
**per-process physical footprint via `vmmap -summary`** — `ps -o rss` is
worthless for Metal processes (§5.146, off by 13 GB). Generation is capped at 8
tokens on purpose: the spike measures prefill and residency, not decode.

Two gotchas worth keeping:

- **Footprint is not the wired cap** (rule 93). These runs show a combined
  footprint of 20.1 GB — over the 18.0 GB ceiling — running fine, because only
  15.53 GB was wired. Gate on wired; footprint is diagnostic.
- The prompt varies its *leading* text per round (`round N. ...`), which
  deliberately defeats prefix caching so every request pays a real prefill.
  That is the worst case wanted here; do not "fix" it without also fixing the
  comparison to the solo baseline.

## Files

| file | what |
|---|---|
| `s1-load.jsonl` | stage 1, `qwen06`+`sushicoder` (floor 12.48 G), 6 requests |
| `s2-load.jsonl` | stage 2, `qwen06`+`qwythos9` (floor 16.00 G), 20 requests / 30.4 min — the exit-criterion run |
| `s3-solo.jsonl` | `qwythos9` alone under the identical load; the baseline the 31% tax is measured against |
| `watchdog-stage*.log` | 2s wired/compressor samples; `grep ABORT` is empty in both |
| `s{1,2}-*.log` | raw server stdout, incl. mlx's own prompt-cache accounting |
