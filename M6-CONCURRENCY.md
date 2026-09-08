# M6 — Concurrency: the backend pool

Plan for `[serving] mode = "concurrent"`. Expands `architecture.md` §14's
one-line M6 into something implementable; closes the two open sub-questions in
§13.6; and changes one stated non-goal, deliberately and in writing.

**Spine:** the backend pool of §5.5 — `PoolManager`, a router, N resident
models across managed and external backends.
**First workload:** escalate a stuck turn to a stronger model.
**Topologies:** all three (managed-local, managed-remote, external) from day
one, because the router should not know which it is talking to.

---

## 1. The memory model, which is not what §5.5 assumed

§5.5 says `max_resident` is bounded by a per-backend memory budget. True, but
the budget is dominated by the **KV cache, not the weights** — and that inverts
which models look "small". Measured on this box (24 GB, `iogpu.wired_limit_mb`
= 0 ⇒ default 75% = 18.0 GB, `memory_reserve_gb` = 5.0, context 41,525 tok)
using the repo's own `memory_fits`:

| alias | weights | KV @ 41k | need | alone |
|---|---|---|---|---|
| `qwen06` | 0.3 G | **4.4 G** | 4.8 G | yes |
| `sushicoder` | 5.5 G | 1.3 G | 7.6 G | yes |
| `qwen4i` | 2.1 G | **5.7 G** | 8.1 G | yes |
| `qwythos9` | 8.6 G | 1.3 G | 11.2 G | yes |
| `qwen38` | 11.0 G | 2.5 G | 15.1 G | yes |
| `qwencoder30` | 16.0 G | 3.8 G | 22.2 G | **no** |

`qwen06` is 0.3 G of weights and 4.4 G of cache — **14× its own size**. Any
`max_resident` policy that counts weights is wrong by an order of magnitude on
exactly the models you would co-resident. Two consequences the design must
carry:

- **Residency is a context budget.** `max_resident` is a *cap*, never a
  promise; the real gate is `resident_fits()` over the whole set. Changing
  `agent.max_history_chars` silently changes how many models fit, so the pool
  recomputes on every warm and refuses rather than evicts-and-hopes.
- **Co-resident pairs on this box:** `qwen06+sushicoder` 12.4 G,
  `qwen06+qwen4i` 12.9 G, **`qwen06+qwythos9` 16.0 G**, `sushicoder+qwen4i`
  15.8 G. Note what is absent: `qwythos9+qwen38` is 26.3 G against 18.0 G. **The
  weak-executor/strong-escalation pair does not fit locally.** M6's own first
  workload therefore requires a remote or external backend — which is why the
  topologies are not staged.

---

## 2. Milestones

Each has an exit criterion that is a fact, not a feeling.

### M6.0 — Spike: can two MLX servers share one Metal GPU? *(blocking)*

Everything below assumes two `mlx_lm.server` processes can hold wired buffers
on one Apple GPU whose sum is under the system cap. **This is unverified.** The
cap is system-wide but the allocations are per-process, and the failure mode on
this hardware is a kernel panic, not an exception (profiles.py records two,
2026-08-01 and 2026-08-02, from a single model that overcommitted).

Spike: start `qwen06` on `:8082` alongside `qwythos9` on `:8081` (16.0 G of an
18.0 G budget), drive both with real 41k-token contexts, watch `vm_stat` wired
pages. **Exit:** either a recorded procedure that survives 30 minutes of
alternating load, or a written finding that Metal co-residency is unsafe — in
which case local concurrency is dead, `max_resident` on a Metal backend is
pinned to 1 forever, and M6 becomes a purely multi-*endpoint* feature. Either
outcome is a result; do not build past this step without one.

> **CLOSED 2026-09-08 — PASSED, with a cost. (§5.147, rule 93.)**
> Staged behind a watchdog that hard-killed both servers at 16.5 GB GPU-wired.
> Stage 1 (`qwen06`+`sushicoder`, floor 12.48 G): 6 requests, 0 failures, peak
> 11.65 G. Stage 2, the pair specced above: **20 requests over 30.4 minutes of
> alternating 41k-context load, 0 failures, 0 aborts**, peak GPU-wired
> **15.53 G** against the 18.0 G cap — 2.47 G still in hand. Footprints
> plateaued and never resumed climbing. `max_resident` on Metal is **not**
> pinned to 1; M6 is not reduced to multi-endpoint.
>
> Three findings the spike was not asked for, which change M6.2:
> 1. **Co-residency taxes the neighbour 31%.** qwythos9 ran a steady-state
>    152.9 s per request co-resident vs **116.9 s alone** — same prompt, same
>    footprint, only the neighbour differs. Co-resident round 1 matched solo
>    (115.4 s); the tax began at round 2, once the second model was fully
>    resident, and the compressor peaked at 11.09 G against a 3.74 G median. It
>    is memory pressure, not compute contention, so it worsens as a pair
>    approaches the cap. Fine for M6.5 escalation (the weak model idles while
>    the strong one runs); a bad trade for steady parallel serving.
> 2. **Budget against wired, not footprint (rule 93).** Combined footprint was
>    20.1 G — over the cap — while only 15.53 G was wired. `resident_fits` must
>    use the wired ceiling or it will refuse pairs that demonstrably work.
> 3. **The table below is a floor, and the guard does not enforce a floor.**
>    Those `need` figures are weights x1.15 + one live sequence with the stored
>    prompt-cache budget at zero. Charge each server the budget it actually
>    gets (`_resident_cache_bytes`) and **no pair fits at all** — qwen38 alone
>    is 17.96 G of 18.0. Co-residency and the prompt cache compete for the same
>    bytes, and the cache is what buys the 175x reuse of §5.146. **§13.6(a) is
>    reopened by this:** `max_resident > 1` is not merely a memory decision, it
>    is a decision to run the pool cache-poor. M6.2 must choose explicitly.

### M6.1 — The seam

Extract a `ModelBackendManager` Protocol (`locode/server/base.py`) from
`SingleGpuManager`'s public surface: `resolve`, `known_aliases`, `ensure_up`,
`status`, `list_served`, `start`, `stop`, `switch`, `restart` — plus one new
method, `client_for(alias) -> ModelClient`.

`client_for` is the whole trick. Today `cli.py:88` constructs one
`ModelClient(cfg.base_url)` and hands it to `AgentLoop`; the loop holds it as
`self._client` for the session. In a pool, the client depends on which backend
holds the alias, so **managers own client construction** and the loop asks per
turn. `SingleGpuManager.client_for` returns the same client always, so single
mode is behaviourally identical.

**Exit:** `AgentLoop` no longer takes a `client` argument, `pytest -q` green,
and a single-mode `locode bench` matches the archived baseline — the refactor
must be invisible to the ladder.

> **Correction (2026-09-08):** "the archived n=6 baseline (24/24 for qwen38)"
> misread the archive. `evals/results/bench-r3-rule90.log` is *4 cases x 2
> models x **3** repeats = 24 **runs total**;* 24 was never a score. qwen38's
> result there was **12/12 solved**, per case: exec-pinpoint 5/5/5 iterations,
> exec-bugfix 5/5/9, repro-only 5/5/5, multi-defect-blind 11/10/10.
> So the exit run is `locode bench -m qwen38 --repeat 3`, and it must return
> **12/12** with iteration counts in that neighbourhood. Judge it on
> **iterations, not wallclock** (rule 88): the archived 529s time-to-done was
> measured on an otherwise-idle box, and rule 89 still applies — correctness
> gates before either number means anything.

> **CLOSED 2026-09-08 — PASSED, and measured rather than argued.**
> `AgentLoop.__init__` no longer takes a `client`; `pytest -q` 1493 green;
> `locode bench -m qwen38 --repeat 3` returned **12/12**, matching the archive
> exactly (`evals/results/bench-m6.1-seam.log`).
>
> One iteration count did move against the archive — repro-only 5/5/5 → 7/7/7
> — so it was **attributed by measurement, not by assertion**. A build-158
> (pre-seam) worktree ran the same case ×3:
>
> | build | repro-only iterations | time-to-done |
> |---|---|---|
> | 153 (archive) | 5, 5, 5 | 95 / 95 / 89 s |
> | 158 (pre-seam) | 7, 7, 7 | 100 / 102 / 102 s |
> | 160 (post-seam) | 7, 7, 7 | 101 / 105 / 105 s |
>
> The shift predates the seam and the seam adds nothing to it: pre- and
> post-seam are the same trajectory to the iteration. It arrived somewhere in
> builds 154–158, and note that the *extra two iterations cost ~7s total* —
> per-iteration cost fell from ~19s to ~14s, so this is the loop taking more,
> cheaper steps, not the model working harder. Bisected further in §5.148.

### M6.2 — Config and the memory gate

`[serving]` in `config.py` + `config.toml.example` (same change, per AGENTS.md):

```toml
[serving]
mode = "single"          # "single" | "concurrent"
max_resident = 1         # cap; resident_fits() is the real gate
max_inflight = 1
router = "pin"           # "pin" | "balance"

[[serving.backends]]
id = "local"
base_url = "http://127.0.0.1:8081"
managed = true
[[serving.backends]]
id = "lan"
base_url = "http://10.0.0.5:8081"
managed = false
```

New pure function beside `memory_fits`:

```python
def resident_fits(needs: list[int], total_ram, reserve, wired_limit)
    -> tuple[bool, int, int]
```

Same shape as `memory_fits`, over a set. Per-backend: a Metal backend budgets
against the local wired cap; a remote managed backend against *its* reported
RAM (unknown ⇒ refuse to co-resident, warm one at a time); an external backend
is not ours to budget and is trusted.

**§13.6(a) closed: default `max_resident = 1`, `max_inflight = 1`.** A user who
sets `mode = "concurrent"` and nothing else gets routing with no co-residency
and no parallelism — every other behaviour identical to single mode. Nothing is
inferred from detected hardware: on this platform a wrong inference panics the
machine, and the failure is not recoverable in-process.

> **Half done, 2026-09-08.** `resident_fits` is landed and unit-tested
> (build 160, `tests/test_manager.py`), including the `qwythos9+qwen38`
> refusal, the pair the spike actually ran, the wired cap binding tighter than
> RAM−reserve, and a rule-93 case pinning that footprint figures must not be
> fed to it. The `[serving]` table is **deliberately not written yet** — it is
> an on-disk format (AGENTS.md: ask), and finding 3 above changed what its
> defaults have to mean.
>
> **The decision, stated so it can be answered in one line.** `resident_fits`
> charges each model a *floor*: weights x1.15 plus one live sequence, stored
> prompt cache at zero. Charge the stored cache each server actually gets and
> nothing co-resides — qwen38 alone is 17.96 G of 18.0. So `max_resident > 1`
> cannot mean "fit two models as they are configured today"; it has to mean one
> of:
>
> | | what it means | cost |
> |---|---|---|
> | **(a) refuse** | charge the full cache budget; on this box no pair is ever admitted | honest, and `max_resident > 1` becomes dead config here |
> | **(b) split** | N resident models each get `1/N` of the cache budget, automatically | the pool silently goes cache-poor; §5.146's 175x reuse is what pays for it |
> | **(c) declare** | per-backend `prompt_cache_gb` in `[[serving.backends]]`; `resident_fits` charges what is written | one more knob, but the trade is visible in the file the user edits |
>
> **Recommended: (c), with (b)'s split as the default when the field is
> absent.** The reason is rule 93's reason — the failure mode here is a GPU
> panic, not an exception — so the number the gate charges should be a number
> someone chose, and the automatic split should only ever *shrink* what a
> server asks for, never grow it. Not implemented pending an answer.

**Exit:** `resident_fits` unit-tested against the six-model table above,
including the `qwythos9+qwen38` refusal; `config.toml.example` documents every
field; a `mode=concurrent, max_resident=1` run is indistinguishable from single.

### M6.3 — `PoolManager` and the router

`locode/server/pool.py`. Owns the backend list, a resident-set per backend, and
the alias→backend map. Warms on miss, refuses (never evicts blindly) when
`resident_fits` says no, LRU-evicts only when over `max_resident` *and* the
eviction is what makes the warm fit. `max_inflight` is an `asyncio.Semaphore`
over model calls; **tool execution stays serialized regardless** — the pool
parallelizes inference, never mutation.

**§13.6(b) closed: `router = "pin"` is the default.** A model stays on the
backend that holds its prompt-cache prefix. Prefill dominates locally, so a
cache-cold hop costs more than any queueing it avoids; `"balance"` exists for
external fleets where the endpoint is cache-agnostic anyway.

**Exit:** `/server` shows every backend with its resident set and headroom;
killing one backend degrades to the others with a named error rather than a
hang; cancellation (`Esc`) tears down all in-flight calls, tested.

### M6.4 — The vertical slice: escalate a stuck turn

The workload that justifies the pool. `loop.py` already detects exactly the
condition — `nudged_stall`, `nudged_repeat`, `nudged_nochange` and friends are
live and already drive nudges. Today an unproductive model gets nudged again.
With a pool it can be handed to a stronger one.

```toml
[models.escalation]
enabled = false          # opt-in
to = "qwen38"
on = ["stall", "repeat", "nochange"]
after_nudges = 2         # consecutive unproductive nudges of that class
max_per_session = 2
```

Design decisions, each with its reason:

- **The turn escalates, not the session.** Control returns to the primary model
  on the next turn. Silent permanent upgrade would make "which model am I
  measuring" unanswerable, which is the failure `bench` exists to prevent.
- **A handoff note is synthesized, not inherited raw.** The strong model gets
  the history plus an explicit statement of what was already tried and failed
  ("edited report.py:41 twice, tests still red"). The stuck model's flailing is
  the most misleading part of the transcript.
- **It is loud.** An `{"phase": "escalate", "from", "to", "reason",
  "iteration"}` event, rendered in the UI and counted by the harness. An
  escalation that is not in the event log cannot be measured, and an
  unmeasurable quality feature is how a benchmark gets gamed.
- **Cost is bounded** by `after_nudges` and `max_per_session`, because
  escalation is expensive for a reason §5.5 flags: the strong model's backend
  is cache-cold for this conversation and pays a **full prefill of the whole
  history**. On a long turn that can exceed the time the escalation saves. This
  is the slice's main risk and M6.5 is what decides it.

**Exit:** the pre-registered criterion below, met or not met, written down
either way.

### M6.5 — Measurement, pre-registered *(rule 74)*

Registered **before** the sweep runs, so a miss is a NO SHIP and not an
argument:

> **Ship criterion.** Escalation ships iff, on the four-case ladder with
> `qwythos9` as primary and `qwen38` as escalation target, pooled over **two**
> sweeps of `--repeat 3` (n=6 per case, per rule 84's homogeneity test before
> pooling), the solved count rises over the `qwythos9` baseline of **14/24** at
> Fisher p < 0.05, **and** median time-to-done per *solved* run rises by less
> than 2×.

Correctness gates first and time is the second gate only (rule 89): a variant
that solves more but takes four times as long is not a better daily driver, and
one that is faster because it gave up sooner fails the first gate outright.
The baseline is the pooled §5.143 figure, not either single sweep.

Secondary numbers to record regardless: escalations per run, the fraction that
converted a FAIL to an `ok` (the only escalation that paid), and prefill cost
at handoff. **If escalation converts nothing, that is a publishable finding
about weak-model failure modes** — the stall may be the task being out of reach
rather than the model being stuck — and the pool still stands on M6.3.

---

## 3. Cloud backends: a stated non-goal, changed on purpose

`architecture.md` §1 says *"No cloud model fallback — local only, by design
(cost/privacy)."* M6 relaxes this to **opt-in and loud**. Recording it here
because a non-goal that erodes silently is worse than one that is changed:

- A backend reaches the network beyond the LAN only with `remote_ok = true`
  explicitly set. There is no default, no inference, no fallback-on-failure.
- First use in a session raises a **permission prompt through `permissions.py`**
  naming the host and what is being sent. Escalation-to-cloud is not exempt —
  an automatic trigger is exactly where a silent egress would hide.
- Every off-machine turn is an event in the log, and the UI marks the model
  line with its host.
- **No redaction, and we say so.** The conversation contains file contents by
  construction. Claiming to scrub it would be a promise the harness cannot
  keep; the honest contract is a clear label and an explicit opt-in.
- `mode = "single"` never touches a cloud backend regardless of config.

§1 and §13 in `architecture.md` are amended in the same change as the code.

---

## 4. Risks

| risk | why it matters | mitigation |
|---|---|---|
| Metal co-residency panics | failure is a kernel panic, not an exception | M6.0 blocks everything; a negative result reduces M6 to multi-endpoint |
| Cache-cold prefill eats the win | escalation's cost scales with history length | measured in M6.5; `after_nudges` keeps escalations rare and late |
| 16.0 G of an 18.0 G cap | 2 G of headroom for the OS and everything else | `resident_fits` refuses rather than evicts; reserve stays configurable |
| Router complexity leaks into the loop | `loop.py` is 2953 lines and the hard correctness core | the pool is behind `client_for`; the loop learns one new concept (escalate) and nothing about backends |
| Two managers drift | `SingleGpuManager` stays the default forever | one Protocol, and the ladder must produce identical results in single mode (M6.1 exit) |

## 5. Not in M6

Parallel subagents, side-by-side compare, model-assisted compaction. Each is
cheap **after** M6.3 and each needs its own justification — compaction most of
all, since `agent/compact.py` deliberately refuses to let a weak model summarize
itself, and re-opening that needs evidence, not a config flag.
