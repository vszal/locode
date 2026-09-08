"""Configuration: defaults <- config.toml <- env (LOCODE_*) <- CLI overrides.

CLI overrides are applied by cli.py via Config.override(); this module handles
the first three layers and the XDG paths. Tolerant by design — a missing or
partial config.toml just falls back to defaults so the tool always starts.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _xdg(env: str, default: Path) -> Path:
    val = os.environ.get(env)
    return Path(val) if val else default


HOME = Path.home()
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "locode"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "locode"
CONFIG_PATH = CONFIG_DIR / "config.toml"
HISTORY_PATH = STATE_DIR / "history"
DATA_DIR = _xdg("XDG_DATA_HOME", HOME / ".local" / "share") / "locode"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8081
    scheme: str = "http"          # "http" | "https" (for remote/proxied endpoints)
    base_url: str = ""            # full override (e.g. https://gpu-box:8081); wins
    auto_start: bool = True
    # Manage a LOCAL mlx_lm.server process (start/stop/switch)? "auto" => yes only
    # for a loopback endpoint; "no" treats the endpoint as remote/unmanaged.
    manage: str = "auto"          # "auto" | "yes" | "no"
    mlx_bin: str = ""  # auto-detected if empty
    # Refuse to load a local model whose estimated footprint (weights × overhead
    # + a KV cache sized from the model's real attention shape at our peak
    # context) won't fit under the tighter of (total RAM − this reserve) and the
    # macOS GPU wired-memory cap, so a too-big model can't take the machine
    # down. 0 disables the guard. See server/manager.py:_check_memory_budget.
    memory_reserve_gb: float = 5.0
    # Multiple of one live sequence's KV cache to hand mlx as
    # --prompt-cache-bytes. mlx spends that budget on STORED prompt caches *plus*
    # the live KV, and evicts with trim_to(max(0, total - active)) — so a budget
    # smaller than a single live sequence clamps to zero and wipes every stored
    # cache, forcing a full re-prefill each request. 1.0 is the floor that stops
    # the wipe; above 1.0 is what actually buys reuse across turns, at the cost
    # of that much more resident memory (the memory guard counts it, so raising
    # this can make a model refuse to load). See ROADMAP §5.144.
    prompt_cache_multiple: float = 1.0

    def endpoint(self) -> str:
        """Resolved base URL: an explicit base_url wins, else scheme://host:port."""
        if self.base_url:
            return self.base_url.rstrip("/")
        return f"{self.scheme}://{self.host}:{self.port}"

    def is_managed(self) -> bool:
        """Whether locode owns the server process (can start/stop it locally)."""
        if self.manage == "yes":
            return True
        if self.manage == "no":
            return False
        host = (urlsplit(self.base_url).hostname if self.base_url else self.host) or ""
        return host in _LOOPBACK


@dataclass
class BackendConfig:
    """One model-serving endpoint in a concurrent pool.

    `prompt_cache_gb` is the M6.2 decision (c): the prompt-cache budget this
    backend is *charged by the memory gate and launched with*. Written by a
    person, because on this platform a wrong number panics the GPU driver
    rather than raising (rule 93), so the figure that decides co-residency
    should be one somebody chose. 0.0 means "not declared" and hands the
    backend to the automatic 1/N split — see `resident_cache_bytes`.
    """
    id: str = ""
    base_url: str = ""
    managed: bool = True
    prompt_cache_gb: float = 0.0


@dataclass
class ServingConfig:
    """Concurrent serving. Defaults are single-mode behaviour, exactly.

    `mode = "concurrent"` with nothing else set gets routing and no more: one
    resident model, one in-flight request, every other behaviour identical to
    single mode. Nothing is inferred from detected hardware — on macOS a wrong
    inference panics the machine and the failure is not recoverable in-process,
    so co-residency is opt-in by an explicit `max_resident`.
    """
    mode: str = "single"          # "single" | "concurrent"
    max_resident: int = 1         # cap; resident_fits() is the real gate
    max_inflight: int = 1
    router: str = "pin"           # "pin" | "balance"
    backends: list[BackendConfig] = field(default_factory=list)


@dataclass
class ModelConfig:
    # qwen38 (Qwen3.8-27B, 3-bit, ~11 GB) is the out-of-box default as of
    # 2026-09-06. It beat the previous default qwythos9 on `repro-only` — the one
    # eval case with dynamic range left — 20/20 perfect runs against 9/20,
    # Fisher exact p=0.0138 even when qwythos9 is granted its single best sweep
    # (ROADMAP §5.138). The other three cases are at ceiling for both models, so
    # this is one demonstrated win plus three ties, not a sweep.
    #
    # It is also *faster in wallclock despite decoding ~4x slower per character*
    # (95s vs 116s on repro-only): it needs ~5-6 iterations where qwythos9 needs
    # ~9. Judge a local model by time-to-done, not tokens/sec (rule 88);
    # the step count explains the win, it is not the verdict. `locode bench`
    # measures this on the user's own machine.
    #
    # Caveat for smaller machines: at ~11 GB it needs more headroom than the
    # 9.6 GB qwythos9, and server/manager.py:_check_memory_budget will refuse it
    # under (total RAM - memory_reserve_gb). On a 16 GB box set
    # default = "qwythos9" in config.toml. Override per-run with -m.
    default: str = "qwen38"
    # Per-turn generation ceiling. A whole write_file/edit_file call — the file
    # body included — must fit in ONE completion, and on a reasoning distill the
    # <think> preamble eats into the same budget, so a tight cap truncates the
    # tool call mid-write (the model then "chunks" a large file).
    #
    # But this is also a WALLCLOCK setting in disguise, which is easy to miss.
    # A 9B MXFP8 model on an M4 Max streams ~26 tok/s, so the old 32768 ceiling
    # let ONE reply run for ~21 minutes — twice the entire turn budget — and the
    # loop's iteration/repeat guards can't see it, because they only run
    # BETWEEN iterations (measured 2026-07-21: a design-doc run spent 860 of its
    # 900 seconds inside a single completion and produced nothing).
    #
    # A whole write_file call — the file body included — must fit in ONE
    # completion, and on a reasoning distill the <think> preamble eats the same
    # budget, so this has to clear the largest document the loop legitimately
    # emits at once. It is also a wallclock setting in disguise: at ~73 chars/s
    # measured on an M4 Max, 8192 tokens is ~32k characters and ~440 seconds —
    # inside max_wallclock_seconds, but only just, so a max-length reply is most
    # of a turn. 12288 would be ~650s and could never finish one. Anything
    # longer than this belongs in a write_file followed by append_file calls,
    # which is what the truncation nudge now asks for.
    max_tokens: int = 8192
    temperature: float = 0.3
    # Anti-repetition sampling. Local models occasionally fall into a degenerate
    # loop and stream one giant reply repeating a short phrase (the "megahyper…
    # universe" runaway). These curb it at the sampler; the streaming abort in
    # the client (see model/repetition.py) is the deterministic backstop. Left
    # OFF by default (0.0 / None) so ordinary code generation — which legitimately
    # repeats tokens like indentation and braces — is not penalised; raise
    # frequency_penalty (~0.2-0.5, OpenAI-standard) or repetition_penalty
    # (~1.1-1.3, llama.cpp/mlx extension) per model if it degenerates. Both
    # neutral by default: frequency_penalty 0.0 and repetition_penalty 1.0 mean
    # "no penalty", and neither is put on the wire at its neutral value.
    frequency_penalty: float = 0.0
    repetition_penalty: float = 1.0
    # Extra stop strings passed through to the server (in addition to whatever the
    # model's chat template already stops on). Empty by default.
    stop: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    # The loop executes ONE tool call per iteration for non-native (fenced
    # ```tool) callers — see loop.py's `trimmed` grounding logic — so this is
    # ~one iteration per file read/edit/test-run, not per logical step. A
    # genuinely multi-file refactor can easily need 30-40 calls; the real stuck
    # loops are bounded by max_repeat_calls/max_error_stall too, so this ceiling
    # only needs to catch a model that's truly never going to finish, not cut
    # off one that's still making progress.
    #
    # READ THIS BEFORE CHANGING IT. From build 154 this is the PRIMARY bound on
    # a turn, not a backstop. The wallclock it used to share the job with is now
    # extendable (progress_grant_seconds below) and does not cap a turn that
    # keeps working, so this is what ends a healthy long turn — and it is what
    # contains a model manufacturing fake progress, since a fresh `bash echo N`
    # is a new tool signature every time and clears both grant triggers. Those
    # two effects scale together: any number that buys a longer agentic loop
    # lengthens the leash on a determined waster by exactly as much.
    #
    # 150 (build 155) is sized off observed trajectories, not theory. Real work
    # measured on the bench cases runs 5-16 iterations, and the live qwen38 turn
    # that motivated the extendable budget used 14; the documented worst case for
    # a hand-written task is the 30-40 calls of a multi-file refactor. 150 is
    # ~4x that, which is the headroom for the retries and re-reads a real run
    # spends on top of its nominal plan. The old 50 left almost none — it was
    # inherited from when the wallclock was the real ceiling and 50 only had to
    # catch a model that would never finish.
    #
    # The cost side stays bounded: at the 16-23s/iteration observed against
    # local models, a turn that runs this out costs ~40-60 minutes, and the
    # genuinely stuck loops are caught far earlier by max_repeat_calls (3) and
    # max_error_stall (3). It is the model making *distinct* useless calls that
    # this number, and only this number, stops.
    #
    # RAISE IT for a deliberate agentic loop — a long autonomous session, a
    # sweep over many files, anything you would leave running — where 150 will
    # cut off real work. Several hundred to ~1000 is reasonable *if you are
    # supervising it or the task is genuinely that long*; Esc is always the
    # real backstop. What you give up is the bound on wasted local-model time
    # if the model turns out to be spinning, so raise it for the run, not for
    # the config file.
    max_iterations: int = 150
    max_wallclock_seconds: int = 600
    # A turn's wallclock budget is a FLOOR, not a ceiling. Every time the model
    # makes real progress the deadline is pushed out to `now + this`, so a turn
    # that keeps working keeps running and only a turn that goes quiet dies.
    # Motivated by a live qwen38 session (ROADMAP 5.144) killed at 600s mid-
    # survey while it was still posting plan updates: over half the turn's
    # server time went to prompt-cache-miss PREFILL, which is latency the model
    # did not cause and cannot avoid, yet the flat budget charged it anyway.
    # What counts as progress is deliberately narrow -- a tool-call batch not yet
    # issued this turn, a bash that exited 0, or a plan task COMPLETED (forward
    # only; re-stating or reverting a plan buys nothing) -- because whatever
    # resets the clock is what a stuck model gets to do forever. Prose, the most
    # freely produced output of a degenerate model, does not qualify.
    # 0 disables the extension and restores a flat `max_wallclock_seconds`;
    # headless (-p) runs default to 0 so benchmarks stay bounded and comparable
    # (rule 91). There is deliberately NO absolute ceiling in interactive use:
    # a ceiling forecloses long-running agentic loops, which is a non-starter
    # for the product.
    progress_grant_seconds: float = 300.0
    max_malformed_retries: int = 3  # bail if the model keeps emitting bad tool JSON
    # How many times a reply cut off at the token limit may be re-nudged. More
    # than one because a genuinely long deliverable (a full design document) can
    # legitimately need two passes to fit under model.max_tokens — one-shot
    # would return the half-written second attempt as if it were the answer.
    max_truncated_retries: int = 2
    # How many times a truncated write_file/append_file may be SALVAGED — its
    # partial content landed on disk and the model steered to append the rest,
    # instead of the whole document evaporating. Higher than the plain truncation
    # cap because each salvage is real forward progress (the file grows), and a
    # very long document legitimately lands over several append passes; a
    # degenerate re-write-the-same-thing loop is still caught by the repeat
    # detector, so this only needs to bound genuinely huge deliverables.
    max_salvaged_writes: int = 4
    # How many times a reply aborted for runaway repetition (the client cut a
    # degenerate token loop off mid-stream) may be nudged before the turn stops.
    # The garbage reply is discarded, not kept, so each retry costs only a fresh
    # generation; a few is enough to shake most models out of the attractor.
    max_repetition_aborts: int = 3
    # Drop a tool call that changed NOTHING out of the resent history, replacing
    # it with a one-line "rejected" marker (agent/loop.py:redact_noop_calls).
    # A rejected call otherwise stays in history verbatim, so a model on its
    # third attempt is reading three worked examples of the call we are telling
    # it to stop making — and the demonstration beats the instruction. Off
    # switches the behaviour back for an A/B.
    redact_noop_calls: bool = True
    # Reset a call's repeat streak when an edit LANDED since it last ran: the
    # model is verifying a change, not spinning. Off restores the pre-build-88
    # behaviour, where re-running a test between two real edits counted as a
    # repeat whenever the output happened not to move — which ended 41% of all
    # logged runs, over half of them wrongly. Leave this on unless A/B-ing it.
    repeat_resets_on_landed_edit: bool = True
    # Refuse edit_file/replace_lines on a file the model has NEVER read this
    # session (write_file counts as reading — it authored the body). The
    # measured root cause behind the edit-failure loops: on the b90 exec-bugfix
    # corpus the model reconstructed the target function from a pytest traceback
    # and edited from memory, so `old` matched nothing; across all 10 runs of
    # both arms it landed at most ONE successful edit and fixed none of the
    # three seeded bugs. One read costs one iteration; the guess-loop cost five
    # to seven and ended in surrender. Off restores the pre-build-93 behaviour
    # for an A/B.
    require_read_before_edit: bool = True
    # The same idea one level up, and deliberately much softer: advise the model
    # to RUN the code before it edits, when the request named a symptom and
    # nothing has been executed yet this turn. 5.111 relocated the problem it
    # targets — on the live sync-script sweep the model landed an edit in 6 runs
    # of 6 and fixed the bug the user actually named in 0 of 6. It was not
    # failing to find the defect; it was never looking for it, because nothing
    # ever showed it the symptom.
    #
    # Unlike require_read_before_edit this is an ADVISORY, not a gate, and the
    # asymmetry is the point: a read is always possible, a run is not. Some
    # tasks have nothing to execute. So it fires at most once per turn, does not
    # suppress the edit, and says out loud that "there is nothing to run here"
    # is an acceptable answer.
    #
    # DEFAULT ON since build 136. Measured on `bugfix-notest`, n=12 paired,
    # qwythos9, against an A/A noise floor of -0.012 taken the same day:
    #
    #     runs_clean       0/12 -> 11/12      fixed_decoy   0/12 -> 11/12
    #     fully_fixed      0/12 -> 11/12      fixed_named  12/12 -> 11/12
    #     score  0.286 -> 0.929 (+0.643, W11/L1/T0, sign-flip p=0.001)
    #
    # The baseline never ran the script even once in 12 runs, so it never saw
    # the NameError on the first line of its output. One advisory converts
    # that. Cost is real and accepted: 3.0 -> 6.3 tool calls and 34s -> 52s per
    # run. See ROADMAP 5.116 — this breached the pre-registered 25% tool-call
    # cap, which was the wrong shape of guard for a lever whose entire purpose
    # is to add a tool call.
    require_run_before_edit: bool = True
    # [lever 0e] Emit the repeat nudge one occurrence EARLY for batches that
    # are all mutating edits — at the second identical call rather than the
    # third — WITHOUT suppressing the call. The nudge and the repeat-kill are
    # one attempt apart by construction: the nudge `continue`s, so the streak
    # never advances and the next appearance of that signature falls straight
    # through to the stop. Measured runway is a median of 1 iteration, 51% get
    # one or less (ROADMAP 5.100/5.101), which no wording can survive. Warning
    # early lands ~5 iterations sooner in 40% more runs. It deliberately does
    # NOT block: the second identical mutating call reports SUCCESS 60% of the
    # time, so suppressing here would bundle an unbounded behaviour change into
    # a timing fix.
    #
    # DEFAULT OFF as of build 133 — b132-early0e graded it NO SHIP on the rule
    # fixed in advance (ROADMAP 5.104). Both gates passed, so this is a real
    # negative and not a void sweep: the lever fired in 16/16 candidate runs and
    # the gating metric, the share of runs ending in the repeat-stop, moved
    # +0.0pp (3/16 vs 3/16, Fisher p=1.0). That metric is one of the few this
    # project has shown to be slot-UNbiased (2/16 vs 2/16 on the aa16-slot A/A),
    # so the tie is trustworthy. Worse, surrender rose: within the candidate
    # slot, gave-up went 0/16 at build 131 to 11/16 and 12/16 at build 132.
    # An extra nudge reads as permission to stop.
    #
    # Kept behind the flag, with its tests, so the timing can be re-tested by a
    # future design without rebuilding it. Turn it on only inside an A/B.
    early_repeat_warn: bool = False
    max_repeat_calls: int = 3        # bail if it repeats the same call w/o progress
    max_error_stall: int = 3         # nudge/bail if edits keep hitting the same error
    max_nochange_edits: int = 2      # redirect/bail if edits keep changing nothing
    # Iterations a turn may still spend after the ESCALATED same-failure steer
    # (the one that fires on the third identical test result) before the turn
    # ends itself. 0 disables the cap.
    #
    # Measured over 140 runs / 3787 iterations of the last five sweeps: 49% of
    # runs reach that steer, and between them they spend 712 iterations — 19%
    # of every iteration in the corpus — after it, to produce ONE verified
    # finish in 69. 88% of them are eventually stopped by the repeat guard or
    # max_error_stall anyway; those guards just arrive ~10 iterations late,
    # because they key on the error TEXT (which the model keeps varying) while
    # the same-failure counter keys on the test's identity (which it doesn't).
    # This is the missing stop on the signal that actually tracks stuck-ness.
    #
    # 8 is deliberately conservative, not tuned: the single observed recovery
    # went green at exactly +8, so the budget is set to keep it. It still cuts
    # 46% of the waste. A quarter of these runs burn 18-30 further iterations
    # at a 0% success rate. ROADMAP 5.43.
    escalated_stall_budget: int = 8
    # Build 116. The same budget, armed by an EARLIER trigger: an `edit_file`
    # whose `old` and `new` are byte-identical. Sharpest death marker in the
    # archive — 4% of the runs that emit one ever verify against a 45% base
    # rate — and it leads the escalated steer by a median of 4 iterations in the
    # runs that hit both, with some runs never reaching the steer at all.
    #
    # Larger than the steer's budget on purpose. It fires sooner, so an equal K
    # would be a strictly tighter cut; and the one run in 140 that ever came
    # back from either signal went green 9 iterations after its own no-op. Both
    # numbers rest on that single run — recalibrate up from evidence, never
    # down. 0 disables this trigger without disabling the other. ROADMAP 5.44.
    noop_resend_stall_budget: int = 10
    max_consecutive_errors: int = 4  # nudge/bail when nothing at all succeeds
    # Consecutive batches where everything SUCCEEDED and returned nothing. Lower
    # than max_consecutive_errors: an empty answer repeats perfectly (the query
    # is deterministic), so waiting longer only buys identical silence.
    max_noinfo_calls: int = 3
    # Nudge if the model edits the SAME file this many times in a row without
    # ever running anything (py_compile/pytest/python) or re-reading it to see
    # the result — the open-loop editing that lets a weak model "fix" a file
    # into a duplicated mess without noticing (the gemmacoder12 loop). A verify
    # bash run or a re-read of that file re-arms the gate. 0 disables.
    max_unverified_edits: int = 3
    # Bail if the model keeps trying to end the turn without EVER having
    # attempted a write_file/edit_file call for a deliverable it was explicitly
    # asked to produce (e.g. "writing a PLAN.md") — as opposed to having tried
    # and failed/been denied, which is trusted after a single nudge.
    max_missing_deliverable_retries: int = 3
    # How many times a model that tries to end the turn with tasks still open
    # in its OWN update_plan list gets pushed back to the work. Bounded because
    # a task it genuinely cannot finish must not become an infinite loop; the
    # nudge explicitly offers "mark it done and say why" as the way out.
    max_open_task_retries: int = 3
    # Catches a model burning wallclock on slow/rambling completions WITHOUT
    # advancing iterations — a different failure mode than simply running out of
    # time. Nudged once (never a hard stop; the wallclock/iteration caps above
    # already bound the turn) when the fraction of iterations consumed falls
    # below slow_progress_ratio x the fraction of wallclock consumed. Held off
    # until BOTH grace thresholds pass, so first-iteration cold-start / first-
    # token latency can't skew the ratio into a false positive.
    slow_progress_ratio: float = 0.5
    slow_progress_grace_seconds: float = 60.0
    slow_progress_grace_iterations: int = 1
    # Hard ceiling on total history size (chars, summed across all messages —
    # a cheap proxy for tokens at ~4 chars/token; no tokenizer dependency).
    # locode has no context compaction: history only shrinks via an explicit
    # reset, so a long session (or a stuck loop re-appending similar content
    # each turn) grows it unboundedly. A local mlx server doesn't reliably
    # reject an over-budget prompt — observed in practice: a stuck edit loop
    # grew the prompt cache past 5GB and mlx_lm hard-crashed with a Metal
    # "Insufficient Memory" abort instead of returning an error. Default is
    # conservative for a ~32K-token local model's context window; raise it for
    # models with a much larger window (e.g. a 1M-ctx model).
    max_history_chars: int = 100_000
    # Soft threshold (fraction of max_history_chars) that triggers structural,
    # deterministic compaction (agent/compact.py) BEFORE the hard stop above
    # can fire — no model call involved, so a weak local model can't stall or
    # hallucinate its way through it. Stale tool-result dumps collapse to a
    # one-line summary and bulky tool-call args (a write_file's full file
    # body) get shrunk; the system prompt, every real user prompt, file-change
    # receipts, and a trailing window of compact_keep_recent messages are
    # always kept verbatim. The same logic backs the explicit /compact command.
    auto_compact_ratio: float = 0.75
    # How many of the most recent messages auto-compact / /compact always
    # leave untouched (the current work in progress).
    compact_keep_recent: int = 8
    # Minimum fraction of the history an auto-compaction pass must actually
    # recover for its result to be KEPT. Below this the pass is discarded and
    # the original list stands.
    #
    # A compaction is not free just because it needs no model call: rewriting
    # the history invalidates the server's prompt-cache prefix from the first
    # changed message on, so the whole tail is re-prefilled. That is worth
    # paying for a real reduction and never worth paying for a rounding error.
    # Once every squeezable message has been squeezed, a saturated history sits
    # just above the soft threshold and each pass recovers ~1% while each
    # iteration adds it back — so it compacts forever. Measured in production
    # (2026-09-07, qwen38): `200 -> 199 messages, 77,144 -> 76,492 chars` on
    # every iteration, a full ~22k-token re-prefill each time at 130-240s, ~3
    # tool calls per 600s turn, and the read-before-edit record cleared often
    # enough that an edit was refused for a file read one call earlier.
    #
    # Discarding rather than latching is deliberate: a discarded pass costs
    # only some pure-Python work over a list, so it is safe to re-try every
    # iteration, and a genuinely bulky new tool result gets compacted normally
    # the moment one arrives. 0 restores the old always-accept behaviour.
    min_compact_recovery_ratio: float = 0.05


@dataclass
class PermissionsConfig:
    # tool name -> "auto" | "ask" | "deny"
    tools: dict[str, str] = field(default_factory=lambda: {
        "read_file": "auto", "ls": "auto", "glob": "auto", "grep": "auto",
        "write_file": "ask", "append_file": "ask", "edit_file": "ask",
        "replace_lines": "ask", "move_file": "ask", "bash": "ask",
        "web_search": "ask", "web_fetch": "auto",
        # Bookkeeping only — update_plan touches nothing but the agent's own
        # in-memory task list, so prompting for it would be pure noise.
        "update_plan": "auto",
        # Asking the user a question is not an action that needs approving, and
        # prompting "allow ask_user?" before the question itself is absurd. In
        # headless mode there is no selector, and the tool declines on its own
        # with a message the model can act on — which is the point: it has to
        # REACH its own run() to do that, so it must not resolve to "ask".
        "ask_user": "auto",
    })
    auto_allow_under: list[str] = field(default_factory=lambda: ["./sandbox"])
    deny_paths: list[str] = field(default_factory=lambda: [
        "~/.ssh", "~/.aws", "~/.config/locode",
    ])


@dataclass
class EditorConfig:
    command: str = ""
    open_diffs: bool = True
    diff_tool: str = ""
    wait: bool = True


@dataclass
class WebConfig:
    # web_search provider: "auto" (a keyed provider if configured, else the
    # keyless "duckduckgo" default), "tavily", "brave", or "duckduckgo".
    search_provider: str = "auto"
    # Per-provider keys (env TAVILY_API_KEY / BRAVE_API_KEY also honored). With
    # no key a provider stays registered but disabled with an actionable error.
    tavily_api_key: str = ""
    brave_api_key: str = ""
    max_results: int = 5
    # web_fetch egress allowlist — host SUFFIXES the model may fetch. Empty =>
    # fail closed (every fetch refused). See tools/web.py for the SSRF guard.
    fetch_allowlist: list[str] = field(default_factory=lambda: [
        "docs.python.org", "developer.mozilla.org", "en.wikipedia.org",
        "pkg.go.dev", "docs.rs", "example.com",
    ])
    fetch_max_bytes: int = 5_000_000
    fetch_timeout: int = 20


@dataclass
class ContextConfig:
    # Repository instruction files folded into the system prompt, read from the
    # git root down to cwd so the nearest one has the last word. CLAUDE.md is
    # deliberately absent: it belongs to another tool, and silently absorbing
    # another vendor's instructions is a surprise, not a feature — add it here
    # if you want it. See locode/context.py.
    instruction_files: list[str] = field(
        default_factory=lambda: ["AGENTS.md", "LOCODE.md"])
    # Hard character budget for the rendered block. A local model has ~32K
    # tokens of context; an unbounded instructions file would spend it before
    # the conversation starts. 0 disables the feature entirely.
    max_instruction_chars: int = 8000


@dataclass
class UIConfig:
    markdown: bool = True       # line-buffered markdown styling of answers (TTY+color)
    spinner: bool = True        # animated wait indicator for model load / first token
    timing: bool = True         # per-turn `~tok · Ns · tok/s` trailer


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    serving: ServingConfig = field(default_factory=ServingConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    editor: EditorConfig = field(default_factory=EditorConfig)
    web: WebConfig = field(default_factory=WebConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    aliases: dict[str, str] = field(default_factory=dict)  # extends the built-in table
    # Per-model reasoning override: alias or model-id substring -> "on" | "off"
    # | "auto". Layers over the capability profile's default at server launch.
    # "on"/"off" force the chat-template enable_thinking kwarg; "auto" omits it.
    thinking: dict[str, str] = field(default_factory=dict)

    # --- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls()
        raw = _read_toml(path if path is not None else CONFIG_PATH)
        if raw:
            cfg._merge_toml(raw)
        cfg._apply_env(os.environ)
        return cfg

    def _merge_toml(self, raw: dict[str, Any]) -> None:
        _assign(self.server, raw.get("server", {}))
        # [serving] is flat except for [[serving.backends]], an array of
        # tables that _assign cannot build (it only sets scalar fields).
        serving = dict(raw.get("serving", {}))
        backends = serving.pop("backends", None)
        _assign(self.serving, serving)
        if backends is not None:
            self.serving.backends = [_backend(b) for b in backends]
        _assign(self.model, raw.get("model", {}))
        _assign(self.agent, raw.get("agent", {}))
        _assign(self.editor, raw.get("editor", {}))
        _assign(self.web, raw.get("web", {}))
        _assign(self.context, raw.get("context", {}))
        _assign(self.ui, raw.get("ui", {}))
        perms = raw.get("permissions", {})
        # Per-tool keys live flat in [permissions] alongside the list keys.
        for k, v in perms.items():
            if k == "auto_allow_under":
                self.permissions.auto_allow_under = list(v)
            elif k == "deny_paths":
                self.permissions.deny_paths = list(v)
            else:
                self.permissions.tools[k] = v
        self.aliases.update(raw.get("aliases", {}))
        self.thinking.update(raw.get("thinking", {}))

    def _apply_env(self, env: dict[str, str]) -> None:
        # A small, documented set of env overrides for the common knobs.
        if "LOCODE_MODEL" in env:
            self.model.default = env["LOCODE_MODEL"]
        if "LOCODE_PORT" in env:
            self.server.port = int(env["LOCODE_PORT"])
        if "LOCODE_HOST" in env:
            self.server.host = env["LOCODE_HOST"]
        if "LOCODE_SCHEME" in env:
            self.server.scheme = env["LOCODE_SCHEME"]
        # One-shot remote endpoint: LOCODE_BASE_URL=https://gpu-box:8081 overrides
        # scheme/host/port entirely (and, via is_managed(), marks it remote).
        if "LOCODE_BASE_URL" in env:
            self.server.base_url = env["LOCODE_BASE_URL"]
        if "LOCODE_MANAGE_SERVER" in env:
            self.server.manage = env["LOCODE_MANAGE_SERVER"]
        if "LOCODE_NO_AUTOSTART" in env:
            self.server.auto_start = False
        if "LOCODE_EDITOR" in env:
            self.editor.command = env["LOCODE_EDITOR"]
        # Compaction budget. Exposed because the auto-compact path is otherwise
        # untestable end-to-end: a real session crosses the 75k threshold over
        # many turns, but an eval case is one headless turn. Setting this low
        # puts a short run into the compaction regime on purpose.
        if "LOCODE_MAX_HISTORY_CHARS" in env:
            try:
                self.agent.max_history_chars = int(env["LOCODE_MAX_HISTORY_CHARS"])
            except ValueError:
                pass
        # Search keys: explicit config wins; otherwise fall back to standard envs.
        if not self.web.tavily_api_key and env.get("TAVILY_API_KEY"):
            self.web.tavily_api_key = env["TAVILY_API_KEY"]
        if not self.web.brave_api_key and env.get("BRAVE_API_KEY"):
            self.web.brave_api_key = env["BRAVE_API_KEY"]

    def override(self, **kw: Any) -> "Config":
        """Return a copy with top-level CLI overrides applied (highest priority)."""
        model = self.model
        schanges: dict[str, Any] = {}
        if kw.get("model"):
            model = replace(model, default=kw["model"])
        if kw.get("port"):
            schanges["port"] = kw["port"]
        if kw.get("host"):
            schanges["host"] = kw["host"]
        if kw.get("base_url"):
            schanges["base_url"] = kw["base_url"]
        server = replace(self.server, **schanges) if schanges else self.server
        achanges: dict[str, Any] = {}
        if kw.get("max_iterations"):
            achanges["max_iterations"] = kw["max_iterations"]
        if kw.get("max_wallclock"):
            achanges["max_wallclock_seconds"] = kw["max_wallclock"]
        # `is not None`, not truthiness: 0 is a meaningful value here (it turns
        # the progress extension off) and must not be swallowed as "unset".
        if kw.get("progress_grant") is not None:
            achanges["progress_grant_seconds"] = kw["progress_grant"]
        agent = replace(self.agent, **achanges) if achanges else self.agent
        return replace(self, model=model, server=server, agent=agent)

    @property
    def base_url(self) -> str:
        return self.server.endpoint()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError):
        # A broken config shouldn't prevent startup; fall back to defaults.
        return {}


def _backend(data: dict[str, Any]) -> BackendConfig:
    """One [[serving.backends]] table -> BackendConfig, ignoring extra keys."""
    b = BackendConfig()
    _assign(b, data)
    return b


def _assign(obj: Any, data: dict[str, Any]) -> None:
    """Set only known dataclass fields from a TOML table; ignore extras."""
    known = obj.__dataclass_fields__
    for k, v in data.items():
        if k in known:
            setattr(obj, k, v)
