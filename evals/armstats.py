#!/usr/bin/env python3
"""Per-arm clean-finish / iteration / nudge stats for an ab.py sweep.

Why this exists as a separate tool: **ab.py's paired test cannot see a
turn-ENDING effect.** Its delta is computed on the case SCORE, so when a change
alters how a turn is reported rather than what the model produces, both arms
score identically, every pair ties, and the sign-flip test correctly reports
INCONCLUSIVE — a true statement about the score and a useless one about the
change. Both times this bit (verifyok-msg, doneverify) the real effect was
sitting in the event logs at full strength: 0/10 vs 10/10 clean finishes behind
a "+0.000, p=1.0" summary.

So: when the hypothesis is about how a turn ends — clean finish vs repeat-stop,
nudge mix, iteration count — read it here, and treat ab.py's INCONCLUSIVE as
"the score didn't move", which is often the intended result.

**Exposure (`--exposure PATTERN`) is the fix for reading n=5 sweeps.** A run
that never reached the code you changed cannot be evidence about it, either
way. Intent-to-treat over 5 runs per arm is mostly sampling noise when only
some runs touch the changed path, so a mixed table invites a wrong conclusion
in *both* directions — crediting a win the change didn't cause, and chasing a
regression it couldn't have caused. Pass a substring (or `re:` regex) that
appears in the tool output of an exposed run and each row also reports how many
of its runs actually fired it.

**Match the TRIGGERING CONDITION, not your new wording.** Text that only the
candidate emits reports the baseline at 0/n exposure and gives you nothing to
compare against. Pick a string both arms produce when they reach the code —
for b90 that is "not found in" (the failure itself), not "ACTUALLY contains"
(the new message). Done right, both arms show the same exposure and
`clean-among-exposed` is the apples-to-apples number.

Worked example (b90-editwindow, the +/-12-line edit window): the raw table read
as mixed — exec-bugfix clean-finish 1/5 -> 4/5 but exec-stall-trap 3/5 -> 1/5.
Exposure settled it. exec-bugfix fired the new path in 5/5 candidate runs;
exec-stall-trap fired it in 1/5, and *none* of its four regressed runs had ever
hit a not-found edit (two never called edit_file at all). The regression was
divergence, not causation, and e2e-spec-to-code was a clean negative control at
0/5 exposure and 0/5 -> 0/5 outcome.

Usage:  python evals/armstats.py <label>            # e.g. doneverify
        python evals/armstats.py <label> --by-case  # split per case
        python evals/armstats.py <label> --by-case --exposure "ACTUALLY contains"
        python evals/armstats.py <label> --exposure "re:not found in .*\\.py"
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

MUTATING = {"write_file", "append_file", "edit_file", "replace_lines",
            "bash", "move_file"}


def _blank() -> dict:
    return {"n": 0, "clean": 0, "done": 0, "gaveup": 0, "landed": 0,
            "verified": 0, "falsedone": 0, "unverifiable": 0,
            "iters": 0, "mut": 0, "exposed": 0, "exposed_clean": 0,
            "exposed_done": 0,
            "nudges": collections.Counter(), "stops": collections.Counter()}


EDITING = {"write_file", "append_file", "edit_file", "replace_lines",
           "move_file"}


def _landed_edits(ev: list[dict]) -> int:
    """Edits that actually SUCCEEDED — a non-error result for an editing call.

    Needed because a self-terminated run is not automatically a finished one:
    the model can reply "I cannot make progress" and end the turn, which
    produces no `stopped` event and used to score as a clean finish. Requiring
    at least one landed edit separates finishing from giving up. `bash` is
    excluded deliberately — running the tests is not progress on the task."""
    n, pending = 0, None
    for e in ev:
        if e.get("phase") == "run" and e.get("name") in EDITING:
            pending = True
        elif e.get("phase") == "result" and pending:
            n += not e.get("error")
            pending = None
    return n


_PASSED = re.compile(r"\b\d+ passed\b")
_FAILED = re.compile(r"\b\d+ (?:failed|error)")


def _ended_green(ev: list[dict]) -> bool | None:
    """Did the run's LAST test command report everything passing?

    `done` (self-terminated with a landed edit) is still too generous: b94's r7
    declared "All planned tasks are complete" with the suite at F....F..FF...
    and was counted a win. Same failure as `clean` counting surrender — one
    level down. Returns None when the run never ran a test, which is not a
    pass and not a fail, only unverifiable.
    """
    for e in reversed(ev):
        if e.get("phase") != "result" or e.get("name") != "bash":
            continue
        c = e.get("content") or ""
        if _PASSED.search(c) or _FAILED.search(c):
            return bool(_PASSED.search(c)) and not _FAILED.search(c)
    return None


def _matcher(pattern: str | None):
    """A predicate over one run's tool output. `re:` prefix means regex."""
    if not pattern:
        return None
    if pattern.startswith("re:"):
        rx = re.compile(pattern[3:])
        return lambda text: bool(rx.search(text))
    return lambda text: pattern in text


def collect(label: str, by_case: bool = False,
            exposure: str | None = None) -> dict:
    root = pathlib.Path("evals/results") / label / "events"
    if not root.is_dir():
        raise SystemExit(f"no events dir: {root}")
    out: dict = collections.defaultdict(_blank)
    hit = _matcher(exposure)
    for p in sorted(root.glob("*.jsonl")):
        arm = "cand" if "__cand" in p.name else "base"
        key = (p.name.split("__")[0], arm) if by_case else (arm,)
        ev = [json.loads(line) for line in open(p) if line.strip()]
        a = out[key]
        a["n"] += 1
        a["iters"] += sum(e.get("phase") == "iteration" for e in ev)
        a["mut"] += sum(e.get("phase") == "run" and e.get("name") in MUTATING
                        for e in ev)
        landed = _landed_edits(ev)
        a["landed"] += landed
        stops = [e.get("reason") for e in ev if e.get("phase") == "stopped"]
        clean = not stops
        # A run that self-terminates having changed NOTHING did not finish the
        # task, it surrendered — see _landed_edits. Counting those as clean is
        # how b90 first read as a 1/5 -> 4/5 win when every run left the tests
        # red. `done` is the honest numerator; `clean` is kept only so old
        # numbers stay comparable.
        done = clean and landed > 0
        a["clean"] += clean
        a["done"] += done
        a["gaveup"] += clean and not done
        green = _ended_green(ev)
        a["verified"] += done and green is True
        a["falsedone"] += done and green is False
        a["unverifiable"] += done and green is None
        if hit is not None:
            fired = any(e.get("phase") == "result" and hit(e.get("content") or "")
                        for e in ev)
            a["exposed"] += fired
            a["exposed_clean"] += fired and clean
            a["exposed_done"] += fired and done
        for s in stops:
            a["stops"][str(s)[:44]] += 1
        for e in ev:
            if e.get("phase") == "nudge":
                a["nudges"][e.get("reason")] += 1
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    label = sys.argv[1]
    by_case = "--by-case" in sys.argv
    exposure = None
    if "--exposure" in sys.argv:
        i = sys.argv.index("--exposure")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--exposure needs a pattern (substring, or re:REGEX)")
        exposure = sys.argv[i + 1]
    rows = collect(label, by_case, exposure)
    for key in sorted(rows):
        a = rows[key]
        if not a["n"]:
            continue
        name = " · ".join(key)
        print(f"{name}: n={a['n']}  VERIFIED={a['verified']}/{a['n']}"
              f" ({a['verified'] / a['n']:.0%})"
              f"  [false-done={a['falsedone']} untested={a['unverifiable']}]"
              f"  DONE={a['done']}/{a['n']}"
              f" ({a['done'] / a['n']:.0%})  gave-up={a['gaveup']}"
              f"  stopped={a['n'] - a['clean']}"
              f"  mean-iters={a['iters'] / a['n']:.1f}"
              f"  mean-landed-edits={a['landed'] / a['n']:.1f}")
        if exposure:
            ex = a["exposed"]
            # DONE among exposed runs — the only subset that can be evidence
            # about the change. "n/a" when nothing fired: a negative control,
            # not a zero.
            among = f"{a['exposed_done']}/{ex}" if ex else "n/a"
            print(f"     exposed={ex}/{a['n']}  done-among-exposed={among}")
        if a["nudges"]:
            print(f"     nudges={dict(a['nudges'])}")
        if a["stops"]:
            print(f"     stops ={dict(a['stops'])}")


if __name__ == "__main__":
    main()
