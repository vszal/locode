"""Reasoning A/B, faithful to locode: streaming, temperature 0.3, no penalties,
and locode's OWN runaway-repetition abort. One arm per invocation:
    python run2.py <model_id> <on|off|auto> <outfile>
"""
import json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, "/Users/vszalvay/Code/locode")
from locode.model import repetition            # locode's real detector

MLX = "/opt/homebrew/bin/mlx_lm.server"
HOST, PORT = "127.0.0.1", 8099
BASE = f"http://{HOST}:{PORT}"
MAX_TOKENS = 8192        # locode's model.max_tokens
TEMPERATURE = 0.3        # locode's model.temperature
CACHE_BYTES = 3 * (1024**3) // 2
HERE = os.path.dirname(os.path.abspath(__file__))


def launch(model_id, thinking):
    argv = [MLX, "--model", model_id, "--host", HOST, "--port", str(PORT)]
    if thinking is not None:
        argv += ["--chat-template-args", json.dumps({"enable_thinking": thinking})]
    argv += ["--max-tokens", str(MAX_TOKENS), "--prompt-cache-size", "4",
             "--prompt-cache-bytes", str(CACHE_BYTES)]
    log = open(os.path.join(HERE, f"srv-{thinking}.log"), "wb")
    print("launching:", " ".join(argv), flush=True)
    return subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)


def wait_up(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/v1/models", timeout=3).read()
            return True
        except Exception:
            time.sleep(2)
    return False


def ask(model_id, prompt):
    """Stream, mirroring locode/model/client.py: content vs reasoning deltas,
    and the same mid-stream runaway abort."""
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE, "stream": True,
    }).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    content, reasoning, finish = [], [], None
    since = 0
    t0 = time.time()
    ttfc = None                                   # time to first CONTENT token
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ch = json.loads(data)["choices"][0]
                delta = ch["delta"]
            except Exception:
                continue
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            piece = delta.get("content")
            if piece:
                if ttfc is None:
                    ttfc = round(time.time() - t0, 1)
                content.append(piece)
                since += len(piece)
                if since >= repetition.CHECK_STRIDE:
                    since = 0
                    if repetition.is_runaway_repetition("".join(content)):
                        finish = "repetition"
                        break
            rp = delta.get("reasoning_content") or delta.get("reasoning")
            if rp:
                reasoning.append(rp)
    return {"content": "".join(content), "reasoning": "".join(reasoning),
            "finish_reason": finish, "seconds": round(time.time() - t0, 1),
            "ttfc": ttfc}


def main():
    model_id, arm, outfile = sys.argv[1], sys.argv[2], sys.argv[3]
    thinking = {"on": True, "off": False, "auto": None}[arm]
    prompts = json.load(open(os.path.join(HERE, "prompts.json")))
    proc = launch(model_id, thinking)
    try:
        if not wait_up():
            print("SERVER DID NOT COME UP", flush=True); sys.exit(1)
        out = []
        for p in prompts:
            try:
                r = ask(model_id, p["prompt"])
            except Exception as e:
                r = {"content": "", "reasoning": "", "finish_reason": "ERROR",
                     "seconds": -1, "ttfc": None, "error": repr(e)}
            r.update(id=p["id"], kind=p["kind"], arm=arm)
            out.append(r)
            print(f"  {p['id']}: {r['seconds']}s ttfc={r['ttfc']} "
                  f"content={len(r['content'])} reasoning={len(r['reasoning'])} "
                  f"{r['finish_reason']}", flush=True)
        json.dump({"model": model_id, "arm": arm, "thinking": thinking,
                   "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
                   "runs": out}, open(outfile, "w"), indent=1)
        print("wrote", outfile, flush=True)
    finally:
        proc.terminate()
        try: proc.wait(timeout=30)
        except Exception: proc.kill()


main()
