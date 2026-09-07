#!/usr/bin/env python3
"""Does this model reuse a prompt cache AT ALL, budget aside?

Uses /v1/completions with pure string concatenation -- no chat template, no
decode/re-encode of generated text -- so the second prompt is guaranteed to be a
strict token-prefix extension of the first. If that misses, reuse is broken for
the architecture and no --prompt-cache-bytes value can help.
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx, random
from locode.config import Config

cfg = Config.load()
mid = cfg.aliases["qwen38"]
W = ("the quick brown fox jumps over lazy dog while parser validates tokens "
     "and returns a result from cache before writing output to disk").split()
r = random.Random(7)
base = " ".join(r.choice(W) for _ in range(7000))

def ask(prompt, label):
    t = time.time()
    try:
        resp = httpx.post(f"{cfg.server.endpoint()}/v1/completions",
                          json={"model": mid, "prompt": prompt, "max_tokens": 4,
                                "temperature": 0.0}, timeout=600)
        dt = time.time() - t
        if resp.status_code != 200:
            print(f"  {label:34s} {dt:7.1f}s  HTTP {resp.status_code}", flush=True)
            return dt
    except Exception as e:
        dt = time.time() - t
        print(f"  {label:34s} {dt:7.1f}s  FAILED {type(e).__name__}", flush=True)
        return dt
    print(f"  {label:34s} {dt:7.1f}s", flush=True)
    return dt

print("A cold (7,000 words)")
a = ask(base, "cold")
print("B identical prompt (exact match)")
b = ask(base, "exact repeat")
print("C strict prefix extension (+40 words)")
ext = base + " " + " ".join(r.choice(W) for _ in range(40))
c = ask(ext, "prefix + 40 words")
print("D second extension (+40 more)")
ext2 = ext + " " + " ".join(r.choice(W) for _ in range(40))
d = ask(ext2, "prefix + 80 words")
print(f"\nverdict: exact={b/a:.0%} of cold, ext1={c/a:.0%}, ext2={d/a:.0%}")
print("reuse WORKS" if c < 0.4*a else "NO REUSE on prefix extension")
