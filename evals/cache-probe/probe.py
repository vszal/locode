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
import argparse, asyncio, json, os, random, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from locode.config import Config
from locode.server.manager import SingleGpuManager, GB

LOG = Path.home() / ".local/state/locode/mlx-server.log"
POOL = re.compile(r"Prompt Cache: (\d+) sequences, ([\d.]+) GB")


def footprint_gb(pid: int) -> float:
    """Physical footprint, which is the only number that means anything here.

    `ps -o rss` reported 3.5 GB for a process whose real footprint was 16.6 GB
    (peak 17.8) -- Metal's wired buffers do not show up in RSS, so an RSS
    watchdog on a GPU process never fires. The wired cap is what panics the
    driver, so the watchdog has to read the footprint.
    """
    try:
        out = subprocess.run(["vmmap", "-summary", str(pid)],
                             capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            if line.strip().startswith("Physical footprint:"):
                v = line.split(":", 1)[1].strip()
                n = float(v.rstrip("GMK"))
                return n if v.endswith("G") else n / 1024 if v.endswith("M") else 0.0
    except Exception:
        pass
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


# Every one of these is a single vocab token for this model (checked against
# tokenizer.json), so words == tokens and the sequence length is predictable.
# The first draft used "c0w00042"-style ids, which split into ~8 tokens each and
# made an 11k-token target come out at 56,023 -- 5x the intended size and 5x the
# prefill time. Hence the assertion in one_arm().
_WORDS = ("the quick brown fox jumps over lazy dog while parser validates tokens "
          "and returns a result from cache before writing output to disk").split()


def filler(words: int, salt: str) -> str:
    # Seeded per conversation so the four diverge immediately after the system
    # message -- otherwise they would share a prefix, dedupe, and the pool would
    # never fill, which is the whole thing being measured.
    r = random.Random(salt)
    return " ".join(r.choice(_WORDS) for _ in range(words))


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

    PROMPT_TOK = re.compile(r"Prompt processing progress: \d+/(\d+)")

    convs = [[{"role": "system", "content": "You are a terse assistant."},
              {"role": "user", "content": filler(args.words, f"c{c}w")}]
             for c in range(args.convs)]
    rows, peak_fp = [], 0.0
    async with httpx.AsyncClient(timeout=900) as http:
        for rnd in range(args.rounds):
            for c, conv in enumerate(convs):
                r = footprint_gb(pid)
                peak_fp = max(peak_fp, r)
                if r > args.rss_limit_gb:
                    print(f"  !! footprint {r:.1f} GB over limit — killing", flush=True)
                    await mgr.stop()
                    return {"multiple": mult, "aborted_footprint_gb": r, "rows": rows}
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
                             "footprint_gb": round(r, 2)})
                if rnd == 0 and c == 0:
                    # Fail loudly on a mis-sized prompt rather than spending an
                    # hour measuring the wrong thing.
                    with open(LOG, "rb") as f:
                        f.seek(log_start)
                        seen = PROMPT_TOK.findall(f.read().decode("utf-8", "replace"))
                    if seen:
                        n = int(seen[-1])
                        print(f"  first prompt = {n:,} tokens", flush=True)
                        if not 0.6 * args.words <= n <= 1.8 * args.words:
                            await mgr.stop()
                            raise SystemExit(
                                f"prompt is {n:,} tokens, expected ~{args.words:,} "
                                "— fix the filler before spending an hour on this")
                print(f"  r{rnd} c{c}: {dt:7.1f}s  fp {r:5.1f} GB", flush=True)
    pool_gb, pool_seqs = pool_after(log_start)
    await mgr.stop()
    return {"multiple": mult, "cache_bytes": argv_cache, "peak_footprint_gb": round(peak_fp, 2),
            "peak_pool_gb": pool_gb, "peak_pool_seqs": pool_seqs, "rows": rows}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen38")
    ap.add_argument("--multiples", default="1.0,1.5,2.0")
    ap.add_argument("--convs", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--words", type=int, default=7000)      # ~7k tokens (1 tok/word)
    ap.add_argument("--history-chars", type=int, default=60_000)
    ap.add_argument("--rss-limit-gb", dest="rss_limit_gb", type=float, default=17.5)
    ap.add_argument("--out", default="evals/cache-probe/results.json")
    args = ap.parse_args()

    out = []
    for m in [float(x) for x in args.multiples.split(",")]:
        out.append(await one_arm(m, args))
        Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {args.out}")


asyncio.run(main())
