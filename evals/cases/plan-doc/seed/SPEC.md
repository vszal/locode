# taskq — a durable in-process task queue

We need a small Python library, `taskq`, that lets a single application enqueue
background work and process it reliably, surviving a crash of the host process.
It is deliberately not a distributed system: one machine, one SQLite file, one
or more worker threads inside the same process.

## What it must do

Callers enqueue a named task with a JSON-serializable payload. Workers pull
tasks and run a registered handler function for that name. A task that raises is
retried later; a task that keeps failing eventually stops being retried and is
set aside for a human to inspect. Nothing may be lost if the process is killed
mid-run, and no task may be executed by two workers at once.

Callers also need to enqueue work for the future — "run this in ten minutes" —
and to avoid duplicate work when the same logical job is enqueued twice by
racing callers.

## Constraints

- Python 3.10+, standard library only. SQLite via the built-in sqlite3 module.
- Multiple worker threads in one process must be safe. Assume the caller may
  also enqueue from several threads.
- A worker that is killed (SIGKILL, power loss) must not leave a task stuck
  claimed forever — the task must become runnable again.
- Retries must not hammer a failing dependency: successive attempts back off.
- Throughput target is modest — hundreds of tasks per second, not millions.
- Observability matters: an operator must be able to ask how many tasks are
  pending, running, failed, and dead.

## Explicitly out of scope

Cross-machine coordination, priority classes, task cancellation, scheduled cron
expressions, and a web UI. Do not design these.

## Open questions the design must answer

- How is a task claimed atomically, and how does a claim expire?
- What exactly does "no duplicate work" mean here, and how is it enforced?
- What happens to a task that fails a fifth time?
- What does the handler contract look like — how does a handler signal a
  permanent failure versus a transient one worth retrying?
