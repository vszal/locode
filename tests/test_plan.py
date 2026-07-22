import pytest

from locode.agent.plan import DOING, DONE, MAX_TASKS, TODO, Plan
from locode.tools.base import ToolContext
from locode.tools.plan import UpdatePlan


def make_ctx(plan=None):
    return ToolContext(cwd="/tmp", plan=plan if plan is not None else Plan())


# --- marker parsing ----------------------------------------------------------
# Deliberately forgiving: the models that need a plan most are the ones whose
# formatting is least reliable, and a dropped status is worse than a guess.

@pytest.mark.parametrize("line,status,text", [
    ("[x] read the spec", DONE, "read the spec"),
    ("[X] read the spec", DONE, "read the spec"),
    ("[done] read the spec", DONE, "read the spec"),
    ("[✓] read the spec", DONE, "read the spec"),
    ("[>] write DESIGN.md", DOING, "write DESIGN.md"),
    ("[~] write DESIGN.md", DOING, "write DESIGN.md"),
    ("[in progress] write DESIGN.md", DOING, "write DESIGN.md"),
    ("[ ] write PLAN.md", TODO, "write PLAN.md"),
    ("[] write PLAN.md", TODO, "write PLAN.md"),
    ("[todo] write PLAN.md", TODO, "write PLAN.md"),
])
def test_status_markers(line, status, text):
    p = Plan()
    p.replace([line])
    assert (p.tasks[0].status, p.tasks[0].text) == (status, text)


def test_unmarked_task_is_todo():
    p = Plan()
    p.replace(["just do the thing"])
    assert p.tasks[0].status == TODO
    assert p.tasks[0].text == "just do the thing"


def test_unrecognized_bracket_is_kept_as_text():
    # "[api] add the endpoint" is a label, not a status. Eating the bracket
    # would silently rewrite the model's own task description.
    p = Plan()
    p.replace(["[api] add the endpoint"])
    assert p.tasks[0].status == TODO
    assert p.tasks[0].text == "[api] add the endpoint"


def test_blank_entries_are_dropped():
    p = Plan()
    p.replace(["[x] a", "", "   ", "[ ] b"])
    assert len(p.tasks) == 2


def test_task_count_is_capped():
    p = Plan()
    p.replace([f"[ ] task {i}" for i in range(MAX_TASKS + 25)])
    assert len(p.tasks) == MAX_TASKS


# --- queries -----------------------------------------------------------------

def test_current_prefers_the_doing_task():
    p = Plan()
    p.replace(["[x] a", "[ ] b", "[>] c", "[ ] d"])
    assert p.current.text == "c"


def test_current_falls_back_to_first_open():
    p = Plan()
    p.replace(["[x] a", "[ ] b", "[ ] c"])
    assert p.current.text == "b"


def test_complete_only_when_every_task_is_done():
    p = Plan()
    p.replace(["[x] a", "[ ] b"])
    assert not p.complete
    p.replace(["[x] a", "[x] b"])
    assert p.complete


def test_empty_plan_is_not_complete():
    # Otherwise "no plan" would read as "everything is finished".
    assert not Plan().complete


def test_signature_tracks_status_not_wording():
    """A model must not be able to keep a stall detector quiet by rephrasing
    its own tasks — only status changes count as progress."""
    p = Plan()
    p.replace(["[ ] write the parser"])
    before = p.signature()
    p.replace(["[ ] write the tokenizer instead"])
    assert p.signature() == before
    p.replace(["[x] write the tokenizer instead"])
    assert p.signature() != before


def test_replace_is_wholesale():
    p = Plan()
    p.replace(["[x] a", "[x] b", "[ ] c"])
    p.replace(["[ ] z"])
    assert [t.text for t in p.tasks] == ["z"]
    assert p.revisions == 2


# --- the tool ----------------------------------------------------------------

async def test_tool_records_the_plan_and_names_the_next_task():
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": ["[x] read SPEC.md", "[>] write DESIGN.md", "[ ] write PLAN.md"]},
        make_ctx(plan))
    assert res.ok
    assert plan.summary() == "1/3 done"
    assert "Next: write DESIGN.md" in res.content
    assert "do not reply with the plan" in res.content


async def test_tool_says_so_when_everything_is_done():
    res = await UpdatePlan().run({"tasks": ["[x] a", "[x] b"]}, make_ctx())
    assert "All tasks are done" in res.content


async def test_tool_calls_out_a_plan_rewritten_with_no_progress():
    # Rewriting the plan instead of working on it is its own kind of stall.
    plan = Plan()
    ctx = make_ctx(plan)
    await UpdatePlan().run({"tasks": ["[ ] a", "[ ] b"]}, ctx)
    res = await UpdatePlan().run({"tasks": ["[ ] a rephrased", "[ ] b"]}, ctx)
    assert "stop revising the plan" in res.content


def _joined(*lines):
    return "\n".join(lines)


async def test_tool_accepts_a_newline_joined_string():
    # Recovering the common malformation costs three lines and saves a whole
    # iteration of nudging.
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": _joined("[x] a", "[ ] b")}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 2


async def test_tool_rejects_an_empty_list():
    res = await UpdatePlan().run({"tasks": []}, make_ctx())
    assert not res.ok


async def test_tool_errors_without_a_plan_in_context():
    res = await UpdatePlan().run({"tasks": ["[ ] a"]}, ToolContext(cwd="/tmp"))
    assert not res.ok
