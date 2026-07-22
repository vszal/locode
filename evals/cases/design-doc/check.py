"""Checks for the design-doc case.

Grading a design document mechanically is inherently partial: we can verify it
EXISTS, is substantial, is structured, and engages with each hard question the
spec posed — but not that the answers are good. Quality is reviewed by hand.
These checks are the regression gate; they catch "wrote nothing", "wrote three
paragraphs of restated spec", and "ignored the concurrency question".

Deliberately matched with word-boundary regexes over synonym sets rather than
loose substrings: a previous eval scored a false positive because it grepped
for "not found" and matched the model's own narration.
"""

import re

# Each entry: (check name, list of alternative patterns — any one satisfies it).
# Alternatives exist because a 9B model's vocabulary varies; we want to detect
# "engaged with the idea", not "used our preferred word".
TOPICS = {
    "covers_public_api": [r"\bAPI\b", r"\benqueue\b.*\(", r"public interface"],
    "covers_schema": [r"CREATE TABLE", r"\bschema\b", r"\bcolumns?\b"],
    "covers_atomic_claim": [r"\batomic", r"\bclaim(ed|ing)?\b", r"\blease\b",
                            r"BEGIN IMMEDIATE", r"\btransaction\b"],
    "covers_claim_expiry": [r"visibility timeout", r"\blease\s+(expir|timeout)",
                            r"stale", r"\bheartbeat\b", r"claimed_until",
                            r"reclaim"],
    "covers_backoff": [r"\bbackoff\b", r"exponential", r"\bjitter\b",
                       r"retry\s+delay"],
    "covers_dead_letter": [r"dead[- ]?letter", r"\bDLQ\b", r"max_attempts",
                           r"max(imum)?\s+(retries|attempts)", r"\bdead\b"],
    "covers_idempotency": [r"idempoten", r"\bdedup", r"UNIQUE", r"duplicate"],
    "covers_crash_recovery": [r"crash", r"recover", r"SIGKILL", r"restart"],
    "covers_handler_contract": [r"handler", r"permanent.*(fail|error)",
                                r"transient", r"raise"],
    "covers_tradeoffs": [r"trade[- ]?off", r"\brejected\b", r"\balternativ",
                         r"we chose", r"instead of"],
    "covers_observability": [r"observab", r"\bmetrics?\b", r"\bstats\b",
                             r"\bpending\b.*\brunning\b", r"\bcounts?\b"],
}


def check(ctx):
    doc = ctx.read("DESIGN.md")
    body = doc.lower()
    results = {"wrote_design_doc": bool(doc.strip())}

    if not doc.strip():
        # Everything else is vacuously false; report them so the score
        # denominator stays comparable across runs.
        results.update({k: False for k in TOPICS})
        results["substantial"] = False
        results["structured"] = False
        results["stayed_in_design_mode"] = _wrote_no_code(ctx)
        return results

    results["substantial"] = len(doc) >= 1500
    # At least 5 markdown headings — a real document, not one wall of prose.
    results["structured"] = len(re.findall(r"^#{1,4}\s+\S", doc, re.M)) >= 5

    for name, patterns in TOPICS.items():
        results[name] = any(re.search(p, body, re.I | re.S) for p in patterns)

    # The prompt said "do not write any code yet". A model that ignores scope
    # here is the same model that will ignore scope in an execute phase.
    results["stayed_in_design_mode"] = _wrote_no_code(ctx)
    return results


def _wrote_no_code(ctx):
    return not any(p.suffix == ".py" for p in ctx.workdir.rglob("*")
                   if p.is_file())
