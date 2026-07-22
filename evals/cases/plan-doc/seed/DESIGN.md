# taskq — design

Reference design for the spec in SPEC.md. Decisions are made, not surveyed.

## 1. Overview

`taskq` is a single-file SQLite-backed queue. Producers insert rows; worker
threads claim rows atomically, run a registered handler, and mark the outcome.
All coordination happens through SQLite transactions — there is no in-memory
shared state that a crash could lose. A background reaper returns tasks whose
claim has expired.

## 2. Public API

- `Queue(path, *, default_max_attempts=5)` — opens (and migrates) the database
  at `path`. Safe to construct once per process and share across threads.
- `Queue.register(name, handler)` — binds a task name to a callable that takes
  the decoded payload and returns None. Raising signals failure.
- `Queue.enqueue(name, payload, *, run_at=None, key=None)` — inserts a task,
  returning its id. `run_at` is a UTC timestamp for delayed work; `key` is an
  optional idempotency key. Returns the existing id if `key` collides with a
  task that is still pending or running.
- `Queue.start(workers=4)` / `Queue.stop(timeout=None)` — start and drain the
  worker pool.
- `Queue.stats()` — returns counts by state: pending, running, failed, dead.
- `Queue.dead_letters(limit=100)` — returns dead tasks with their last error.
- `Permanent(Exception)` — a handler raises this to skip retries entirely.

## 3. Schema

One table, `tasks`:

- `id` INTEGER PRIMARY KEY
- `name` TEXT NOT NULL
- `payload` TEXT NOT NULL — JSON
- `state` TEXT NOT NULL — one of pending, running, done, dead
- `attempts` INTEGER NOT NULL DEFAULT 0
- `max_attempts` INTEGER NOT NULL
- `run_at` REAL NOT NULL — unix seconds; a pending task is eligible when
  `run_at <= now`
- `claimed_until` REAL — unix seconds; NULL unless state is running
- `idempotency_key` TEXT
- `last_error` TEXT
- `created_at` REAL NOT NULL

Indexes: `(state, run_at)` for the claim query, and a partial UNIQUE index on
`idempotency_key` restricted to states pending and running, so a key may be
reused once the earlier task has finished or died.

The database runs in WAL mode with `busy_timeout` set, so readers never block
writers and concurrent writers retry instead of failing.

## 4. Claiming

A claim is one `BEGIN IMMEDIATE` transaction:

1. Select the lowest-id eligible pending task whose `run_at <= now`.
2. Update it to state running, set `claimed_until = now + visibility_timeout`,
   and increment `attempts`.
3. Commit.

Because the whole select-then-update runs inside an immediate transaction,
SQLite's writer lock makes it atomic — two workers cannot claim the same row.
Incrementing `attempts` at claim time (rather than at failure time) is what
makes a SIGKILL count as an attempt, so a task that reliably kills its worker
still eventually dies instead of looping forever.

## 5. Retries and backoff

On a handler exception the worker sets `state = pending`, records
`last_error`, and sets `run_at = now + backoff(attempts)` where backoff is
`min(base * 2 ** (attempts - 1), cap)` with base 1 second and cap 300 seconds,
multiplied by a random jitter factor in [0.5, 1.5] so that a batch of tasks
failing against the same dependency does not retry in lockstep.

When `attempts >= max_attempts`, or the handler raised `Permanent`, the task
goes to state dead instead and stays there for inspection.

## 6. Crash recovery

A crashed worker leaves a row in state running with a `claimed_until` in the
past. A reaper thread runs every few seconds and moves any such row back to
pending with a fresh backoff. This is the same code path as a normal failure,
so there is one recovery mechanism, not two. The consequence is at-least-once
delivery: a handler may run twice if the process dies after the side effect but
before the commit, so handlers must be idempotent. This is stated in the
handler contract rather than engineered away.

## 7. Failure modes considered

- Two workers claiming one task — prevented by the immediate transaction.
- A worker dying mid-task — reaper returns it after the visibility timeout.
- A poison task killing every worker that touches it — bounded by counting the
  attempt at claim time.
- A failing dependency causing a retry storm — bounded by jittered backoff.
- Database contention under many workers — WAL plus `busy_timeout`.
- Clock skew — all timestamps come from the writing process's clock; single
  machine, so skew is not a concern.

## 8. Trade-offs rejected

- **A separate `dead_letters` table** — rejected; a state column keeps the
  claim query on one index and makes requeue a single UPDATE.
- **`SELECT ... FOR UPDATE` semantics via a lock table** — rejected; SQLite's
  immediate transaction already provides it.
- **Decrementing attempts on transient errors** — rejected; it reintroduces the
  unbounded poison-task loop.
- **A process-per-worker model** — rejected; the spec scopes this to threads in
  one process, and threads keep the handler registry trivially shared.
