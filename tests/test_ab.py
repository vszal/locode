"""Tests for the paired same-session A/B (evals/ab.py).

This tool's whole job is to say "the change helped" or "it didn't", from very
few runs. The ways it can be wrong are all quiet: a pair whose two arms ran the
same code, an ungraded run substituted with a zero, an arm that always went
second on a warm box, a p-value that could never have reached alpha. Each of
those is pinned here.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


harness = _load("eval_harness", ROOT / "evals" / "harness.py")
ab = _load("eval_ab", ROOT / "evals" / "ab.py")


def _run(case="c", repeat=1, arm="base", score=1.0, invalid=""):
    return harness.RunResult(
        case=case, track="execute", model="m", repeat=repeat, score=score,
        checks={"ok": score >= 1.0}, metrics={}, returncode=0, timed_out=False,
        seconds=1.0, workdir="/tmp/x", invalid=invalid, arm=arm)


# --- pairing --------------------------------------------------------------

def test_pairs_are_matched_on_case_model_and_repeat():
    runs = [_run(repeat=1, arm="base", score=0.5),
            _run(repeat=1, arm="cand", score=1.0),
            _run(repeat=2, arm="cand", score=0.0),
            _run(repeat=2, arm="base", score=0.5)]
    pairs, dropped = ab.pair_runs(runs)
    assert dropped == []
    assert [(p["repeat"], p["delta"]) for p in pairs] == [(1, 0.5), (2, -0.5)]


def test_a_pair_with_an_ungraded_arm_is_dropped_not_zeroed():
    # The failure this prevents: an infra-killed baseline run scoring 0.0 makes
    # the candidate look like a huge win.
    runs = [_run(repeat=1, arm="base", score=0.0, invalid="infrastructure: EOF"),
            _run(repeat=1, arm="cand", score=1.0)]
    pairs, dropped = ab.pair_runs(runs)
    assert pairs == []
    assert len(dropped) == 1
    assert "infrastructure" in dropped[0]["why"]


def test_a_half_finished_pair_is_dropped():
    pairs, dropped = ab.pair_runs([_run(repeat=1, arm="base")])
    assert pairs == []
    assert "incomplete pair" in dropped[0]["why"]


def test_runs_of_different_cases_do_not_pair_with_each_other():
    runs = [_run(case="a", arm="base"), _run(case="b", arm="cand")]
    pairs, dropped = ab.pair_runs(runs)
    assert pairs == []
    assert len(dropped) == 2


# --- the sign-flip test ---------------------------------------------------

def test_no_difference_gives_p_of_one():
    assert ab.signflip_p([0.0, 0.0, 0.0, 0.0, 0.0, 0.0]) == 1.0


def test_a_unanimous_delta_hits_the_exact_floor():
    # Every pair favouring the candidate is the strongest evidence n pairs can
    # carry, so p must land exactly on 2/2**n — the two all-same-sign
    # assignments. This is what makes _MIN_PAIRS arithmetic rather than taste.
    for n in (6, 8, 10):
        assert ab.signflip_p([0.25] * n) == pytest.approx(2 / 2 ** n)


def test_the_test_is_two_sided():
    assert ab.signflip_p([0.25] * 8) == ab.signflip_p([-0.25] * 8)


def test_five_pairs_can_never_reach_alpha():
    # Not "usually won't" — cannot. The floor is above alpha.
    assert 2 / 2 ** 5 > ab._ALPHA
    assert ab.signflip_p([1.0] * 5) > ab._ALPHA


def test_ties_do_not_change_the_p_value():
    # A pair that scored the same on both arms cannot move a sign-flip test:
    # flipping the sign of zero does nothing. Padding with ties must therefore
    # leave p exactly where it was, not dilute it toward 1.
    assert ab.signflip_p([0.25] * 8) == ab.signflip_p([0.25] * 8 + [0.0] * 5)


def test_ties_do_not_count_toward_the_sample_size():
    assert ab.effective_pairs([0.5, 0.0, -0.25, 0.0, 0.0]) == 2


def test_a_run_padded_with_ties_is_inconclusive_not_significant():
    # The trap: 8 pairs, 5 unanimous wins, 3 ties. `n_pairs` clears _MIN_PAIRS,
    # but only 5 pairs moved and 2/2**5 = 0.0625 is above alpha — so the run
    # could not have produced a significant result no matter how it came out.
    a = ab.analyze(_pairs([0.5] * 5 + [0.0] * 3), [])
    assert a["n_pairs"] == 8 and a["n_effective"] == 5
    assert a["status"] == "inconclusive"
    assert "scored the same on both arms" in a["why"]


def test_a_mixed_sample_is_not_significant():
    assert ab.signflip_p([0.5, -0.5, 0.5, -0.5, 0.25, -0.25]) > ab._ALPHA


def test_the_sampled_branch_agrees_with_the_exact_one():
    # Above _EXACT_MAX_PAIRS the test switches to sampling; the switch must not
    # move the answer.
    deltas = [0.2] * 12 + [-0.1] * 8
    assert len(deltas) > ab._EXACT_MAX_PAIRS
    assert ab.signflip_p(deltas) < ab._ALPHA


# --- the verdict ----------------------------------------------------------

def _pairs(deltas):
    return [{"case": "c", "model": "m", "repeat": i + 1,
             "base": 0.5, "cand": 0.5 + d, "delta": d}
            for i, d in enumerate(deltas)]


def test_a_consistent_gain_is_called_improved():
    a = ab.analyze(_pairs([0.25] * 8), [])
    assert a["status"] == "improved"
    assert a["mean_delta"] == 0.25
    assert a["wins"] == 8


def test_a_consistent_loss_is_called_regressed():
    assert ab.analyze(_pairs([-0.25] * 8), [])["status"] == "regressed"


def test_noise_is_called_no_difference_not_improved():
    a = ab.analyze(_pairs([0.5, -0.5, 0.25, -0.25, 0.5, -0.5]), [])
    assert a["status"] == "no-difference"
    assert "not the same as equal" in a["why"]


def test_too_few_pairs_is_inconclusive_however_clean_the_split():
    a = ab.analyze(_pairs([1.0] * 4), [])
    assert a["status"] == "inconclusive"
    assert "4 informative pair" in a["why"]


def test_no_usable_pairs_is_inconclusive():
    a = ab.analyze([], [{"why": "ungraded"}] * 6)
    assert a["status"] == "inconclusive"
    assert a["mean_delta"] is None
    assert a["n_dropped"] == 6


def test_inconclusive_exits_two_and_regressed_exits_one():
    # 2 means "re-run bigger", 1 means "the candidate is worse" — a caller that
    # conflated them would revert a change the experiment never measured.
    assert ab._EXIT["inconclusive"] == 2
    assert ab._EXIT["regressed"] == 1
    assert ab._EXIT["improved"] == 0
    assert ab._EXIT["no-difference"] == 0


# --- interleaving ---------------------------------------------------------

class _C:
    def __init__(self, cid):
        self.id = cid


def test_arm_order_alternates_so_neither_arm_always_runs_second():
    plan = list(ab._plan([_C("a")], ["m"], 6))
    orders = [p[3] for p in plan]
    assert orders[0] != orders[1]
    assert sum(1 for o in orders if o[0] == "base") == 3
    assert sum(1 for o in orders if o[0] == "cand") == 3


def test_the_plan_covers_every_case_model_repeat():
    plan = list(ab._plan([_C("a"), _C("b")], ["m1", "m2"], 3))
    assert len(plan) == 12
    assert len({(c.id, m, r) for c, m, r, _ in plan}) == 12


# --- the identical-arms guard --------------------------------------------

def test_tree_digest_distinguishes_two_trees(tmp_path):
    a, b = tmp_path / "a" / "locode", tmp_path / "b" / "locode"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "m.py").write_text("x = 1\n")
    (b / "m.py").write_text("x = 2\n")
    assert ab.tree_digest(a.parent) != ab.tree_digest(b.parent)


def test_tree_digest_is_stable_for_identical_content(tmp_path):
    a, b = tmp_path / "a" / "locode", tmp_path / "b" / "locode"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    for d in (a, b):
        (d / "m.py").write_text("x = 1\n")
    assert ab.tree_digest(a.parent) == ab.tree_digest(b.parent)


def test_tree_digest_notices_a_renamed_file(tmp_path):
    # Content-only hashing would call these two trees the same.
    a, b = tmp_path / "a" / "locode", tmp_path / "b" / "locode"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "one.py").write_text("x = 1\n")
    (b / "two.py").write_text("x = 1\n")
    assert ab.tree_digest(a.parent) != ab.tree_digest(b.parent)


# --- the launcher ---------------------------------------------------------

LAUNCHER = ROOT / "evals" / "_agent_launcher.py"


def _launch(*args):
    return subprocess.run([sys.executable, str(LAUNCHER), *args],
                          capture_output=True, text=True, timeout=120)


def test_launcher_refuses_a_root_with_no_locode_package(tmp_path):
    p = _launch(str(tmp_path))
    assert p.returncode == 2
    assert "no locode package" in p.stderr


def test_launcher_refuses_with_no_arguments():
    assert _launch().returncode == 2


def test_launcher_runs_the_cli_from_the_requested_tree():
    p = _launch(str(ROOT), "--help")
    assert p.returncode == 0
    assert "locode" in p.stdout.lower()


# --- the harness side ----------------------------------------------------

def test_a_run_that_logged_no_events_is_invalid_not_a_zero():
    # The A/B's worst case: the launcher refuses, the agent never starts, the
    # checker grades an untouched workdir 0.0 — and every process metric reads
    # clean, because there is nothing to read.
    why = harness._invalidity(error="", checks={"ok": False}, has_checker=True,
                              infra_error=None,
                              launch_error="launch: the agent logged no events")
    assert harness._invalid_kind(why) == "launch"


def test_launch_failure_outranks_every_other_reason():
    why = harness._invalidity(error="checker raised: X", checks={},
                              has_checker=False, infra_error="EOF",
                              launch_error="launch: nope")
    assert why == "launch: nope"


def test_a_normal_run_is_unaffected_by_the_new_field():
    assert harness._invalidity(error="", checks={"ok": True}, has_checker=True,
                               infra_error=None) == ""


def test_run_result_defaults_arm_to_empty_so_old_results_still_load():
    raw = {"case": "c", "track": "t", "model": "m", "repeat": 1, "score": 1.0,
           "checks": {}, "metrics": {}, "returncode": 0, "timed_out": False,
           "seconds": 1.0, "workdir": "/tmp/x"}
    assert harness.RunResult(**raw).arm == ""


# --- the A/A noise floor (2026-08-07) --------------------------------------
# Earned the hard way: a real sweep read "+0.375, p=0.031, ✅ IMPROVED" and an
# A/A calibration of the SAME setup — identical code in both arms — returned
# +0.281. Five n=8 samples of one build spanned 0.438 to 0.812. The sign-flip
# test is not wrong about the signs; it cannot know the score is coarse enough
# that identical code already produces a delta that size.

@pytest.fixture
def floor(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "NOISE_FLOOR", tmp_path / "noise_floor.json")
    return tmp_path / "noise_floor.json"


def _improved(delta):
    return {"mean_delta": delta, "status": "improved", "why": ""}


def test_an_uncalibrated_setup_cannot_claim_an_improvement(floor):
    a = ab.apply_noise_floor(_improved(0.9), "k")
    assert a["status"] == "inconclusive"
    assert a["calibrated"] is False
    assert "--allow-identical" in a["why"]


def test_the_exact_result_that_prompted_this_gate_is_refused(floor):
    ab.record_calibration("k", 0.2812, "aa1")
    ab.record_calibration("k", 0.0625, "aa2")
    a = ab.apply_noise_floor(_improved(0.375), "k")
    assert a["status"] == "inconclusive"
    assert a["noise_floor"] == 0.2812 and a["noise_floor_n"] == 2


def test_a_delta_under_the_floor_is_refused(floor):
    ab.record_calibration("k", 0.2812, "aa1")
    a = ab.apply_noise_floor(_improved(0.2), "k")
    assert a["status"] == "inconclusive"
    assert "does not clear" in a["why"]


def test_a_big_enough_delta_still_gets_through(floor):
    ab.record_calibration("k", 0.2812, "aa1")
    ab.record_calibration("k", 0.0625, "aa2")
    assert ab.apply_noise_floor(_improved(0.9), "k")["status"] == "improved"


def test_a_regression_is_gated_the_same_way(floor):
    ab.record_calibration("k", 0.2812, "aa1")
    ab.record_calibration("k", 0.0625, "aa2")
    assert ab.apply_noise_floor(
        {"mean_delta": -0.3, "status": "regressed", "why": ""},
        "k")["status"] == "inconclusive"
    assert ab.apply_noise_floor(
        {"mean_delta": -0.9, "status": "regressed", "why": ""},
        "k")["status"] == "regressed"


def test_more_calibration_runs_relax_the_margin(floor):
    for x in (0.2812, 0.0625):
        ab.record_calibration("k", x, "aa")
    assert ab.apply_noise_floor(_improved(0.375), "k")["status"] == "inconclusive"
    for x in (0.10, 0.15, 0.09, 0.12):
        ab.record_calibration("k", x, "aa")
    a = ab.apply_noise_floor(_improved(0.375), "k")
    assert a["noise_floor"] == 0.2812  # unchanged: still the largest draw
    assert a["status"] == "improved"   # but 6 draws no longer demand 2x


def test_a_non_claiming_status_is_left_alone(floor):
    ab.record_calibration("k", 0.9, "aa")
    a = ab.apply_noise_floor(
        {"mean_delta": 0.01, "status": "no-difference", "why": "w"}, "k")
    assert a["status"] == "no-difference" and a["why"] == "w"


def test_calibration_records_absolute_values_and_accumulates(floor):
    ab.record_calibration("k", -0.25, "aa1")
    ab.record_calibration("k", 0.125, "aa2")
    import json
    row = json.loads(floor.read_text())["k"]
    assert row["samples"] == [0.25, 0.125]
    assert row["labels"] == ["aa1", "aa2"]


def test_the_floor_is_keyed_per_setup(floor):
    ab.record_calibration("exec-bugfix|m|r8", 0.05, "aa")
    assert ab.apply_noise_floor(_improved(0.9), "e2e|m|r8")["calibrated"] is False
    assert ab.apply_noise_floor(_improved(0.9), "exec-bugfix|m|r8")["status"] == "improved"


def test_floor_key_is_stable_regardless_of_argument_order():
    class C:
        def __init__(self, i): self.id = i
    a = ab.floor_key([C("b"), C("a")], ["z", "y"], 8)
    b = ab.floor_key([C("a"), C("b")], ["y", "z"], 8)
    assert a == b == "a,b|y,z|r8"


def test_a_missing_mean_delta_is_not_treated_as_a_result(floor):
    ab.record_calibration("k", 0.05, "aa")
    a = ab.apply_noise_floor({"mean_delta": None, "status": "inconclusive",
                              "why": ""}, "k")
    assert a["status"] == "inconclusive"


# --- an A/A must survive its own first checkpoint (build 100) --------------
# _persist runs after EVERY run so a killed sweep keeps its partial results.
# The first call therefore has one run banked and no complete pair, and the
# A/A branch formatted mean_delta (None) unconditionally: every A/A sweep died
# on run 1 with a TypeError. That is why the standing 0.2812 floor rests on 2
# samples — no calibration had been able to finish since this call site landed.

def test_an_aa_checkpoint_survives_having_no_complete_pair(tmp_path):
    report = ab._persist(tmp_path, [_run(arm="base")], "l", "ref", "sha", None,
                         key="k", identical=True)
    a = report["analysis"]
    assert a["mean_delta"] is None
    assert a["status"] == "inconclusive"
    assert "no complete pair yet" in a["why"]


def test_a_complete_aa_pair_still_reports_the_delta(tmp_path):
    runs = [_run(arm="base", score=0.5), _run(arm="cand", score=1.0)]
    report = ab._persist(tmp_path, runs, "l", "ref", "sha", None,
                         key="k", identical=True)
    a = report["analysis"]
    assert a["mean_delta"] == 0.5
    assert "IS the noise floor" in a["why"]


def test_a_partial_ab_checkpoint_also_survives(tmp_path):
    # The non-identical path reaches apply_noise_floor with the same None.
    report = ab._persist(tmp_path, [_run(arm="cand")], "l", "ref", "sha", None,
                         key="k", identical=False)
    assert report["analysis"]["mean_delta"] is None
