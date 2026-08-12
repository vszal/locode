#!/usr/bin/env python3
"""Grade the plan-hijack A/B (ROADMAP 5.109).

    .venv/bin/python evals/grade_planhijack.py

WRITTEN AND COMMITTED BEFORE THE SWEEP PRODUCED A NUMBER.

The failure, reported from a live session and reproduced three times. Asked to
"fix the bug comparing directories in sync_gke_compute_classes.py", qythos9
reads the file, copies the five-step numbered list out of its module docstring
into `update_plan`, appends the user's actual request as item 6, and then works
item 1 -- cloning repositories -- until the repeat guard stops the turn. The
user sees "it just quits". The quit is the last symptom, not the defect.

Arm A is the shipped `update_plan` description. Arm B adds two sentences: that a
one-file bug fix is a SINGLE step and does not need a plan, and that the plan is
the model's own steps, never a summary of code it just read.

Disclosure, because it matters for how much this rule is worth: I have already
watched three arm-A runs and all three took the bait. What is NOT yet observed
is any arm-B count beyond a single pilot run, and no count from either arm at
n>1. The thresholds below are set against the pilot, not against the sweep.

Second disclosure: a first attempt at this sweep (`abplan/`, four runs) is VOID
and is not read by this script. Two copies of the runner were live at once, both
swapping `locode/tools/plan.py` in the working tree, so no run can be attributed
to an arm. See ROADMAP 5.109. The rerun freezes each arm as its own package copy
and selects it with PYTHONPATH; nothing mutates the live tree.

PRIMARY, fixed in advance: the share of runs that take the bait -- any
`update_plan` whose task list names two or more of the script's own workflow
steps. Two, not one, because "clean up" or "apply" could plausibly appear in an
honest plan; the whole docstring showing up could not.

  SHIP if arm B's bait rate is at least 50 points below arm A's AND B is not
  worse on either guardrail below. Anything less is not worth a prompt-length
  increase on a 9B model's most-read tool description.

GUARDRAILS (a lever that fixes planning by breaking work is not a fix):
  - runs landing at least one edit must not FALL by more than 1 run;
  - runs ending in a guard stop must not RISE by more than 1 run.

Rule 8 applies: endings must not fall. Rule 61 applies harder -- 6 v 6 is a
pilot, so a pass here licenses the eval case, not a claim about the model.
"""
import pathlib
import re
import sys
from math import comb

OUT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = pathlib.Path.home() / ".claude/jobs/1652a884/tmp/abplan2"

# The arms are stored under neutral names because the source path can reach the
# model through a traceback and rule 68 forbids the label on any channel it can
# read. src_q7 is the shipped description, src_m3 the candidate.
ARM_DIR = {"A": "q7", "B": "m3"}

# Distinctive steps from the script's own docstring. Matched case-insensitively
# against the text the model passed to update_plan.
BAIT = [r"clon\w*", r"stag\w*", r"_staged-diffs", r"clean\s*up", r"appl\w+\s+stag"]
BAIT_MIN = 2


def fisher_2x2(a, n1, b, n2):
    def p(x, y):
        return comb(n1, x) * comb(n2, y) / comb(n1 + n2, x + y)
    tot = a + b
    obs = p(a, b)
    return sum(p(x, tot - x) for x in range(0, n1 + 1)
               if 0 <= tot - x <= n2 and p(x, tot - x) <= obs + 1e-12)


def read_run(path):
    """(took_bait, landed_edit, guard_stopped, n_calls) for one transcript."""
    txt = path.read_text(errors="replace")
    calls = re.findall(r'\{"name":\s*"(\w+)"', txt)
    bait = False
    for m in re.finditer(r'"name":\s*"update_plan".*', txt):
        seg = m.group(0)[:2000]
        if sum(bool(re.search(p, seg, re.I)) for p in BAIT) >= BAIT_MIN:
            bait = True
    return (bait,
            "edit_file" in calls or "write_file" in calls,
            "⏹ stopped" in txt or "without making progress" in txt,
            len(calls))


def main():
    arms = {}
    for arm in ("A", "B"):
        runs = sorted(RESULTS.glob(f"r*-{ARM_DIR[arm]}.txt"))
        arms[arm] = [read_run(p) for p in runs]
    na, nb = len(arms["A"]), len(arms["B"])
    if not na or not nb:
        sys.exit(f"  VOID — no transcripts found under {RESULTS}")
    if na != nb:
        print(f"!! unequal arms ({na} vs {nb}); the sweep did not finish. "
              f"Reporting shares, not a verdict.\n")

    def share(arm, idx):
        return sum(r[idx] for r in arms[arm])

    print(f"{'':<32}{'A (shipped)':>14}{'B (candidate)':>15}")
    print("-" * 61)
    rows = [("took the bait [PRIMARY]", 0), ("landed an edit", 1),
            ("ended on a guard stop", 2)]
    for name, idx in rows:
        print(f"{name:<32}{f'{share(chr(65), idx)}/{na}':>14}"
              f"{f'{share(chr(66), idx)}/{nb}':>15}")
    print(f"{'mean tool calls':<32}"
          f"{sum(r[3] for r in arms['A'])/na:>14.1f}"
          f"{sum(r[3] for r in arms['B'])/nb:>15.1f}")

    ba, bb = share("A", 0), share("B", 0)
    drop = 100 * (ba / na - bb / nb)
    p = fisher_2x2(ba, na, bb, nb)
    print(f"\n=== decision (fixed in advance) ===")
    print(f"  bait-rate drop: {drop:+.1f}pp  (Fisher two-sided p={p:.4f})")

    ea, eb = share("A", 1), share("B", 1)
    sa, sb = share("A", 2), share("B", 2)
    guards_ok = (eb >= ea - 1) and (sb <= sa + 1)
    print(f"  guardrails: edits {ea}->{eb} (may fall by 1), "
          f"stops {sa}->{sb} (may rise by 1)  "
          f"{'✓' if guards_ok else '✗ VIOLATED'}")

    if drop >= 50 and guards_ok:
        print("\n  => SHIP the description change, and promote this into a "
              "standing eval case.\n     6v6 is a pilot (rule 61): this "
              "licenses the case, it does not\n     settle the size of the "
              "effect.")
    elif not guards_ok:
        print("\n  => NO SHIP. A guardrail moved the wrong way — the change "
              "fixed planning by\n     breaking the work, which is the trade "
              "rule 8 exists to refuse.")
    else:
        print(f"\n  => NO SHIP. The bait rate fell {drop:.1f}pp, short of the "
              "50pp bar.\n     Not worth lengthening the most-read tool "
              "description on a 9B model.")


if __name__ == "__main__":
    main()
