"""Regression tests for eval case checkers.

A checker that grades formatting instead of the requirement silently caps a
case's score and hides real movement. `plan_has_tasks` did exactly that: it
accepted only `1. ` and `- [ ]` task lines, so eleven of twelve b142 e2e runs
failed it while writing plans that answered the prompt in full. See ROADMAP
§5.131.
"""
import importlib.util
import sys
from pathlib import Path

CHECK = (Path(__file__).resolve().parent.parent
         / "evals" / "cases" / "e2e-spec-to-code" / "check.py")


def _plan_has_tasks(plan: str) -> bool:
    """Run the case checker's plan-task rule against `plan` alone."""
    spec = importlib.util.spec_from_file_location("e2e_check", CHECK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_check"] = mod
    spec.loader.exec_module(mod)

    class _Ctx:
        def read(self, name):
            return plan if name == "PLAN.md" else ""

        def bash(self, *a, **k):
            raise AssertionError("plan checks must not shell out")

    import re
    src = CHECK.read_text()
    body = re.search(
        r'results\["plan_has_tasks"\] = (len\(re\.findall\(.*?\)\) >= 6)',
        src, re.S).group(1)
    return eval(body, {"re": re, "plan": plan})  # noqa: S307 - test-only


_NUMBERED_LIST = "\n".join(f"{i}. do the thing" for i in range(1, 8))
_CHECKBOXES = "\n".join(f"- [ ] {i}) do the thing" for i in range(1, 8))
_HEADINGS = "\n".join(
    f"### Task {i//3 + 1}.{i} - Create envcfg.py" for i in range(1, 8))
_TABLE = "\n".join(f"| 1.{i} | do the thing | envcfg.py | pytest |"
                   for i in range(1, 8))


def test_a_plain_numbered_list_counts():
    assert _plan_has_tasks(_NUMBERED_LIST)


def test_checkbox_tasks_count():
    assert _plan_has_tasks(_CHECKBOXES)


def test_heading_style_tasks_count():
    # The style qythos9 actually writes; it used to score zero.
    assert _plan_has_tasks(_HEADINGS)


def test_table_row_tasks_count():
    assert _plan_has_tasks(_TABLE)


def test_prose_without_numbered_tasks_still_fails():
    plan = ("# Plan\n\n## Milestone 1\n\nWe will write the module and then "
            "test it thoroughly, taking care over the coercion rules.\n")
    assert not _plan_has_tasks(plan)


def test_too_few_tasks_still_fails():
    assert not _plan_has_tasks("\n".join(f"{i}. task" for i in range(1, 5)))
