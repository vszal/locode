"""Checks for the plan-doc case.

A plan is more mechanically checkable than a design: it has countable structure
(milestones, numbered tasks) and each task is supposed to name a file and a
done-condition. We verify the shape and the coverage; a human judges whether
the ordering is actually sound.
"""

import re

# Concepts from DESIGN.md that any real plan must schedule work for. Missing one
# means the plan skipped a whole chunk of the design.
COVERAGE = {
    "plans_schema_work": [r"schema", r"CREATE TABLE", r"migrat", r"\btable\b"],
    "plans_claim_work": [r"claim", r"BEGIN IMMEDIATE", r"atomic"],
    "plans_retry_work": [r"backoff", r"retry", r"jitter"],
    "plans_reaper_work": [r"reaper", r"expir", r"visibility", r"stale",
                          r"recover"],
    "plans_dead_letter_work": [r"dead[- ]?letter", r"\bDLQ\b", r"\bdead\b"],
    "plans_idempotency_work": [r"idempoten", r"\bkey\b.*uniq", r"UNIQUE",
                               r"dedup"],
    "plans_test_work": [r"\btests?\b", r"pytest", r"test_"],
}


def check(ctx):
    doc = ctx.read("PLAN.md")
    results = {"wrote_plan_doc": bool(doc.strip())}
    if not doc.strip():
        results.update({k: False for k in COVERAGE})
        results.update({"has_milestones": False, "enough_tasks": False,
                        "tasks_name_files": False,
                        "tasks_have_done_criteria": False,
                        "stayed_in_plan_mode": _wrote_no_code(ctx)})
        return results

    # Milestones: a heading or bolded label containing the word, or an M1/M2
    # style tag. Require at least 3 distinct ones.
    milestones = set(re.findall(r"(?im)^[#\s*_]*(?:milestone\s*\d+|M\d+)\b", doc))
    if len(milestones) < 3:
        milestones = set(re.findall(r"(?im)^#{1,4}\s*.*milestone.*$", doc))
    results["has_milestones"] = len(milestones) >= 3

    # Numbered or checkbox tasks anywhere in the document.
    tasks = re.findall(r"(?m)^\s*(?:\d+[.)]\s+|[-*]\s*\[[ x]\]\s*)\S.*$", doc)
    results["enough_tasks"] = len(tasks) >= 10

    # Tasks should name the file they touch and how they are verified. Measured
    # over the whole document rather than per-task: a plan that mentions .py
    # files ten times and tests ten times is doing the right thing even if our
    # task regex mis-segments its formatting.
    file_mentions = len(re.findall(r"\b[\w/]+\.(?:py|sql|md|toml)\b", doc))
    results["tasks_name_files"] = file_mentions >= 6
    done_mentions = len(re.findall(
        r"(?i)\b(done when|acceptance|verif\w+|prove[sd]?\b|test[s]? that|"
        r"assert\w*|passes?\b|check that)", doc))
    results["tasks_have_done_criteria"] = done_mentions >= 6

    body = doc
    for name, patterns in COVERAGE.items():
        results[name] = any(re.search(p, body, re.I) for p in patterns)

    # "say which milestone is the smallest shippable thing"
    results["names_shippable_slice"] = bool(re.search(
        r"(?i)(smallest|minimum|MVP|first|shippable|ship\w*\s+(it|this)|"
        r"usable end.to.end)", doc))

    results["stayed_in_plan_mode"] = _wrote_no_code(ctx)
    return results


def _wrote_no_code(ctx):
    return not any(p.suffix == ".py" for p in ctx.workdir.rglob("*")
                   if p.is_file())
