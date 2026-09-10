"""Tests for evals/aider_compare.py -- the locode-vs-aider harness comparison.

Nothing here spends a model or a GPU. What is tested is the part that decides
what the comparison *means*: the statistic, the alias resolution that keeps both
arms on the same weights, and the URL handling that keeps them pointed at the
same server. A bug in any of those produces a clean-looking table that is
answering the wrong question.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import aider_compare as A  # noqa: E402


# --------------------------------------------------------------------------
# 1. The statistic.
# --------------------------------------------------------------------------

def test_mcnemar_reproduces_the_archived_qwen38_result():
    """§5.160 reported p = 0.0034 on 13 discordant pairs splitting 12-1. If
    this function cannot reproduce a number already in the ROADMAP, it is the
    function that is wrong."""
    assert round(A.mcnemar_exact(12, 1), 4) == 0.0034


def test_mcnemar_is_one_when_there_is_nothing_to_test():
    """No discordant pairs is no evidence, which is p = 1.0 -- not a
    significant tie, and not a crash."""
    assert A.mcnemar_exact(0, 0) == 1.0


def test_mcnemar_is_symmetric_in_its_arguments():
    """It is two-sided: which harness won cannot change the p-value, only the
    direction reported alongside it."""
    for a, b in ((12, 1), (7, 3), (20, 0), (1, 0)):
        assert A.mcnemar_exact(a, b) == A.mcnemar_exact(b, a)


def test_an_even_split_is_never_significant():
    for n in (2, 6, 10, 30):
        assert A.mcnemar_exact(n // 2, n // 2) == 1.0


def test_more_lopsided_at_fixed_n_is_more_significant():
    assert A.mcnemar_exact(10, 0) < A.mcnemar_exact(9, 1) < A.mcnemar_exact(8, 2)


def test_a_single_discordant_pair_proves_nothing():
    """One case going one way is exactly a single coin flip. A comparison that
    called that significant would manufacture findings out of one exercise."""
    assert A.mcnemar_exact(1, 0) == 1.0
    assert A.mcnemar_exact(2, 0) == 0.5


# --------------------------------------------------------------------------
# 2. Both arms must reach the same server.
# --------------------------------------------------------------------------

def test_v1_and_origin_accept_either_form():
    """aider wants the /v1 root, locode wants the bare origin, and the user
    should not have to remember which."""
    for given in ("http://127.0.0.1:8081", "http://127.0.0.1:8081/",
                  "http://127.0.0.1:8081/v1", "http://127.0.0.1:8081/v1/"):
        assert A.v1(given) == "http://127.0.0.1:8081/v1"
        assert A.origin(given) == "http://127.0.0.1:8081"


def test_v1_and_origin_are_idempotent():
    assert A.v1(A.v1("http://h:1")) == A.v1("http://h:1")
    assert A.origin(A.origin("http://h:1/v1")) == A.origin("http://h:1/v1")


def test_a_hostname_containing_v1_is_not_mangled():
    assert A.origin("http://v1host:8081") == "http://v1host:8081"
    assert A.v1("http://v1host:8081") == "http://v1host:8081/v1"


# --------------------------------------------------------------------------
# 3. Both arms must run the same weights.
# --------------------------------------------------------------------------

def _config(tmp_path, body):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def test_alias_resolves_from_the_same_config_locode_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CONFIG", _config(tmp_path, (
        '[aliases]\nqwen38 = "org/Qwen3.8-27B"\nqwythos9 = "org/Qwythos-9B"\n')))
    assert A.resolve_alias("qwen38") == "org/Qwen3.8-27B"


def test_an_unknown_alias_exits_rather_than_guessing(tmp_path, monkeypatch):
    """Silently falling back would put the two arms on different weights while
    still printing a tidy comparison table."""
    monkeypatch.setattr(A, "CONFIG", _config(tmp_path, '[aliases]\na = "org/A"\n'))
    with pytest.raises(SystemExit) as exc:
        A.resolve_alias("nope")
    assert "nope" in str(exc.value)


def test_a_missing_config_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CONFIG", tmp_path / "absent.toml")
    with pytest.raises(SystemExit):
        A.resolve_alias("qwen38")


def test_server_probe_rejects_an_endpoint_not_serving_the_model(monkeypatch):
    """The worst failure this script has: comparing two harnesses on two
    different models and reporting it as a harness result."""
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": [{"id": "org/OTHER"}]}).encode()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(SystemExit) as exc:
        A.assert_server_serves("http://127.0.0.1:8081", "org/WANTED")
    assert "org/WANTED" in str(exc.value)


def test_server_probe_passes_when_the_model_is_listed(monkeypatch):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": [{"id": "org/WANTED"}]}).encode()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    A.assert_server_serves("http://127.0.0.1:8081", "org/WANTED")


def test_an_unreachable_server_exits_with_the_url(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as exc:
        A.assert_server_serves("http://127.0.0.1:8081", "org/WANTED")
    assert "8081" in str(exc.value)


# --------------------------------------------------------------------------
# 4. Both arms must be given the same task and the same test command.
# --------------------------------------------------------------------------

def test_stub_is_read_from_the_prompt_the_model_actually_gets():
    case = SimpleNamespace(id="polyglot-leap", prompt=(
        "Instructions here.\n\nImplement the above in `leap.py`. The file "
        "currently contains stubs.\n"))
    assert A._stub(case) == "leap.py"


def test_a_prompt_without_the_imperative_exits(monkeypatch):
    case = SimpleNamespace(id="polyglot-leap", prompt="just do something")
    with pytest.raises(SystemExit):
        A._stub(case)


def test_test_command_is_the_graders_own_minus_its_redirection():
    """aider's --test-cmd has to be the command `check.py` will run. If they
    drift, a model can be chasing a green run the grader never sees."""
    mod = SimpleNamespace(TEST_CMD="python3 -m pytest leap_test.py -q 2>&1",
                          SPEC_SHAS={"leap_test.py": "abc"})
    assert A._test_command(mod) == "python3 -m pytest leap_test.py -q"


def test_test_command_survives_a_prefixed_environment_variable():
    """The JavaScript track pins TZ, and that prefix must reach aider too."""
    mod = SimpleNamespace(
        TEST_CMD="TZ=UTC ./node_modules/.bin/jest leap.spec.js --ci --forceExit 2>&1",
        SPEC_SHAS={})
    assert A._test_command(mod).startswith("TZ=UTC ")
    assert "2>&1" not in A._test_command(mod)


def test_protected_files_come_from_the_graders_own_sha_list():
    """Whatever `spec_unmodified` guards is what aider must be given read-only,
    or the guard vetoes a run for a file aider was invited to edit."""
    mod = SimpleNamespace(TEST_CMD="x",
                          SPEC_SHAS={"paasio_test.py": "a", "test_utils.py": "b"})
    assert A._protected(mod) == ["paasio_test.py", "test_utils.py"]


def test_grader_module_loads_a_real_generated_case():
    """Integration: the constants really are importable off a generated case,
    which is the whole reason this stopped being a regex."""
    from evals.harness import discover_cases
    cases = [c for c in discover_cases(None)
             if c.id.startswith("polyglot-") and not c.id.startswith("polyglot-js-")]
    if not cases:
        pytest.skip("no generated polyglot cases -- run evals/polyglot.py")
    mod = A._grader_module(cases[0])
    assert A._test_command(mod).startswith("python3 -m pytest")
    assert A._protected(mod)


# --------------------------------------------------------------------------
# 5. Rule 99: a run that spent its whole budget is an unknown.
# --------------------------------------------------------------------------

def test_a_stalled_server_is_recorded_as_unknown_not_as_failure():
    """The failure this actually caught: the server accepted the request and
    returned zero characters for the full 600s. The turn ended in `error` with
    no stop_reason, so the precise signal was absent and the run was scored a
    flat 0.00 -- reading as "the model could not do it" when the truth is "we
    never found out". That is the exact confusion rule 99 exists to prevent."""
    assert A._budget_stopped(stop_reason="", seconds=600.2, budget=600)


def test_a_budget_stop_is_believed_when_the_events_say_so():
    assert A._budget_stopped(stop_reason="budget: wallclock", seconds=42.0, budget=600)


def test_no_progress_is_a_real_verdict_not_an_unknown():
    """`budget: no progress` means the loop stopped because the model had
    stopped doing anything, which is a finding about the model."""
    assert not A._budget_stopped(stop_reason="budget: no progress", seconds=42.0,
                                 budget=600)


def test_a_fast_clean_finish_is_not_an_unknown():
    assert not A._budget_stopped(stop_reason="done", seconds=188.0, budget=600)


# --------------------------------------------------------------------------
# 6. A wedged server must abort the run, not degrade it.
# --------------------------------------------------------------------------

def _fake_completion(monkeypatch, behaviour):
    import urllib.request

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"choices": [{"message": {}}]}).encode()

    def opener(req, timeout=None):
        if behaviour == "ok":
            return _Resp()
        if behaviour == "hang":
            raise TimeoutError("timed out")
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", opener)


def test_a_completing_server_is_alive(monkeypatch):
    _fake_completion(monkeypatch, "ok")
    assert A.server_alive("http://127.0.0.1:8081", "org/M")


def test_a_server_that_answers_but_never_completes_is_not_alive(monkeypatch):
    """The real failure: mlx_lm.server kept accepting connections and serving
    `/models` for seven hours while completing nothing, because one stalled
    generation blocks every request behind it. A `/models` probe calls that
    healthy; only a real completion catches it."""
    _fake_completion(monkeypatch, "hang")
    assert not A.server_alive("http://127.0.0.1:8081", "org/M")


def test_an_unreachable_server_is_not_alive(monkeypatch):
    _fake_completion(monkeypatch, "refused")
    assert not A.server_alive("http://127.0.0.1:8081", "org/M")


# --------------------------------------------------------------------------
# 7. Recovery from a wedge, and its limit.
# --------------------------------------------------------------------------

def test_boot_gives_up_after_its_retries_rather_than_looping(monkeypatch):
    """A wedge that a restart cannot clear must end the run. Retrying forever
    would spend the night proving the server is broken."""
    calls = []
    monkeypatch.setattr(A.subprocess, "run",
                        lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(A.time, "sleep", lambda *_: None)
    monkeypatch.setattr(A, "server_alive", lambda *a, **k: False)
    assert A.boot_server("qwen38", "http://127.0.0.1:8081", "org/M", tries=2) is False


def test_boot_stops_as_soon_as_the_server_answers(monkeypatch):
    seen = {"n": 0}

    def alive(*a, **k):
        seen["n"] += 1
        return seen["n"] >= 1

    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(A.time, "sleep", lambda *_: None)
    monkeypatch.setattr(A, "server_alive", alive)
    assert A.boot_server("qwen38", "http://127.0.0.1:8081", "org/M", tries=3) is True
    assert seen["n"] == 1


def test_recovery_lets_locode_own_the_server_it_is_starting(monkeypatch):
    """The arms run with LOCODE_MANAGE_SERVER=no so neither can restart the
    endpoint under the other. Recovery is the one place that must not inherit
    it, or the warm-up turn will decline to start anything."""
    envs = []

    def run(cmd, **kw):
        envs.append(kw.get("env") or {})
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("LOCODE_MANAGE_SERVER", "no")
    monkeypatch.setattr(A.subprocess, "run", run)
    monkeypatch.setattr(A.time, "sleep", lambda *_: None)
    monkeypatch.setattr(A, "server_alive", lambda *a, **k: True)
    A.boot_server("qwen38", "http://127.0.0.1:8081", "org/M")
    warm = [e for e in envs if e]
    assert warm and "LOCODE_MANAGE_SERVER" not in warm[-1]
