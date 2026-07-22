Read SPEC.md in this directory. Then carry out the whole job, in three stages,
in this one session:

First, write DESIGN.md — the approach, the public API, the precedence rules, the
coercion rules, and how errors are reported.

Second, write PLAN.md — milestones, each broken into numbered tasks, each task
naming the file it touches and how it will be verified.

Third, implement it: write envcfg.py and test_envcfg.py, then run
`python3 -m pytest -q` with the bash tool and keep working until the tests pass.

Do all three. Do not stop after the documents.
