#!/usr/bin/env python3
"""Overnight prompt battery: run a variety of realistic tasks against local
models, capture each session (transcript + event log), then score it on (a) did
the task actually get done and (b) how much did the model flail getting there.

This is the observation engine for the "run varied prompts, find issues, fix,
repeat" loop. It reuses evals/replay.py for the pathology summary (repeats,
no-ops, fails, stop reason) so the numbers here match what a human sees on screen.

    python evals/night/run_battery.py --models gemmacoder12,qythos9 \
        --outdir evals/night/results/pass1 [--cases logic-bug,indent-bug] [--reps 1]

Prints a scannable table and writes <outdir>/<case>__<model>__r<n>/{events.jsonl,
transcript.txt,workdir/}. A row is a PROBLEM when done=NO (task not accomplished)
or when it flailed hard (repeat-stop / many no-ops) even if it eventually landed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "evals"))
import replay  # noqa: E402

LOCODE = str(ROOT / ".venv" / "bin" / "locode")
PY = str(ROOT / ".venv" / "bin" / "python")


def _run_py(workdir: Path, code: str, timeout: int = 20) -> tuple[int, str]:
    """Run a python snippet in workdir; return (rc, combined output)."""
    try:
        p = subprocess.run([PY, "-c", code], cwd=workdir, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def _compiles(workdir: Path, name: str) -> bool:
    rc, _ = _run_py(workdir, f"import py_compile,sys; py_compile.compile('{name}', doraise=True)")
    return rc == 0


# --- the battery -------------------------------------------------------------
# Each case: files (name->content), prompt (PLAIN PROSE — brace-dense specs
# corrupt weak models' tool JSON), and check(workdir)->(ok, detail).
def _case_logic_bug():
    files = {"calc.py": "def add(a, b):\n    return a - b\n"}
    prompt = ("calc.py has an add function that subtracts instead of adds. Fix it, "
              "then verify by running: python3 -c \"import calc; print(calc.add(2,3))\" "
              "which should print 5.")
    def check(w):
        rc, out = _run_py(w, "import calc; print(calc.add(2,3))")
        return (rc == 0 and out.strip() == "5", f"add(2,3)={out.strip()!r}")
    return files, prompt, check


def _case_indent_bug():
    files = {"io_util.py": (
        "def read_all(path):\n"
        "    with open(path) as f:\n"
        "data = f.read()\n"          # dedented body -> IndentationError
        "    return data\n")}
    prompt = ("io_util.py fails to import with an IndentationError inside read_all. "
              "Fix the indentation so the file compiles cleanly. Verify with "
              "python3 -m py_compile io_util.py.")
    def check(w):
        return (_compiles(w, "io_util.py"), "compiles" if _compiles(w, "io_util.py") else "still broken")
    return files, prompt, check


def _case_undefined_vars():
    files = {"sync.py": (
        "def changed(src, dst):\n"
        "    new = []\n"
        "    for name in source_files:\n"       # undefined
        "        if name not in dest_files:\n"   # undefined
        "            new.append(name)\n"
        "    return new\n\n"
        "if __name__ == '__main__':\n"
        "    print(changed('a', 'b'))\n")}
    prompt = ("sync.py crashes with a NameError because the changed function refers "
              "to source_files and dest_files that are never defined. Make it list "
              "the files in the src and dst directory arguments (using os.listdir) "
              "and return the names present in src but not dst. It must run without "
              "error: verify with python3 sync.py (an empty list output is fine).")
    def check(w):
        (w / "a").mkdir(exist_ok=True)
        (w / "b").mkdir(exist_ok=True)
        rc, out = _run_py(w, "import sync; print(sync.changed('a','b'))")
        return (rc == 0, f"rc={rc} {out.strip()[:60]!r}")
    return files, prompt, check


def _case_add_logging():
    files = {"job.py": (
        "import os\n\n"
        "def run(items):\n"
        "    for it in items:\n"
        "        process(it)\n\n"
        "def process(it):\n"
        "    return it\n")}
    prompt = ("job.py runs almost silently. Make run verbose: print a line for each "
              "item as it is processed, and print a summary line with the total "
              "count when done. Keep it valid Python (it must still import).")
    def check(w):
        if not _compiles(w, "job.py"):
            return (False, "does not compile")
        txt = (w / "job.py").read_text()
        return (txt.count("print(") >= 2, f"{txt.count('print(')} prints")
    return files, prompt, check


def _case_new_module():
    files = {}
    prompt = ("Create a new file fib.py containing a function fib that takes an "
              "integer n and returns the nth Fibonacci number counting from zero, "
              "so fib of 0 is 0, fib of 1 is 1, and fib of 10 is 55. Compute it "
              "iteratively, not recursively. Then verify by running python3 -c "
              "\"import fib; print(fib.fib(10))\" which should print 55.")
    def check(w):
        rc, out = _run_py(w, "import fib; print(fib.fib(10))")
        return (rc == 0 and out.strip() == "55", f"fib(10)={out.strip()!r}")
    return files, prompt, check


def _case_refactor_rename():
    files = {"geo.py": (
        "def compute(x):\n"
        "    return x * 2\n\n"
        "a = compute(3)\n"
        "b = compute(4)\n"
        "print(a, b)\n")}
    prompt = ("In geo.py, rename the function compute to double everywhere it "
              "appears, including the two call sites. The behavior must stay the "
              "same. Verify with python3 geo.py, which should print 6 8.")
    def check(w):
        rc, out = _run_py(w, "import subprocess,sys; subprocess.run([sys.executable,'geo.py'])")
        txt = (w / "geo.py").read_text()
        ok = "compute" not in txt and "double" in txt and _compiles(w, "geo.py")
        return (ok, f"compute_gone={'compute' not in txt}")
    return files, prompt, check


def _case_syntax_fix():
    files = {"parser.py": (
        "def parse(line)\n"           # missing colon
        "    parts = line.split(',')\n"
        "    return parts\n")}
    prompt = ("parser.py has a syntax error and won't compile. Find and fix it so "
              "python3 -m py_compile parser.py succeeds.")
    def check(w):
        return (_compiles(w, "parser.py"), "compiles" if _compiles(w, "parser.py") else "broken")
    return files, prompt, check


def _case_add_test():
    files = {}
    prompt = ("Create primes.py with a function is_prime that returns True when its "
              "integer argument is a prime number and False otherwise (numbers less "
              "than 2 are not prime). Also create test_primes.py with pytest tests "
              "covering a few primes and non-primes. Then run the tests with "
              f"{PY} -m pytest test_primes.py -q and make sure they pass.")
    def check(w):
        rc, out = _run_py(w, "import primes; print(primes.is_prime(7), primes.is_prime(8))")
        return (rc == 0 and out.strip() == "True False", f"is_prime probe={out.strip()!r}")
    return files, prompt, check


def _case_read_before_edit():
    # The correct value lives in ANOTHER file — the model must read config.py
    # before editing server.py, not guess. Stresses read-then-edit.
    files = {
        "config.py": "PORT = 8080\nHOST = 'localhost'\n",
        "server.py": ("def url():\n"
                      "    return 'http://localhost:9090/api'\n"),
    }
    prompt = ("server.py hardcodes port 9090 in its url function, but the correct "
              "port is the PORT value defined in config.py. Look at config.py to "
              "find the right port, then update server.py so url returns a URL "
              "using that port. Verify with python3 -c \"import server; "
              "print(server.url())\" which should print http://localhost:8080/api.")
    def check(w):
        rc, out = _run_py(w, "import server; print(server.url())")
        return (rc == 0 and out.strip() == "http://localhost:8080/api",
                f"url={out.strip()!r}")
    return files, prompt, check


def _case_rename_across_files():
    # A symbol used in TWO files: definition + import + call site. Single-file
    # rename is already covered by refactor-rename; this needs grep/glob + edits
    # that stay consistent across files.
    files = {
        "models.py": "def get_user(uid):\n    return uid * 10\n",
        "views.py": ("from models import get_user\n\n"
                     "def show(uid):\n"
                     "    return get_user(uid)\n"),
    }
    prompt = ("Rename the function get_user to fetch_user everywhere it appears "
              "across BOTH models.py and views.py — the definition, the import, and "
              "the call site — keeping behavior identical. Nothing named get_user "
              "may remain. Verify with python3 -c \"import views; "
              "print(views.show(3))\" which should print 30.")
    def check(w):
        rc, out = _run_py(w, "import views; print(views.show(3))")
        m = (w / "models.py").read_text()
        v = (w / "views.py").read_text()
        gone = "get_user" not in m and "get_user" not in v
        renamed = "fetch_user" in m and "fetch_user" in v
        return (rc == 0 and out.strip() == "30" and gone and renamed,
                f"show(3)={out.strip()!r} get_user_gone={gone} renamed={renamed}")
    return files, prompt, check


def _case_fix_traceback():
    # Crashes at RUNTIME (KeyError), not compile time — the model must run it,
    # read the traceback, and fix. Different signal from syntax-fix (py_compile)
    # and add-test (pytest green).
    files = {"report.py": (
        "def total(rows):\n"
        "    return sum(r['amount'] for r in rows)\n\n"
        "data = [{'amount': 5}, {'amount': 7}, {'cost': 3}]\n"
        "print(total(data))\n")}
    prompt = ("report.py crashes when you run it, because one row is missing its "
              "amount. Run it to see the error, then fix the total function so a "
              "row with no amount counts as zero instead of crashing. Verify with "
              "python3 report.py, which should print 12.")
    def check(w):
        rc, out = _run_py(w, "import subprocess,sys;"
                             "p=subprocess.run([sys.executable,'report.py'],"
                             "capture_output=True,text=True);"
                             "print('RC',p.returncode);print(p.stdout.strip())")
        printed_12 = "\n12" in ("\n" + out) and "RC 0" in out
        return (printed_12, f"out={out.strip()[:60]!r}")
    return files, prompt, check


def _case_even_median():
    # A WRONG-OUTPUT bug with no traceback: the model must reason about the
    # even/odd median edge case, not just make a crash go away. Stresses
    # correctness reasoning (the workhorse-stressing dimension).
    files = {"stats.py": (
        "def median(nums):\n"
        "    s = sorted(nums)\n"
        "    return s[len(s) // 2]\n")}
    prompt = ("stats.py has a median function that is wrong for even-length lists: "
              "the median of [1, 2, 3, 4] should be 2.5 (the average of the two "
              "middle values) but it returns 3. Fix median so it averages the two "
              "middle values when the length is even, and still returns the single "
              "middle value when it is odd. Verify with python3 -c \"import stats; "
              "print(stats.median([1,2,3,4]), stats.median([1,2,3]))\" — the median "
              "of [1,2,3,4] should be 2.5 and of [1,2,3] should be 2.")
    def check(w):
        rc, out = _run_py(w, "import stats;"
                             "a=stats.median([1,2,3,4]);b=stats.median([1,2,3]);"
                             "print(abs(a-2.5)<1e-9 and abs(b-2)<1e-9)")
        return (rc == 0 and out.strip() == "True", f"ok={out.strip()!r}")
    return files, prompt, check


def _case_dedup_order():
    # Another wrong-output reasoning bug: dedup that loses order. The model must
    # know set()+sorted drops first-seen order and implement an order-preserving
    # dedup — no error guides it.
    files = {"util.py": (
        "def dedup(items):\n"
        "    return sorted(set(items))\n")}
    prompt = ("util.py has a dedup function meant to remove duplicates while "
              "keeping first-seen order, but it uses set and sorting, which loses "
              "the original order: dedup([3, 1, 3, 2, 1]) should return [3, 1, 2] "
              "but returns [1, 2, 3]. Fix dedup to remove duplicates and preserve "
              "the order in which each value first appears. Verify with python3 -c "
              "\"import util; print(util.dedup([3,1,3,2,1]))\" which should print "
              "[3, 1, 2].")
    def check(w):
        rc, out = _run_py(w, "import util; print(util.dedup([3,1,3,2,1]))")
        return (rc == 0 and out.strip() == "[3, 1, 2]", f"out={out.strip()!r}")
    return files, prompt, check


def _case_already_correct():
    # The "nothing to fix" path — every other case ships a real bug; this one
    # does NOT. The code is already correct. A good model verifies and finishes
    # cleanly WITHOUT editing; a flailing one over-edits working code (repeats /
    # no-ops / a syntax-guard save). Directly probes the finish/flail dimension.
    files = {"palindrome.py": (
        "def is_palindrome(s):\n"
        "    s = s.lower()\n"
        "    cleaned = [c for c in s if c.isalnum()]\n"
        "    return cleaned == cleaned[::-1]\n")}
    prompt = ("palindrome.py has an is_palindrome function that should ignore case, "
              "spaces, and punctuation. Verify it correctly returns True for "
              "\"A man, a plan, a canal: Panama\" and False for \"hello\", and fix "
              "it only if it is actually wrong. Check with python3 -c \"import "
              "palindrome as p; print(p.is_palindrome('A man, a plan, a canal: "
              "Panama'), p.is_palindrome('hello'))\" which should print True False.")
    def check(w):
        rc, out = _run_py(w, "import palindrome as p;"
                             "print(p.is_palindrome('A man, a plan, a canal: Panama'),"
                             "p.is_palindrome('hello'))")
        return (rc == 0 and out.strip() == "True False", f"out={out.strip()!r}")
    return files, prompt, check


CASES = {
    "logic-bug": _case_logic_bug,
    "indent-bug": _case_indent_bug,
    "undefined-vars": _case_undefined_vars,
    "add-logging": _case_add_logging,
    "new-module": _case_new_module,
    "refactor-rename": _case_refactor_rename,
    "syntax-fix": _case_syntax_fix,
    "add-test": _case_add_test,
    "read-before-edit": _case_read_before_edit,
    "rename-across-files": _case_rename_across_files,
    "fix-traceback": _case_fix_traceback,
    "even-median": _case_even_median,
    "dedup-order": _case_dedup_order,
    "already-correct": _case_already_correct,
}


def run_one(case: str, model: str, rep: int, outdir: Path, *,
            max_iter: int, max_wall: int) -> dict:
    files, prompt, check = CASES[case]()
    rundir = outdir / f"{case}__{model}__r{rep}"
    work = rundir / "workdir"
    work.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (work / name).write_text(content)
    log = rundir / "events.jsonl"
    transcript = rundir / "transcript.txt"
    cmd = [LOCODE, "-p", prompt, "-m", model, "--no-splash", "--no-markdown",
           "--show-events", "--allow-tool",
           "edit_file,write_file,replace_lines,read_file,bash,glob,grep",
           "--max-iterations", str(max_iter), "--max-wallclock", str(max_wall),
           "--log-events", str(log)]
    t0 = time.monotonic()
    with open(transcript, "w") as tf:
        try:
            subprocess.run(cmd, cwd=work, stdout=tf, stderr=subprocess.STDOUT,
                           timeout=max_wall + 60)
        except subprocess.TimeoutExpired:
            tf.write("\n[BATTERY: hard timeout]\n")
    wall = time.monotonic() - t0
    events = replay.load(log) if log.exists() else []
    s = replay.summarize(events)
    try:
        ok, detail = check(work)
    except Exception as e:
        ok, detail = False, f"check-raised {type(e).__name__}: {e}"
    return {"case": case, "model": model, "rep": rep, "done": ok, "detail": detail,
            "wall": wall, "s": s, "rundir": rundir}


def _problem(row: dict) -> bool:
    s = row["s"]
    return (not row["done"] or s["repeats"] or s["noops"] >= 2
            or (s["stop_reason"] and "repeat" in (s["stop_reason"] or "").lower()))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gemmacoder12,qythos9")
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--max-iter", type=int, default=18)
    ap.add_argument("--max-wall", type=int, default=180)
    a = ap.parse_args(argv)

    # ABSOLUTE — the subprocess runs with cwd=workdir, so a relative --log-events
    # path would be written nested under workdir and replay.load() would read an
    # empty file (every pathology count bogusly zero). Learned the hard way.
    outdir = Path(a.outdir).resolve()
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    models = [m for m in a.models.split(",") if m]
    cases = [c for c in a.cases.split(",") if c in CASES]

    rows = []
    for case in cases:
        for model in models:
            for rep in range(1, a.reps + 1):
                row = run_one(case, model, rep, outdir, max_iter=a.max_iter,
                              max_wall=a.max_wall)
                rows.append(row)
                s = row["s"]
                flag = "PROBLEM" if _problem(row) else "ok"
                print(f"{flag:>7} | {case:<15} {model:<13} | "
                      f"done={'Y' if row['done'] else 'N'} | "
                      f"{s['iterations']:>2}it {row['wall']:>4.0f}s | "
                      f"f{s['fails']} n{s['noops']} r{s['repeats']} "
                      f"{'green' if s['saw_green'] else '     '} | "
                      f"{(s['stop_reason'] or 'answered')[:34]:<34} | {row['detail'][:40]}",
                      flush=True)

    probs = [r for r in rows if _problem(r)]
    print(f"\n=== {len(probs)}/{len(rows)} problem rows ===")
    for r in probs:
        print(f"  {r['case']}__{r['model']}__r{r['rep']}  -> replay: "
              f"python evals/replay.py {r['rundir']}/events.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
