"""`locode bench` — measure how a local model actually does on real tasks.

The user-facing half of the eval work. `evals/` holds the research harness
(arms, A/B, permutation tests, an archive of sweeps); this holds the four cases
worth shipping and just enough runner to answer one question: on THIS machine,
with THESE models, which one gets the work done and how long does it take.
"""

from locode.bench.runner import (  # noqa: F401
    LADDER,
    BenchResult,
    Case,
    CheckCtx,
    load_cases,
    run_case,
)
