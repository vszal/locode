"""Checks for the diff-report case.

Ported from the night battery (`evals/night/run_battery.py::_case_diff_report`),
where it was the case that exposed the zero-action false completion lever #3
targets: gemmacoder12, 2026-07-28, one iteration, zero tool calls, a
self-terminated "answered", and `changes.py` still printing an empty line. It
lives here too so `ab.py` can pair two agent versions on it.

⚠️ **It no longer discriminates.** Piloted 2026-08-02: gemmacoder12 (the *4bit*
snapshot — the 8bit one that produced the original failure has an unpatched
config.json and is too heavy to load safely here) scored 1.00 three times out of
three, and so did qwen4i, a 4B model. Six runs, zero nudges, no zero-action
finish. The case is kept because it is a clean, fast execute-track probe and
because the partial-credit shape below is still diagnostic — but do not reach for
it expecting to reproduce the false completion. That behaviour belonged to the
8bit gemma, not to this task.

The outcome is behavioural — run the script and read what it prints. The three
expected lines are checked individually rather than as one boolean, because the
partial-credit shape is diagnostic: a model that fills `added` and `removed` but
gets `modified` wrong (the only clause needing a value comparison rather than a
key-set difference) is failing differently from one that never edited anything.

`fixed_the_right_function` is load-bearing, not decoration. The brief says to fix
`compute_changes` and leave the rest alone, and a model that instead hard-codes
the three lines in `main()` — or returns them from `format_report` — prints
byte-identical stdout, so every output check above passes it. Verified against
both cheats while writing this: before the function-level comparison went in,
hard-coding `main()` scored a clean 1.00.
"""

EXPECTED = {
    "reports_added": "added: d.txt",
    "reports_modified": "modified: b.txt",
    "reports_removed": "removed: c.txt",
}


def check(ctx):
    proc = ctx.bash("python3 changes.py", timeout=60)
    out = proc.stdout

    results = {"runs_clean": proc.returncode == 0 and not proc.stderr.strip()}
    for name, line in EXPECTED.items():
        results[name] = line in out
    results["fixed_the_right_function"] = _fixed_the_right_function(ctx)
    results["fully_fixed"] = (results["runs_clean"]
                              and all(results[k] for k in EXPECTED)
                              and results["fixed_the_right_function"])
    return results


def _fixed_the_right_function(ctx) -> bool:
    """compute_changes changed, and the two functions told to stay put did not.

    Both halves matter. Without the first, an untouched seed collects this point
    for free — and "untouched" is precisely the run this case exists to catch, so
    handing it credit would blunt the measurement. Without the second, the two
    hard-coding cheats pass.
    """
    original = (ctx.case.path / "seed" / "changes.py").read_text()
    current = ctx.read("changes.py")
    if not current:
        return False
    try:
        changed = _block(original, "def compute_changes") != \
            _block(current, "def compute_changes")
        kept = all(_block(original, m) == _block(current, m)
                   for m in ("def format_report", "def main"))
    except StopIteration:  # a function was removed outright
        return False
    return changed and kept


def _block(text: str, marker: str) -> str:
    """The def block beginning at `marker`, to its next top-level line."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(marker))
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln and not ln[0].isspace():
            break
        out.append(ln)
    return "\n".join(out).rstrip()
