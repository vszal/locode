"""The model's own task list for a turn.

Two problems this exists to solve, both observed in eval runs:

1. **No decomposition.** Asked for "a design doc, then a plan, then the code",
   a local model does the first thing it thinks of and then declares victory.
   Nothing in the loop knows the request had three parts.
2. **No progress signal.** The loop can only measure *effort* — iterations
   burned, wallclock spent, calls repeated. It cannot tell "20 iterations of
   steady progress" from "20 iterations going nowhere", so every guard is a
   blunt budget cap and every nudge is generic.

A plan the model writes and maintains gives both: the act of writing it forces
decomposition, and the completed-count gives the loop something to compare
against effort. It is deliberately not a scheduler — nothing here executes,
reorders, or validates the work. It just remembers what the model said it was
going to do, so the loop can hold it to that.

The encoding is a flat list of strings with a leading status marker
(`[x] read the spec`). Flat strings — not nested objects — because the models
that need this most are the ones whose tool-call JSON breaks first, and a list
of plain strings is the least they can get wrong. Marker parsing is
correspondingly forgiving: see `_MARKERS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TODO = "todo"
DOING = "doing"
DONE = "done"

# Leading marker -> status. Models are inconsistent about which convention they
# reach for, and rejecting an unfamiliar one would silently drop the task's
# state, so accept every spelling seen in practice and treat anything
# unrecognized as "not started".
_MARKERS = {
    "x": DONE, "X": DONE, "done": DONE, "v": DONE, "✓": DONE, "✔": DONE,
    ">": DOING, "doing": DOING, "~": DOING, "-": DOING, "*": DOING,
    "in progress": DOING, "wip": DOING,
    "": TODO, " ": TODO, "todo": TODO, "pending": TODO, "o": TODO,
}

_MARKER_RE = re.compile(r"^\s*\[([^\]]{0,12})\]\s*(.*)$", re.DOTALL)

MAX_TASKS = 40
MAX_TASK_CHARS = 200


@dataclass
class Task:
    text: str
    status: str = TODO

    @property
    def marker(self) -> str:
        return {DONE: "x", DOING: ">"}.get(self.status, " ")

    def render(self) -> str:
        return f"[{self.marker}] {self.text}"


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)
    # How many times the plan has been written. A model that keeps rewriting the
    # plan without ever completing anything is stalling in a new and creative
    # way, and the loop needs to be able to see that.
    revisions: int = 0

    def __bool__(self) -> bool:
        return bool(self.tasks)

    # --- state ------------------------------------------------------------
    def replace(self, raw_tasks) -> None:
        """Adopt a new task list, wholesale.

        Whole-list replacement rather than per-task mutation: it needs no stable
        IDs, so there is no way for the model to address a task that has moved
        or never existed, and no partially-applied update to reason about.
        """
        tasks: list[Task] = []
        for raw in raw_tasks:
            text = str(raw).strip()
            if not text:
                continue
            tasks.append(_parse_task(text))
            if len(tasks) >= MAX_TASKS:
                break
        self.tasks = tasks
        self.revisions += 1

    def clear(self) -> None:
        self.tasks = []
        self.revisions = 0

    # --- queries ----------------------------------------------------------
    @property
    def done(self) -> list[Task]:
        return [t for t in self.tasks if t.status == DONE]

    @property
    def open(self) -> list[Task]:
        return [t for t in self.tasks if t.status != DONE]

    @property
    def current(self) -> Task | None:
        """The task the model says it is on: the first `doing`, else the first
        not-yet-done one."""
        for t in self.tasks:
            if t.status == DOING:
                return t
        return self.open[0] if self.open else None

    @property
    def complete(self) -> bool:
        return bool(self.tasks) and not self.open

    def signature(self) -> tuple:
        """What "progress happened" means for a plan.

        Only the *statuses* count. Re-wording a task, or appending more work, is
        not progress — otherwise a model could keep a stall detector quiet
        forever by editing its own plan.
        """
        return tuple(t.status for t in self.tasks)

    # --- rendering --------------------------------------------------------
    def render(self) -> str:
        return "\n".join(t.render() for t in self.tasks)

    def summary(self) -> str:
        """One line for logs and nudges."""
        if not self.tasks:
            return "no plan"
        return f"{len(self.done)}/{len(self.tasks)} done"


def has_status_marker(text: str) -> bool:
    """True when `text` opens with a status marker this module RECOGNIZES.

    Distinct from "matches the marker regex", which is deliberately permissive:
    `["[>] Write DESIGN.md` matches it too, with a marker group of `"[>`. That
    permissiveness is right for parsing a task (keep the model's words rather
    than eat them) and wrong for deciding whether a string is a task list at all
    — which is what the update_plan tool needs in order to tell a newline-joined
    list from a mangled JSON array."""
    m = _MARKER_RE.match(text)
    if not m:
        return False
    return m.group(1).strip().lower() in _MARKERS


def _parse_task(text: str) -> Task:
    m = _MARKER_RE.match(text)
    if not m:
        return Task(text=text[:MAX_TASK_CHARS], status=TODO)
    marker, rest = m.group(1), m.group(2).strip()
    status = _MARKERS.get(marker.strip().lower(), TODO)
    # A bare "[foo] do the thing" was probably a label, not a status marker —
    # keep the whole line rather than eating the model's own words.
    if marker.strip() and marker.strip().lower() not in _MARKERS:
        return Task(text=text[:MAX_TASK_CHARS], status=TODO)
    return Task(text=(rest or text)[:MAX_TASK_CHARS], status=status)
