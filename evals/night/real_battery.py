#!/usr/bin/env python3
"""Realistic-task battery: the *lived interactive experience* probe.

The night battery (run_battery.py) is saturated for qythos9 — 31/31 clean at
f0 n0 r0 in recent sweeps — while the user reports the same model is "basically
not usable" on simple real tasks. That gap is the point of this file. Every
run_battery case is a 2-50 line synthetic file, names the file to edit, and
spells out the verify command. Real tasks do none of that.

Each case here is a small but *real* multi-file package where the model must:
  (a) FIND the relevant code (the prompt names a symptom, not a file),
  (b) read enough of a few-hundred-line project to orient,
  (c) make a coordinated change across more than one place,
  (d) verify on its own (no command handed to it).

Same runner contract as run_battery.py so replay.py/watch.sh work unchanged.

    python evals/night/real_battery.py --models qythos9 \
        --outdir evals/night/results/real1 [--cases locate-symptom] [--reps 3]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "evals"))
import replay  # noqa: E402

LOCODE = str(ROOT / ".venv" / "bin" / "locode")
PY = str(ROOT / ".venv" / "bin" / "python")


def _run_py(workdir: Path, code: str, timeout: int = 25) -> tuple[int, str]:
    try:
        p = subprocess.run([PY, "-c", code], cwd=workdir, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def _run_cli(workdir: Path, *args: str, timeout: int = 25) -> tuple[int, str]:
    try:
        p = subprocess.run([PY, "cli.py", *args], cwd=workdir,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


# --- the shared fake project -------------------------------------------------
# A small invoicing CLI. Deliberately ordinary: dataclasses, a formatter, a
# thin argparse front end, and a test file. ~230 lines across 5 files -- big
# enough that the model has to orient, small enough to fit a 9B context.

_MODEL_PY = '''"""Invoice data model."""

from dataclasses import dataclass, field


TAX_RATE = 0.08


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price: float

    def line_total(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Invoice:
    number: str
    customer: str
    items: list = field(default_factory=list)

    def add_item(self, description, quantity, unit_price):
        self.items.append(LineItem(description, quantity, unit_price))

    def subtotal(self) -> float:
        return sum(item.line_total() for item in self.items)

    def tax(self) -> float:
        return round(self.subtotal() * TAX_RATE, 2)

    def total(self) -> float:
        return round(self.subtotal(), 2)


def from_dict(data) -> Invoice:
    inv = Invoice(number=data["number"], customer=data["customer"])
    for row in data.get("items", []):
        inv.add_item(row["description"], row["quantity"], row["unit_price"])
    return inv
'''

_REPORT_PY = '''"""Human-readable invoice rendering."""

WIDTH = 46


def _rule(char="-"):
    return char * WIDTH


def format_line(item):
    left = "{0} x{1}".format(item.description, item.quantity)
    right = "{0:.2f}".format(item.line_total())
    pad = WIDTH - len(left) - len(right)
    if pad < 1:
        pad = 1
    return left + (" " * pad) + right


def format_report(invoice):
    lines = []
    lines.append(_rule("="))
    lines.append("Invoice {0}".format(invoice.number))
    lines.append("Customer: {0}".format(invoice.customer))
    lines.append(_rule())
    for item in invoice.items:
        lines.append(format_line(item))
    lines.append(_rule())
    lines.append("Subtotal: {0:.2f}".format(invoice.subtotal()))
    lines.append("Tax: {0:.2f}".format(invoice.tax()))
    lines.append("Total: {0:.2f}".format(invoice.total()))
    return "\\n".join(lines)
'''

_CLI_PY = '''"""Invoice CLI front end."""

import argparse
import json
import sys

from invoice.model import from_dict
from invoice.report import format_report


def load_invoice(path):
    with open(path) as handle:
        return from_dict(json.load(handle))


def cmd_report(args):
    invoice = load_invoice(args.path)
    print(format_report(invoice))
    return 0


def cmd_total(args):
    invoice = load_invoice(args.path)
    print("{0:.2f}".format(invoice.total()))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="invoice")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="print the full invoice report")
    report.add_argument("path")
    report.set_defaults(func=cmd_report)

    total = sub.add_parser("total", help="print just the invoice total")
    total.add_argument("path")
    total.set_defaults(func=cmd_total)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
'''

_TEST_PY = '''from invoice.model import Invoice, LineItem, from_dict


def test_line_total():
    item = LineItem("widget", 3, 2.5)
    assert item.line_total() == 7.5


def test_subtotal():
    inv = Invoice(number="A-1", customer="Acme")
    inv.add_item("widget", 2, 10.0)
    inv.add_item("gadget", 1, 5.0)
    assert inv.subtotal() == 25.0


def test_from_dict():
    inv = from_dict({"number": "A-2", "customer": "Beta",
                     "items": [{"description": "bolt", "quantity": 4,
                                "unit_price": 1.25}]})
    assert inv.number == "A-2"
    assert len(inv.items) == 1
'''

_DATA_JSON = json.dumps({
    "number": "INV-1001",
    "customer": "Acme Corp",
    "items": [
        {"description": "widget", "quantity": 4, "unit_price": 25.0},
        {"description": "gadget", "quantity": 2, "unit_price": 12.5},
    ],
}, indent=2)


def _project() -> dict:
    """The shared multi-file project every realistic case starts from."""
    return {
        "cli.py": _CLI_PY,
        "invoice/__init__.py": "",
        "invoice/model.py": _MODEL_PY,
        "invoice/report.py": _REPORT_PY,
        "tests/test_model.py": _TEST_PY,
        "data.json": _DATA_JSON,
    }


# --- realistic cases ---------------------------------------------------------
# Prompts are PLAIN PROSE, name a SYMPTOM not a file, and hand over NO verify
# command. That is the whole delta from run_battery.py.

def _case_locate_symptom():
    """Find-the-bug across files. total() drops the tax."""
    files = _project()
    prompt = ("The invoice total is wrong. When I run the report the Total line "
              "comes out the same as the Subtotal line, but the total is supposed "
              "to include the tax as well. Please find the cause and fix it.")

    def check(w):
        rc, out = _run_cli(w, "total", "data.json")
        # subtotal 125.0, tax 10.0, expected total 135.0
        return (rc == 0 and out.strip() == "135.00", f"total={out.strip()[:40]!r}")
    return files, prompt, check


def _case_add_json_flag():
    """Coordinated two-file feature: new option in cli.py + new renderer."""
    files = _project()
    prompt = ("Add a json option to the report command so that it can print the "
              "invoice as JSON instead of the text table. The JSON should have a "
              "number key, a customer key, and a total key. The plain text report "
              "must keep working exactly as it does today when the option is not "
              "given.")

    def check(w):
        rc, out = _run_cli(w, "report", "data.json", "--json")
        if rc != 0:
            return (False, f"rc={rc} {out.strip()[:40]!r}")
        try:
            payload = json.loads(out.strip())
        except Exception:
            return (False, f"not json: {out.strip()[:40]!r}")
        ok = all(k in payload for k in ("number", "customer", "total"))
        # the text path must not have regressed
        rc2, out2 = _run_cli(w, "report", "data.json")
        ok = ok and rc2 == 0 and "Subtotal:" in out2
        return (ok, f"keys={sorted(payload)[:4]} text_ok={rc2 == 0}")
    return files, prompt, check


def _case_keep_tests_green():
    """Change a dataclass + keep an existing test file passing."""
    files = _project()
    prompt = ("Line items need to support a per item discount. Give the line item "
              "a discount field that is a number between zero and one, defaulting "
              "to zero, and make the line total apply it as a fractional discount. "
              "The existing tests in the tests directory must still pass.")

    def check(w):
        rc, out = _run_py(w, "from invoice.model import LineItem;"
                             "i=LineItem('w',2,10.0);"
                             "j=LineItem('w',2,10.0);j.discount=0.5;"
                             "print(i.line_total(), j.line_total())")
        if rc != 0:
            return (False, f"import/rc fail: {out.strip()[:40]!r}")
        if out.strip() != "20.0 10.0":
            return (False, f"line_total={out.strip()[:30]!r}")
        p = subprocess.run([PY, "-m", "pytest", "-q", "tests"], cwd=w,
                           capture_output=True, text=True, timeout=90)
        return (p.returncode == 0, f"discount ok; pytest rc={p.returncode}")
    return files, prompt, check


def _case_rename_across():
    """A rename that must land in two files at once."""
    files = _project()
    prompt = ("The function that builds the text report is called format_report. "
              "Rename it to render_report and update every place that calls it so "
              "the command line tool still works.")

    def check(w):
        rc, out = _run_cli(w, "report", "data.json")
        if rc != 0 or "Subtotal:" not in out:
            return (False, f"cli broken rc={rc} {out.strip()[:30]!r}")
        stale = []
        for rel in ("cli.py", "invoice/report.py"):
            text = (w / rel).read_text()
            if "format_report" in text:
                stale.append(rel)
            if rel == "invoice/report.py" and "def render_report" not in text:
                return (False, "render_report not defined")
        return (not stale, f"stale={stale or 'none'}")
    return files, prompt, check


# --- the compaction-regime case ----------------------------------------------
# Every other case in every battery finishes well under the 75,000-char
# auto-compact threshold, so the compaction path -- the product's worst
# reported failure mode -- had ZERO eval coverage. This case exists to put a
# run *through* compaction and then require correct work on the far side.
#
# Forcing function: the target is identified by a BEHAVIOUR, not by a name, so
# no single grep locates it -- the model has to actually read the modules. Six
# ~15k-char files is ~88k chars of reads, well past the threshold, and the one
# edit that scores the case happens on the far side of the compaction.

_HANDLER = """

def {name}(request, context=None):
    \"\"\"Handle a {verb} request for the {module} subsystem.

    The caller passes the decoded request body and an optional context
    mapping. Unknown keys in the body are ignored; missing keys fall back
    to the subsystem defaults recorded in the deployment manifest.
    \"\"\"
    payload = dict(request or {{}})
    context = dict(context or {{}})
    payload.setdefault("subsystem", "{module}")
    payload.setdefault("action", "{verb}")
    if context.get("dry_run"):
        return {{"status": "skipped", "reason": "dry run", "echo": payload}}
    retries = int(context.get("retries", {idx} % 3))
    for _ in range(max(retries, 1)):
        if payload.get("ready"):
            break
    return {{"status": "ok", "handled_by": "{claims}", "payload": payload}}
"""

_VERBS = ["create", "update", "delete", "list", "inspect", "drain", "resume"]

_NOTES_MODULES = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
_BROKEN_MODULE = "foxtrot"
_BROKEN_IDX = 12


def _handler_name(module: str, i: int) -> str:
    return f"{module}_{i:02d}_handler"


def _notes_module(module: str, count: int, broken_at: int | None = None) -> str:
    """One plausible ~15k-char handler module.

    Every handler reports its own name back in `handled_by` -- except the one
    at `broken_at`, which reports its neighbour's. That mismatch is invisible
    to grep (both strings occur all over the tree) and only shows up if you
    read the def and its return together.
    """
    out = [f'"""{module} — request handlers for the {module} subsystem.\n\n'
           f'Generated notes module. Every handler takes a decoded request\n'
           f'body and an optional context mapping, and reports its own name\n'
           f'back to the caller in the handled_by field of its result.\n'
           f'"""\n']
    for i in range(count):
        name = _handler_name(module, i)
        claims = _handler_name(module, i - 1) if i == broken_at else name
        out.append(_HANDLER.format(name=name, claims=claims, module=module,
                                   verb=_VERBS[i % len(_VERBS)], idx=i))
    return "".join(out)


def _case_long_context_find():
    """Cross the auto-compact threshold, then still land the right one-line edit."""
    files = {"notes/__init__.py": ""}
    for name in _NOTES_MODULES:
        broken = _BROKEN_IDX if name == _BROKEN_MODULE else None
        files[f"notes/{name}.py"] = _notes_module(name, 18, broken_at=broken)
    prompt = ("Every handler function in the notes directory is supposed to "
              "report its own name back to the caller in the handled_by field "
              "of the dictionary it returns. Exactly one handler in there "
              "reports the wrong name. Find that one handler and add a comment "
              "line reading FIXME unreviewed directly above its def line. The "
              "comment must start with a hash character. Do not change "
              "anything else anywhere in the notes directory, and in "
              "particular do not correct the wrong name itself.")

    target_def = f"def {_handler_name(_BROKEN_MODULE, _BROKEN_IDX)}("

    def check(w):
        lines = (w / "notes" / f"{_BROKEN_MODULE}.py").read_text().splitlines()
        placed = False
        for i, ln in enumerate(lines):
            if not ln.startswith(target_def):
                continue
            # "Directly above" generously: skip blank lines on the way up, and
            # don't care about the comment's indentation. A blank line between
            # the comment and the def is not a wrong answer.
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            prev = lines[j].strip() if j >= 0 else ""
            placed = prev.startswith("#") and "FIXME" in prev
            break
        strays = []
        for name in _NOTES_MODULES:
            n = (w / "notes" / f"{name}.py").read_text().count("FIXME")
            if n > (1 if name == _BROKEN_MODULE else 0):
                strays.append(f"{name}x{n}")
        return (placed and not strays, f"placed={placed} strays={strays or 'none'}")

    # bash is deliberately absent: shell one-liners would collapse the search
    # into a single call and never build the history this case is measuring.
    tools = "edit_file,write_file,append_file,replace_lines,read_file,glob,grep,ls"
    # Budget trimmed so compaction fires with margin instead of only just: at
    # the 100k default this fixture compacts exactly once, right at the end,
    # which would stop being true if the fixture or the default ever drifts.
    # Not trimmed further -- at 40k it compacted after every second read, and
    # qythos9 responded by inventing notes/golf.py ... notes/tango.py and
    # burning nine iterations on files that never existed. That is a real
    # finding (see the consecutive-error gap in the notes below) but it is a
    # harsher regime than production, and this case is meant to measure
    # production. _problem() asserts the run actually compacted.
    env = {"LOCODE_MAX_HISTORY_CHARS": "70000"}
    return files, prompt, check, tools, env


_SYNC_PY = '''#!/usr/bin/env python3
"""Sync assets from the vendored upstream tree into ./local.

Reports every file that is new or changed upstream. Run with no arguments.
"""
import os
from pathlib import Path

SOURCE_ROOT = Path("./upstream")
SOURCE_PATH = "shared/vendor/widgets"
LOCAL_DIR = Path("./local")


def scan(root):
    found = {}
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            found[str(p.relative_to(root))] = p.read_text()
    return found


def main():
    source = scan(SOURCE_ROOT / SOURCE_PATH)
    local = scan(LOCAL_DIR)
    changed = [n for n, body in source.items()
               if n not in local or local[n] != body]
    if not changed:
        print("no differences")
        return
    for n in sorted(changed):
        print("differs:", n)


if __name__ == "__main__":
    main()
'''


def _case_empty_query_diagnosis():
    """Diagnose a bug whose only evidence is that queries come back EMPTY.

    Modelled on a real qythos9 failure. SOURCE_PATH names a directory that
    exists but holds nothing -- the upstream files actually live one level up.
    So the script walks an empty tree, finds no files, and cheerfully prints
    "no differences". Nothing errors anywhere: the script exits 0, and every
    ls/glob/grep aimed at the wrong path succeeds and returns nothing. Every
    other guard in the loop keys on failure, so this shape used to run until
    the repeat guard ended the turn.
    """
    files = {
        "sync.py": _SYNC_PY,
        # SOURCE_PATH points HERE: the directory exists and is empty. That is
        # the load-bearing detail. A merely-missing path makes ls/glob/grep
        # ERROR, which is a loud signal every existing guard can already see;
        # an EMPTY one makes them all succeed and return nothing, which is the
        # failure mode under test. os.walk yields nothing either way, so the
        # script exits 0 and prints "no differences" regardless.
        "upstream/shared/vendor/widgets/": "",
        # The real upstream content, one level ABOVE where SOURCE_PATH looks.
        "upstream/shared/widgets/button.txt": "button v2\n",
        "upstream/shared/widgets/slider.txt": "slider v2\n",
        "local/button.txt": "button v1\n",
        "local/slider.txt": "slider v1\n",
    }
    prompt = ("The sync.py script says there are no differences, but the "
              "upstream widgets really have changed and it should be "
              "reporting them. Run it, work out why it finds nothing, and fix "
              "it so it reports the changed files.")

    def check(w):
        import subprocess
        r = subprocess.run(["python3", "sync.py"], cwd=w, capture_output=True,
                           text=True, timeout=60)
        out = (r.stdout or "").strip()
        names = {ln.split(":", 1)[1].strip() for ln in out.splitlines()
                 if ln.startswith("differs:")}
        ok = names == {"button.txt", "slider.txt"}
        return (ok, f"rc={r.returncode} out={out[:60]!r}")

    return files, prompt, check


CASES = {
    "empty-query-diagnosis": _case_empty_query_diagnosis,
    "locate-symptom": _case_locate_symptom,
    "add-json-flag": _case_add_json_flag,
    "keep-tests-green": _case_keep_tests_green,
    "rename-across": _case_rename_across,
    "long-context-find": _case_long_context_find,
}

_DEFAULT_TOOLS = ("edit_file,write_file,append_file,replace_lines,"
                  "read_file,bash,glob,grep")


def run_one(case: str, model: str, rep: int, outdir: Path, *,
            max_iter: int, max_wall: int) -> dict:
    # A case returns (files, prompt, check) plus, optionally, its own tool
    # allowance and env overrides (the compaction case needs both).
    spec = CASES[case]()
    files, prompt, check = spec[0], spec[1], spec[2]
    tools = spec[3] if len(spec) > 3 else _DEFAULT_TOOLS
    extra_env = spec[4] if len(spec) > 4 else {}
    rundir = outdir / f"{case}__{model}__r{rep}"
    work = rundir / "workdir"
    work.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        dest = work / name
        # A trailing slash means "create this as an EMPTY DIRECTORY". Needed
        # because an empty dir is not expressible as a file, and it is the whole
        # point of empty-query-diagnosis: a path that EXISTS and holds nothing
        # makes ls/glob/grep return empty, where a path that is merely missing
        # makes them error — a completely different signal to the model.
        if name.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    log = rundir / "events.jsonl"
    transcript = rundir / "transcript.txt"
    cmd = [LOCODE, "-p", prompt, "-m", model, "--no-splash", "--no-markdown",
           "--show-events", "--allow-tool", tools,
           "--max-iterations", str(max_iter), "--max-wallclock", str(max_wall),
           "--log-events", str(log)]
    env = {**os.environ, **extra_env}
    t0 = time.monotonic()
    with open(transcript, "w") as tf:
        try:
            subprocess.run(cmd, cwd=work, stdout=tf, stderr=subprocess.STDOUT,
                           env=env, timeout=max_wall + 60)
        except subprocess.TimeoutExpired:
            tf.write("\n[REAL-BATTERY: hard timeout]\n")
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
    # A compaction case that never compacted didn't test what it claims to --
    # flag it so a drifting fixture or a raised default can't quietly turn this
    # into just another find-the-symptom case.
    if row["case"] == "long-context-find" and not s.get("compactions"):
        return True
    return (not row["done"] or s["repeats"] or s["noops"] >= 2
            or (s["stop_reason"] and "repeat" in (s["stop_reason"] or "").lower()))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qythos9")
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--max-iter", type=int, default=24)
    ap.add_argument("--max-wall", type=int, default=300)
    a = ap.parse_args(argv)

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
                print(f"{flag:>7} | {case:<16} {model:<13} | "
                      f"done={'Y' if row['done'] else 'N'} | "
                      f"{s['iterations']:>2}it {row['wall']:>4.0f}s | "
                      f"f{s['fails']} n{s['noops']} r{s['repeats']} "
                      f"c{s.get('compactions', 0)} "
                      f"{'green' if s['saw_green'] else '     '} | "
                      f"{(s['stop_reason'] or 'answered')[:30]:<30} | {row['detail'][:44]}",
                      flush=True)

    probs = [r for r in rows if _problem(r)]
    print(f"\n=== {len(probs)}/{len(rows)} problem rows ===")
    for r in probs:
        print(f"  {r['case']}__{r['model']}__r{r['rep']}  -> replay: "
              f"python evals/replay.py {r['rundir']}/events.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
