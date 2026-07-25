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

async def test_tool_rejects_a_truncated_json_array():
    """The turn-killer from the r4-clean sweep: a model sent the fragment
    `["[>] Write DESIGN.md — the approach` and the tool adopted it as one task.
    With no status marker it parsed as open, could never be marked done, and the
    loop's completion gate then refused every final answer for the rest of the
    turn — the run produced nothing and scored 0.00. Reject, don't adopt."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": '["[>] Write DESIGN.md — the approach'}, make_ctx(plan))
    assert not res.ok
    assert "cut off" in res.content
    assert plan.tasks == []


async def test_tool_accepts_a_well_formed_json_array_string():
    """The lenient path still has to work for a model that JSON-encodes the
    argument correctly but sends it as a string."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": '["[x] a", "[ ] b"]'}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 2


async def test_a_plain_task_starting_with_a_bracket_still_splits_by_line():
    """`[ ] a` opens with a bracket but is not JSON — it must not be dragged
    into the JSON path and rejected."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": _joined("[ ] a", "[ ] b")}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 2


async def test_tool_unwraps_a_double_wrapped_dict_argument():
    """The r15 qythos9 exec-bugfix stall: the model sent the whole call shape
    nested inside the argument — {"tasks": {"tasks": [...]}}. The old code
    hard-rejected the dict, the model resent the identical shape, and the run
    stall-died AFTER already solving the task. Unwrap the single-key wrapper."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": {"tasks": ["[x] a", "[ ] b"]}}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 2


async def test_tool_unwraps_a_double_wrapped_dict_with_a_string_inside():
    """Same double-wrap, but the inner value is a single task string, not an
    array (also seen in the r15 run). Recover it as a one-task plan."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": {"tasks": "[x] run tests to see failures"}}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 1
    assert plan.tasks[0].status == DONE


async def test_tool_rejects_a_truncated_json_object_string():
    """The other half of the r15 stall: the model sent the inner fragment of a
    double-wrap as a string — `{"tasks": "[ ] run tests to see failures"` — which
    does not close. The old code fell through to the newline split and adopted
    the raw JSON as ONE bogus task, poisoning the completion gate. Reject it with
    the real shape instead of silently accepting garbage."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": '{"tasks": "[ ] run tests to see failures"'}, make_ctx(plan))
    assert not res.ok
    assert "array" in res.content
    assert plan.tasks == []


async def test_tool_recovers_a_well_formed_json_object_string():
    """A model that JSON-encodes the double-wrap correctly as a string —
    '{"tasks": ["[x] a", "[ ] b"]}' — should have the inner array pulled out and
    adopted, not rejected."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": '{"tasks": ["[x] a", "[ ] b"]}'}, make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 2


async def test_tool_recovers_a_task_to_status_dict():
    """The r16 qythos9 shape: the model sent a dict mapping marked task text to a
    status word — {"[ ] Run tests": "done", "[>] Fix wrap": "in progress"}. The
    key marker and the value disagree; the value is the live intent and must win,
    or the task stays open forever and the completion gate never lets the turn
    finish. Recover it with the value's status applied to the key's text."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": {
            "[ ] Run tests to see failures": "done",
            "[>] Fix word_wrap exact fit edge case": "in progress",
            "[ ] Fix truncate exact limit edge case": "pending",
        }},
        make_ctx(plan))
    assert res.ok
    assert len(plan.tasks) == 3
    assert plan.tasks[0].status == DONE      # value "done" beat the "[ ]" key
    assert plan.tasks[1].status == DOING     # value "in progress"
    assert plan.tasks[2].status == TODO
    assert plan.tasks[0].text == "Run tests to see failures"


async def test_tool_does_not_mistake_an_ordinary_object_for_a_plan():
    """The {task: status} recovery is gated on every key looking like a task
    line. A dict whose keys are NOT marked task lines must not be silently
    adopted — it falls through to the hard rejection."""
    plan = Plan()
    res = await UpdatePlan().run(
        {"tasks": {"first": "do a thing", "second": "do another"}},
        make_ctx(plan))
    assert not res.ok
    assert plan.tasks == []


def test_has_status_marker_accepts_recognized_markers():
    from locode.agent.plan import has_status_marker
    assert has_status_marker("[x] done thing")
    assert has_status_marker("[>] doing thing")
    assert has_status_marker("[ ] todo thing")
    assert has_status_marker("[wip] thing")


def test_has_status_marker_rejects_a_mangled_json_array():
    """The regex alone matches this, with a marker group of `\"[>` — which is
    precisely how the truncated fragment got adopted as a task."""
    from locode.agent.plan import has_status_marker
    assert not has_status_marker('["[>] Write DESIGN.md — the approach')
    assert not has_status_marker("no marker at all")
    assert not has_status_marker("[label] probably not a status")
