#!/usr/bin/env python3
"""Measure what --prompt-cache-bytes actually buys, at 1.0x / 1.5x / 2.0x.

Sends the SAME token sequences to the server under each budget, so the only
variable is the budget. Four conversations are advanced round-robin -- the shape
that produced "Prompt Cache: 4 sequences, 5.93 GB" against a 1.5 GB budget in
the 2026-09-07 session, then a collapse to 0.41 GB (system only).

Per request we record wallclock. A cache hit decodes a few tokens in seconds; a
miss re-prefills the whole prompt and costs ~100s. That ratio is the result.

A watchdog kills the server if RSS crosses --rss-limit-gb, because mlx enforces
--prompt-cache-bytes lazily (it was observed 4x over budget) and overshooting
the macOS wired cap panics the GPU driver rather than swapping.
"""
import argparse, asyncio, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from locode.config import Config
from locode.server.manager import SingleGpuManager, GB

LOG = Path.home() / ".local/state/locode/mlx-server.log"
POOL = re.compile(r"Prompt Cache: (\d+) sequences, ([\d.]+) GB")


def rss_gb(pid: int) -> float:
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        return int(out) / (1024 * 1024) if out else 0.0
    except Exception:
        return 0.0


def pool_after(offset: int) -> tuple[float, int]:
    """Largest pool reading written to the log since byte `offset`."""
    with open(LOG, "rb") as f:
        f.seek(offset)
        tail = f.read().decode("utf-8", "replace")
    hits = POOL.findall(tail)
    if not hits:
        return 0.0, 0
    return max(float(g) for _, g in hits), max(int(n) for n, _ in hits)


def filler(words: int, salt: str) -> str:
    # Distinct per conversation so the four share no prefix beyond the system
    # message -- otherwise they would dedupe and the pool would never fill.
    return " ".join(f"{salt}{i:05d}" for i in range(words))


async def one_arm(mult: float, args) -> dict:
    cfg = Config.load()
    cfg.server.prompt_cache_multiple = mult
    cfg.agent.max_history_chars = args.history_chars
    mgr = SingleGpuManager(cfg)
    model = mgr.resolve(args.model)
    print(f"\n=== {mult}x  (model {model}) ===", flush=True)

    await mgr.stop()
    await asyncio.sleep(2)
    log_start = LOG.stat().st_size if LOG.exists() else 0
    t0 = time.time()
    await mgr.start(args.model)
    pid = mgr._proc.pid
    argv_cache = None
    try:
        argv_cache = int(subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
            text=True).stdout.split("--prompt-cache-bytes")[1].split()[0])
    except Exception:
        pass
    print(f"  launched pid {pid} in {time.time()-t0:.0f}s, "
          f"--prompt-cache-bytes={argv_cache} "
          f"({(argv_cache or 0)/GB:.2f} GB)", flush=True)

    convs = [[{"role": "system", "content": "You are a terse assistant."},
              {"role": "user", "content": filler(args.words, f"c{c}w")}]
             for c in range(args.convs)]
    rows, peak_rss = [], 0.0
    async with httpx.AsyncClient(timeout=900) as http:
        for rnd in range(args.rounds):
            for c, conv in enumerate(convs):
                r = rss_gb(pid)
                peak_rss = max(peak_rss, r)
                if r > args.rss_limit_gb:
                    print(f"  !! RSS {r:.1f} GB over limit — killing", flush=True)
                    await mgr.stop()
                    return {"multiple": mult, "aborted_rss_gb": r, "rows": rows}
                t = time.time()
                resp = await http.post(
                    f"{cfg.server.endpoint()}/v1/chat/completions",
                    json={"model": model, "messages": conv,
                          "max_tokens": 8, "temperature": 0.0})
                dt = time.time() - t
                txt = resp.json()["choices"][0]["message"]["content"] or ""
                conv.append({"role": "assistant", "content": txt})
                # A short "tool result": grows the conversation the way a real
                # turn does, so the next request needs the previous prefix.
                conv.append({"role": "user", "content": filler(60, f"c{c}r{rnd}t")})
                rows.append({"round": rnd, "conv": c, "seconds": round(dt, 1),
                             "rss_gb": round(r, 2)})
                print(f"  r{rnd} c{c}: {dt:7.1f}s  rss {r:5.1f} GB", flush=True)
    pool_gb, pool_seqs = pool_after(log_start)
    await mgr.stop()
    return {"multiple": mult, "cache_bytes": argv_cache, "peak_rss_gb": round(peak_rss, 2),
            "peak_pool_gb": pool_gb, "peak_pool_seqs": pool_seqs, "rows": rows}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen38")
    ap.add_argument("--multiples", default="1.0,1.5,2.0")
    ap.add_argument("--convs", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--words", type=int, default=7000)      # ~11k tokens
    ap.add_argument("--history-chars", type=int, default=60_000)
    ap.add_argument("--rss-limit-gb", type=float, default=20.0)
    ap.add_argument("--out", default="evals/cache-probe/results.json")
    args = ap.parse_args()

    out = []
    for m in [float(x) for x in args.multiples.split(",")]:
        out.append(await one_arm(m, args))
        Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {args.out}")


asyncio.run(main())
