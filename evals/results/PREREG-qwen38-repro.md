# Pre-registration — qwen38 vs qythos9 on repro-only

Written 2026-09-06 14:0x, BEFORE the confirming runs, after a first look at
n=8 vs n=12 gave a suggestive but non-significant result (p=0.117).

## Why this case
Per §5.136, `repro-only` is the only one of the four comparison cases with
dynamic range left on `qythos9`: per-run scores span 0.33–1.00. `exec-pinpoint`
is saturated at 1.000 and `e2e` is pinned at a scaffolding floor (§5.135), so
neither can show a model difference. This case can.

## The observation being tested
First look: `qythos9` 8/12 runs perfect (mean 0.819); `qwen38` 8/8 perfect
(mean 1.000). Fisher exact two-tailed **p = 0.1166 — not significant.**

## The test, fixed in advance
- **Statistic:** count of runs scoring exactly 1.000, per model.
- **Test:** Fisher exact, two-tailed, on the 2x2 of perfect vs imperfect.
- **alpha = 0.05.**
- **Target n = 20 per model**, extending both existing sweeps (same frozen
  build-142 agent root, same case, `--clean`). Existing runs are pooled with
  the extension; the rig is unchanged, which is the only condition under which
  pooling is legitimate.
- **Powered so:** if `qwen38` stays perfect at 20/20 against `qythos9` at its
  current rate, p = 0.0138. If `qythos9` regresses to 13/20, p = 0.0083.

## What would falsify it
`qwen38` dropping even one run materially weakens the claim: at 19/20 against
qythos9's 8/12 the result is no longer significant at 0.05. I am recording that
now so a single imperfect run cannot be quietly reframed as "still basically
perfect".

## What is NOT being claimed
- Not a general capability win. Three of the four cases are at or near ceiling
  for both models; this tests one case.
- The **iteration** difference (qwen38 ~5-6 vs qythos9 ~9 across three cases)
  is a separate, more consistent observation and is not what this p-value is
  about. It is reported descriptively, untested.

## Amendment 1 (14:07, after launch, before any extension run was graded)

The prereg above said both arms share the same agent root. On checking, they
did not: the original `qwen38-repro` n=8 ran against the **installed tree**
(stage 5 passed no `--agent-root`), while `qythos9`'s 0.819 came from stage 3
against **frozen-b142**. The stage-6 extension runs against frozen-b142 for
both models.

**Pooling is still legitimate, and this was verified rather than assumed.**
`diff -rq frozen-b142/locode locode` reports exactly one differing file,
`__init__.py`, and the entire diff is `__build__ = 142` → `146`. That constant
is a splash-screen tripwire with no reader anywhere in `locode/` or `evals/`;
all four commits touching `locode/` since b142 changed only that literal, with
the real work in `evals/`. The agent under test is byte-identical.

Two things recorded so this is not repeated:
- The trees diverged **only because I bumped `__build__` for a harness-only
  change** (build 146). `evals/LOG.md` shows the repo's own convention is *not*
  to bump for harness edits. The bump was harmless but it manufactured a
  rig-difference scare during a live sweep, which is exactly the cost the
  convention exists to avoid.
- Every future sweep intended for pooling passes `--agent-root` explicitly,
  including when the intent is "the current tree". Relying on the default made
  two arms differ silently.
