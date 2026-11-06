"""Binding, deterministic research screen (SPEC_v2). LLMs have no vote.

Pure comparisons are separate from committed-evidence authentication. Callers
of the live policy must first use engine.evidence.verify_evidence.
"""

import math
import re
from datetime import datetime

from engine.errors import EngineError
from engine.metrics import deflated_sharpe_from_moments
from engine.trials import canonical

POLICY = "quantlab-v2"
MODEL = "deterministic-python-v2"


def number(value, name, low=None, high=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise EngineError(f"{name}: expected finite number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise EngineError(f"{name}: out of bounds")
    return value


def validate_registration(blocks):
    params, grid, criteria = blocks["params"], blocks["grid"], blocks["criteria"]
    if (
        not isinstance(params, dict)
        or not params
        or not isinstance(grid, list)
        or len(grid) > 2
    ):
        raise EngineError("invalid parameter registration")
    for setting in [params, *grid]:
        if not isinstance(setting, dict) or set(setting) != set(params):
            raise EngineError("parameter grid must have same keys")
        for key, value in setting.items():
            if not isinstance(key, str) or not key:
                raise EngineError("invalid parameter key")
            number(value, f"parameter {key}")
    if len({canonical(p) for p in [params, *grid]}) != len(grid) + 1:
        raise EngineError("duplicate pre-registered settings")
    expected = {
        "min_sharpe",
        "max_drawdown",
        "min_hit_rate",
        "min_sign_consistent_folds",
    }
    if not isinstance(criteria, dict) or set(criteria) != expected:
        raise EngineError("invalid criteria keys")
    number(criteria["min_sharpe"], "min_sharpe", 0)
    number(criteria["max_drawdown"], "max_drawdown", 0, 1)
    number(criteria["min_hit_rate"], "min_hit_rate", 0, 1)
    folds = criteria["min_sign_consistent_folds"]
    if type(folds) is not int or not 1 <= folds <= 4:
        raise EngineError("invalid fold threshold")


def validate_iteration(blocks, results):
    iteration = results["iteration"]
    if type(iteration) is not int or not 0 <= iteration <= 2:
        raise EngineError("invalid iteration")
    settings = [blocks["params"], *blocks["grid"]]
    expected = [
        {"iteration": i, "params": p} for i, p in enumerate(settings[: iteration + 1])
    ]
    if len(expected) != iteration + 1 or canonical(
        results["iteration_history"]
    ) != canonical(expected):
        raise EngineError("inconsistent iteration history/order")
    if canonical(results["params"]) != canonical(expected[-1]["params"]):
        raise EngineError("inconsistent evaluated parameters")


def decide(blocks: dict, results: dict, *, historical=False) -> dict:
    """Evaluate a verified result; historical mode is non-authorizing analysis.

    Missing/malformed evidence raises EngineError (infrastructure abstention),
    never a merit KILL or permission to try a different setting.
    """
    try:
        return _decide(blocks, results, historical=historical)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise EngineError(f"malformed decision evidence: {exc}") from exc


def _decide(blocks, results, *, historical):
    validate_registration(blocks)
    validate_iteration(blocks, results)
    if results["status"] != "ok" or not re.fullmatch(
        r"H\d{3,}", results["hypothesis_id"]
    ):
        raise EngineError("invalid result identity/status")
    if not historical and results["protocol"] != POLICY:
        raise EngineError("historical/unrecognized protocol cannot authorize promotion")
    timestamp = datetime.fromisoformat(results["generated_at"])
    if timestamp.tzinfo is None:
        raise EngineError("result timestamp must be timezone-aware")
    agg, folds, ref = results["aggregate"], results["folds"], results["referee_inputs"]
    n = agg["n_days"]
    if type(n) is not int or n < 2:
        raise EngineError("invalid sample size")
    sr = number(agg["sharpe_daily"], "daily Sharpe")
    ann = number(agg["sharpe_annualized"], "annual Sharpe")
    if not math.isclose(ann, sr * math.sqrt(365), rel_tol=1e-12, abs_tol=1e-12):
        raise EngineError("inconsistent annual/daily Sharpe")
    number(agg["max_drawdown"], "drawdown", 0, 1)
    number(agg["hit_rate"], "hit rate", 0, 1)
    if (
        len(folds) != 4
        or any(type(f["fold"]) is not int for f in folds)
        or [f["fold"] for f in folds] != [1, 2, 3, 4]
    ):
        raise EngineError("invalid folds")
    if (
        any(type(f["n_days"]) is not int or f["n_days"] < 2 for f in folds)
        or sum(f["n_days"] for f in folds) != n
    ):
        raise EngineError("inconsistent fold coverage")
    consistent = sum(
        (number(f["sharpe_annualized"], "fold Sharpe") > 0) == (ann > 0) for f in folds
    )
    dsr = deflated_sharpe_from_moments(
        sr,
        n,
        agg["skew"],
        agg["kurtosis_raw"],
        ref["n_trials"],
        ref["trial_sharpes_daily"],
        variance_floor=0 if historical else 1 / (n - 1),
    )
    if not math.isclose(
        number(ref["deflated_sharpe"], "DSR", 0, 1), dsr, abs_tol=1e-12, rel_tol=1e-12
    ):
        raise EngineError("inconsistent multiple-testing adjustment")
    if not historical and (
        ref["pending_attempts"] != 0
        or ref["effective_independent_trials"] is not None
        or ref["method"] != "all-configurations-dsr-variance-floor-v2"
    ):
        raise EngineError("unresolved or invalid trial accounting")
    c = blocks["criteria"]
    checks = []
    for name, value, threshold, op in [
        ("min_sharpe", ann, c["min_sharpe"], ">="),
        ("max_drawdown", agg["max_drawdown"], c["max_drawdown"], "<="),
        ("min_hit_rate", agg["hit_rate"], c["min_hit_rate"], ">="),
        ("stability", consistent, c["min_sign_consistent_folds"], ">="),
        ("deflated_sharpe", dsr, 0.95, ">="),
    ]:
        checks.append(
            {
                "name": name,
                "required": f"{op} {threshold}",
                "observed": value,
                "pass": bool(value >= threshold if op == ">=" else value <= threshold),
            }
        )
    if all(c["pass"] for c in checks):
        outcome, next_params = "PROMOTE", None
    elif results["iteration"] >= len(blocks["grid"]):
        outcome, next_params = "KILL", None
    else:
        outcome, next_params = "ITERATE", blocks["grid"][results["iteration"]]
    return {
        "hypothesis_id": results["hypothesis_id"],
        "iteration": results["iteration"],
        "decision": outcome,
        "kill_reason": "merits" if outcome == "KILL" else None,
        "criteria": checks,
        "iterate_params": next_params,
        "justification": "; ".join(
            f"{c['name']}={c['observed']} required {c['required']}: {c['pass']}"
            for c in checks
        ),
        "referee_model": MODEL,
        "timestamp": results["generated_at"],
    }
