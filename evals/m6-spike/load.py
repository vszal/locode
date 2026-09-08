"""M6.0 spike load driver: alternate real long-context requests across two
co-resident mlx_lm.server processes and record per-process physical footprint.

`ps -o rss` is worthless for Metal processes (rule of thumb from ROADMAP
§5.146: it reported 3.5 GB for a process at 16.6 GB), so footprint comes from
`vmmap -summary`. Generation is capped short on purpose -- the spike measures
prefill and residency, not decode throughput.
"""
import json, re, subprocess, sys, time
import httpx

def footprint_gb(pid):
    try:
        out = subprocess.run(["vmmap", "-summary", str(pid)], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        return None
    m = re.search(r"Physical footprint:\s+([\d.]+)([KMG])", out)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return v / {"K": 1048576, "M": 1024, "G": 1}[u]

def wired_gb():
    out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    ps = int(re.search(r"page size of (\d+)", out).group(1))
    w = int(re.search(r"Pages wired down:\s+(\d+)", out).group(1))
    return w * ps / 1073741824

def prompt_of(tokens):
    # ~3.6 chars/token for English prose; varied so it is not a degenerate
    # repeat the tokenizer could collapse.
    words = ("the quick brown fox jumps over a lazy dog while parsing tool call "
             "json emitted by a local model under memory pressure ").split()
    out, n = [], int(tokens * 0.78)
    for i in range(n):
        out.append(words[i % len(words)] + ("" if i % 17 else f" [{i}]"))
    return " ".join(out)

def main():
    arms = json.loads(sys.argv[1])       # [{name, port, pid, tokens}, ...]
    minutes = float(sys.argv[2])
    log = open(sys.argv[3], "a")
    body = prompt_of(max(a["tokens"] for a in arms))
    deadline = time.time() + minutes * 60
    rnd = 0
    while time.time() < deadline:
        rnd += 1
        for a in arms:
            p = body[: int(a["tokens"] * 3.6)]
            t0 = time.time()
            err = ""
            try:
                r = httpx.post(f"http://127.0.0.1:{a['port']}/v1/chat/completions",
                               json={"messages": [{"role": "user",
                                                   "content": f"round {rnd}. {p}\n\nReply with one word."}],
                                     "max_tokens": 8, "temperature": 0.0},
                               timeout=900)
                ok = r.status_code == 200
                ptok = r.json().get("usage", {}).get("prompt_tokens") if ok else None
                if not ok:
                    err = r.text[:200]
            except Exception as e:
                ok, ptok, err = False, None, repr(e)[:200]
            dt = time.time() - t0
            fps = {b["name"]: footprint_gb(b["pid"]) for b in arms}
            rec = {"t": time.strftime("%H:%M:%S"), "round": rnd, "arm": a["name"],
                   "ok": ok, "secs": round(dt, 1), "prompt_tokens": ptok,
                   "wired_gb": round(wired_gb(), 2),
                   "footprint_gb": {k: (round(v, 2) if v else None) for k, v in fps.items()},
                   "err": err}
            print(json.dumps(rec), file=log, flush=True)
            print(json.dumps(rec), flush=True)
            if not ok:
                return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
