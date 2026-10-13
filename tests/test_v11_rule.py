import json
import math
import pytest
from orchestrator import rule

def _results(hid="H002", iteration=0, params=None, sharpe=-1.0, dd=0.5, hit=0.4,
             folds=(-1.0, -1.0, -1.0, -1.0), dsr=0.01, history=None, status="ok"):
    params = params or {"lookback_days": 90}
    return {
        "hypothesis_id": hid, "iteration": iteration, "params": params,
        "status": status, "generated_at": "2026-10-08T00:00:00+00:00",
        "iteration_history": history or [{"iteration": 0, "params": params}],
        "aggregate": {"sharpe_annualized": sharpe, "sharpe_daily": sharpe / math.sqrt(365),
                      "max_drawdown": dd, "hit_rate": hit, "n_days": 973},
        "folds": [{"sharpe_annualized": f} for f in folds],
        "referee_inputs": {"deflated_sharpe": dsr, "n_trials": 1},
        "engine": {"git_commit": "abc", "data_manifest_sha256": "def"},
    }


BLOCKS = {"params": {"lookback_days": 90},
          "grid": [{"lookback_days": 60}, {"lookback_days": 120}],
          "criteria": {"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
                       "min_sign_consistent_folds": 3}}
PASSING = dict(sharpe=1.5, dd=0.2, hit=0.55, folds=(1.0, 1.2, 0.8, 1.1), dsr=0.97)


def test_rule_matches_spec_decisions():
    assert rule.decide(BLOCKS, _results())["decision"] == "ITERATE"
    assert rule.decide(BLOCKS, _results())["iterate_params"] == {"lookback_days": 60}
    assert rule.decide(BLOCKS, _results(**PASSING))["decision"] == "PROMOTE"
    hist = [{"iteration": 0, "params": {"lookback_days": 90}},
            {"iteration": 1, "params": {"lookback_days": 60}},
            {"iteration": 2, "params": {"lookback_days": 120}}]
    d = rule.decide(BLOCKS, _results(iteration=2, params={"lookback_days": 120}, history=hist))
    assert (d["decision"], d["kill_reason"], d["iterate_params"]) == ("KILL", "merits", None)
    assert d["referee_model"] == rule.RULE_ID


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.pop("folds"), "lack 'folds'"),
    (lambda r: r["aggregate"].update(sharpe_annualized=float("nan")), "not finite"),
    (lambda r: r["aggregate"].update(hit_rate="0.5"), "not a number"),
    (lambda r: r["referee_inputs"].pop("deflated_sharpe"), "deflated_sharpe"),
    (lambda r: r.update(status="error: boom"), "not 'ok'"),
    (lambda r: r["folds"].pop(), "exactly 4"),
    (lambda r: r.update(iteration_history=[{"iteration": 0, "params": {"lookback_days": 90}},
                                           {"iteration": 1, "params": {"lookback_days": 45}}],
                        iteration=1, params={"lookback_days": 45}), "not grid point 1 in declared order"),
    (lambda r: r.update(iteration=1), "has 1 entries for iteration 1"),
])
def test_rule_refuses_bad_inputs_instead_of_judging(mutate, match):
    r = _results()
    mutate(r)
    with pytest.raises(rule.RuleInputError, match=match):
        rule.decide(BLOCKS, r)


def _hist(*params):
    return [{"iteration": i, "params": p} for i, p in enumerate(params)]


@pytest.mark.parametrize("name, blocks_mut, results_mut", [
    ("history[0] differs from pre-registered params", None,
     lambda r: r.update(iteration_history=_hist({"lookback_days": 45}))),
    ("history skips grid points", None,
     lambda r: r.update(iteration=1, params={"lookback_days": 120},
                        iteration_history=_hist({"lookback_days": 90}, {"lookback_days": 120}))),
    ("history length mismatches iteration", None,
     lambda r: r.update(iteration=2, params={"lookback_days": 90})),
    ("iteration > 2", None,
     lambda r: r.update(iteration=3, params={"lookback_days": 90},
                        iteration_history=_hist({"lookback_days": 90}, {"lookback_days": 60},
                                                {"lookback_days": 120}, {"lookback_days": 90}))),
    ("results.params != last history entry", None,
     lambda r: r.update(params={"lookback_days": 60})),
    ("hypothesis_id empty", None, lambda r: r.update(hypothesis_id="")),
    ("grid is a string", lambda b: b.update(grid="[]"), None),
    ("min_sign_consistent_folds = 7", lambda b: b["criteria"].update(min_sign_consistent_folds=7), None),
    ("min_sign_consistent_folds = 2.5", lambda b: b["criteria"].update(min_sign_consistent_folds=2.5), None),
    ("negative max_drawdown criterion", lambda b: b["criteria"].update(max_drawdown=-0.1), None),
    ("deflated_sharpe = 7.0", None, lambda r: r["referee_inputs"].update(deflated_sharpe=7.0)),
    ("hit_rate = 1.7", None, lambda r: r["aggregate"].update(hit_rate=1.7)),
    ("history entry without params", None,
     lambda r: r.update(iteration_history=[{"iteration": 0}])),
    ("non-dict history entry", None, lambda r: r.update(iteration_history=["x"])),
    ("aggregate is None", None, lambda r: r.update(aggregate=None)),
    ("folds are strings", None, lambda r: r.update(folds=["a", "b", "c", "d"])),
    ("blocks is None", "none", None),
])
def test_rule_raises_rule_input_error_on_every_inconsistent_input(name, blocks_mut, results_mut):
    import copy
    blocks = None if blocks_mut == "none" else copy.deepcopy(BLOCKS)
    if callable(blocks_mut):
        blocks_mut(blocks)
    results = _results()
    if results_mut:
        results_mut(results)
    with pytest.raises(rule.RuleInputError):
        rule.decide(blocks, results)


def test_rule_accepts_a_full_valid_iteration_walk():
    hist = _hist({"lookback_days": 90}, {"lookback_days": 60}, {"lookback_days": 120})
    d = rule.decide(BLOCKS, _results(iteration=2, params={"lookback_days": 120}, history=hist))
    assert d["decision"] == "KILL"


