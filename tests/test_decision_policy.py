import copy
import json
from pathlib import Path

import pytest

from engine.errors import EngineError
from engine.metrics import deflated_sharpe_from_moments
from engine.policy import decide, MODEL
from orchestrator.stages import StageFailure, validate_decision, validate_hypothesis_md

ROOT = Path(__file__).resolve().parents[1]


def fixture(iteration=2):
    blocks = validate_hypothesis_md(
        (ROOT / "hypotheses/H001/hypothesis.md").read_text()
    )
    result = json.loads(
        (ROOT / f"tests/fixtures/H001_iteration_{iteration}.json").read_text()
    )
    scores = [
        json.loads((ROOT / f"tests/fixtures/H001_iteration_{i}.json").read_text())[
            "aggregate"
        ]["sharpe_daily"]
        for i in range(iteration + 1)
    ]
    result["protocol"] = "quantlab-v2"
    result["referee_inputs"].update(
        n_trials=iteration + 1,
        trial_sharpes_daily=scores,
        pending_attempts=0,
        effective_independent_trials=None,
        method="all-configurations-dsr-variance-floor-v2",
    )
    refresh(result)
    return blocks, result


def refresh(r):
    a, ref = r["aggregate"], r["referee_inputs"]
    ref["deflated_sharpe"] = deflated_sharpe_from_moments(
        a["sharpe_daily"],
        a["n_days"],
        a["skew"],
        a["kurtosis_raw"],
        ref["n_trials"],
        ref["trial_sharpes_daily"],
        variance_floor=1 / (a["n_days"] - 1),
    )


def validate(d, b, r):
    return validate_decision(
        json.dumps(d),
        b["grid"],
        r["iteration_history"],
        MODEL,
        trusted_results=r,
        hypothesis_blocks=b,
    )


def test_h001_financially_false_promotion_rejected():
    b, r = fixture()
    expected = decide(b, r)
    assert expected["decision"] == "KILL"
    false = {**expected, "decision": "PROMOTE", "kill_reason": None}
    with pytest.raises(StageFailure, match="deterministic"):
        validate(false, b, r)


@pytest.mark.parametrize(
    "field,value", [("observed", 999), ("pass", True), ("required", ">= -999")]
)
def test_forged_criteria(field, value):
    b, r = fixture()
    d = decide(b, r)
    d["criteria"][0][field] = value
    with pytest.raises(StageFailure, match="criteria"):
        validate(d, b, r)


def test_iteration_order_and_premature_kill():
    b, r = fixture(0)
    d = decide(b, r)
    assert d["decision"] == "ITERATE" and d["iterate_params"] == b["grid"][0]
    validate(d, b, r)
    with pytest.raises(StageFailure):
        validate(
            {**d, "decision": "KILL", "kill_reason": "merits", "iterate_params": None},
            b,
            r,
        )
    r["iteration_history"][0]["iteration"] = 1
    with pytest.raises(EngineError, match="history"):
        decide(b, r)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sharpe_daily", float("nan")),
        ("max_drawdown", -1),
        ("hit_rate", True),
        ("n_days", 0),
        ("sharpe_annualized", 20),
    ],
)
def test_invalid_metrics_fail_closed(field, value):
    b, r = fixture()
    r["aggregate"][field] = value
    with pytest.raises(EngineError):
        decide(b, r)


def test_no_evidence_never_authorizes():
    b, r = fixture()
    with pytest.raises(StageFailure, match="trusted metric"):
        validate_decision(
            json.dumps(decide(b, r)), b["grid"], r["iteration_history"], MODEL
        )
    for modify in [
        lambda x: x.pop("aggregate"),
        lambda x: x["folds"].pop(),
        lambda x: x["referee_inputs"].update(deflated_sharpe=1),
        lambda x: x["referee_inputs"].update(pending_attempts=1),
        lambda x: x.update(protocol="v1"),
    ]:
        bad = copy.deepcopy(r)
        modify(bad)
        with pytest.raises(EngineError):
            decide(b, bad)


def test_positive_metrics_promote_and_failure_iterates():
    b, r = fixture(0)
    r["aggregate"].update(
        sharpe_daily=0.2,
        sharpe_annualized=0.2 * 365**0.5,
        max_drawdown=0.1,
        hit_rate=0.6,
    )
    for fold in r["folds"]:
        fold["sharpe_annualized"] = 2
    r["referee_inputs"]["trial_sharpes_daily"] = [0.2]
    refresh(r)
    assert decide(b, r)["decision"] == "PROMOTE"
    r["folds"][0]["sharpe_annualized"] = -1
    r["folds"][1]["sharpe_annualized"] = -1
    assert decide(b, r)["decision"] == "ITERATE"


def test_duplicate_keys_and_nonfinite_registration_rejected():
    from orchestrator import dryrun
    text = dryrun.render(dryrun.CANNED[0]['hypothesis_md'],hid='H001',ts='2026-11-05')
    for bad in [text.replace('"min_sharpe": 1.0', '"min_sharpe": NaN'),
                text.replace('"min_sharpe": 1.0', '"min_sharpe": 1.0, "min_sharpe": 0.0')]:
        with pytest.raises(StageFailure):
            validate_hypothesis_md(bad)


def test_identical_trial_scores_do_not_disable_v2_deflation():
    scores = [.05]*3
    legacy = deflated_sharpe_from_moments(.05,100,0,3,3,scores)
    corrected = deflated_sharpe_from_moments(.05,100,0,3,3,scores,variance_floor=1/99)
    assert corrected < legacy
