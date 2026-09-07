#!/usr/bin/env python3
"""Does a CHAT turn keep the prefix stable across turns?

The prefix test proved raw prefix extension hits (1.1s vs 70.5s cold). A chat
turn differs in one way that matters: the assistant's reply is stored by the
server as TOKENS, but the client appends it back as TEXT, which the template
re-tokenizes. If decode->encode is not identity the prefix breaks and the whole
prompt is re-prefilled.

Arm A lets the model finish its reply naturally. Arm B truncates mid-token with
max_tokens=8 -- what the earlier probe did -- to see if that alone explains its
100% miss rate.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx, random
from locode.config import Config

cfg = Config.load(); mid = cfg.aliases["qwen38"]
W = ("the quick brown fox jumps over lazy dog while parser validates tokens "
     "and returns a result from cache before writing output to disk").split()

def turn(msgs, maxtok, label):
    t = time.time()
    r = httpx.post(f"{cfg.server.endpoint()}/v1/chat/completions",
                   json={"model": mid, "messages": msgs, "max_tokens": maxtok,
                         "temperature": 0.0}, timeout=600)
    dt = time.time() - t
    txt = r.json()["choices"][0]["message"]["content"] or ""
    print(f"  {label:34s} {dt:7.1f}s  reply={len(txt)}ch", flush=True)
    return dt, txt

for label, maxtok, seed in (("A natural stop (max_tokens=200)", 200, 11),
                            ("B truncated  (max_tokens=8)", 8, 22)):
    r = random.Random(seed)
    filler = " ".join(r.choice(W) for _ in range(7000))
    msgs = [{"role": "system", "content": "You are a terse assistant."},
            {"role": "user", "content": filler + "\n\nReply with exactly: OK"}]
    print(label)
    t1, reply = turn(msgs, maxtok, "turn 1 (cold)")
    msgs.append({"role": "assistant", "content": reply})
    msgs.append({"role": "user", "content": "thanks"})
    t2, _ = turn(msgs, maxtok, "turn 2 (should reuse)")
    print(f"  -> turn2 = {t2/t1:.0%} of cold  "
          f"{'HIT (prefix stable)' if t2 < 0.3*t1 else 'MISS (prefix broken)'}\n", flush=True)
