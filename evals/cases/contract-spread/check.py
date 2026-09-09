"""Checks for contract-spread.

The suite had no multi-file-consistency case. Every execute case before this
one is satisfiable by editing a single file, so a model that changes the thing
it was told to change and stops scores full marks. Here that model scores
0.250: flipping `store.get` to raise is one line, and the point of the case is
the three callers it breaks, each of which needs a *different* adaptation to
preserve behaviour it already has.

The grading problem this case has to solve is that "report still prints `key=?`"
is true of the untouched seed -- the callers cope with `None` today, so a check
that just calls them passes before the model does anything. Scoring that would
pay the seed (rule 90).

So the callers are graded against a store that RAISES, installed by the probe
on top of whatever the model left. That asks the only question worth asking --
would this caller survive the new contract? -- and it is false on the seed,
where every caller propagates the KeyError. It also keeps the four outcomes
independent: a model that fixes the callers but never touches the store still
earns those three, which is the credit it is due.

`present_keys_still_work` is the one thing here true of the untouched seed, so
it is a GUARD: it cannot be earned, and it vetoes the run if the model bought
its KeyError handling by breaking the ordinary path.
"""

import json
import shutil

PROBE_NAME = "_check_contract.py"
BACKUP_NAME = "_store_backup.py"

PROBE = '''\
import json, importlib, shutil, sys, os
sys.path.insert(0, ".")

out = {}

def probe(name, fn):
    try:
        out[name] = bool(fn())
    except Exception:
        out[name] = False

def fresh(name):
    """Import `name` with the CURRENT store.py on disk, never a cached one."""
    for mod in ("store", "report", "cli", "cache"):
        sys.modules.pop(mod, None)
    return importlib.import_module(name)

# --- against the model's own store ---------------------------------------
def _store_raises():
    store = fresh("store")
    try:
        store.get("nope")
    except KeyError:
        return True
    return False

def _present_ok():
    # The guard: the ordinary path must still work everywhere.
    store = fresh("store")
    if store.get("alpha") != 1:
        return False
    if fresh("report").line("beta") != "beta=2":
        return False
    if fresh("cli").run(["alpha", "gamma"]) != (2, ["alpha 1", "gamma 3"]):
        return False
    return fresh("cache").warm(["alpha", "beta"]) == {"alpha": 1, "beta": 2}

probe("store_raises_on_missing", _store_raises)
probe("present_keys_still_work", _present_ok)

# --- against a store that DEFINITELY raises ------------------------------
# Installed over whatever the model left, so each caller is asked the only
# question that matters and the seed's callers all fail it.
RAISING = (
    "_DATA = {'alpha': 1, 'beta': 2, 'gamma': 3}\\n"
    "def get(key):\\n"
    "    if key not in _DATA:\\n"
    "        raise KeyError(key)\\n"
    "    return _DATA[key]\\n"
    "def keys():\\n"
    "    return sorted(_DATA)\\n"
)
shutil.copyfile("store.py", "''' + BACKUP_NAME + '''")
try:
    open("store.py", "w").write(RAISING)

    def _report():
        return (fresh("report").line("nope") == "nope=?"
                and fresh("report").line("alpha") == "alpha=1")

    def _cli():
        return (fresh("cli").run(["alpha", "nope", "gamma"])
                == (2, ["alpha 1", "gamma 3"])
                and fresh("cli").run(["nope"]) == (0, []))

    def _cache():
        return (fresh("cache").warm(["alpha", "nope"]) == {"alpha": 1}
                and fresh("cache").warm(["nope"]) == {})

    probe("report_survives_raising_store", _report)
    probe("cli_survives_raising_store", _cli)
    probe("cache_survives_raising_store", _cache)
finally:
    shutil.copyfile("''' + BACKUP_NAME + '''", "store.py")
    os.remove("''' + BACKUP_NAME + '''")

print("PROBE" + json.dumps(out))
'''

OUTCOMES = ["store_raises_on_missing", "report_survives_raising_store",
            "cli_survives_raising_store", "cache_survives_raising_store"]

# Rule 90: true of the UNTOUCHED seed, so it cannot be earned. It vetoes a run
# that bought its KeyError handling by breaking the ordinary path.
GUARDS = {"present_keys_still_work"}
DERIVED = {"fully_fixed"}


def check(ctx):
    got = _probe(ctx)
    results = {name: bool(got.get(name)) for name in OUTCOMES}
    results["present_keys_still_work"] = bool(got.get("present_keys_still_work"))
    results["fully_fixed"] = all(results[name] for name in OUTCOMES)
    return results


def _probe(ctx):
    path = ctx.workdir / PROBE_NAME
    try:
        path.write_text(PROBE)
        proc = ctx.bash(f"python3 {PROBE_NAME} 2>&1", timeout=60)
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.startswith("PROBE"):
                try:
                    return json.loads(line[len("PROBE"):])
                except json.JSONDecodeError:
                    return {}
        return {}
    finally:
        path.unlink(missing_ok=True)
        (ctx.workdir / BACKUP_NAME).unlink(missing_ok=True)
        shutil.rmtree(ctx.workdir / "__pycache__", ignore_errors=True)
