"""The SPEC §8 decision rule, as code (SPEC v1.1 §10, §13).

Since v1.1 this function is the AUTHORITATIVE S4 decision in every mode.
The LLM referee still runs in live mode, as a secondary re-derivation
whose agreement is recorded (hypotheses/H###/referee_llm.json) but which
can never alter the decision.

Inputs are validated first. Missing keys, non-finite numbers, or an
iteration history inconsistent with the declared grid raise
RuleInputError — an explicit error, never a scientific verdict.
"""

import math

RULE_ID = "rule:spec-v1.1"
DSR_THRESHOLD = 0.95


class RuleInputError(ValueError):
    """Required rule inputs are missing, inconsistent, or non-finite."""


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleInputError(f"{name} is not a number: {value!r}")
    if not math.isfinite(value):
        raise RuleInputError(f"{name} is not finite: {value!r}")
    return float(value)


def _validate_inputs_strict(blocks, results) -> None:
    if not isinstance(blocks, dict) or not isinstance(results, dict):
        raise RuleInputError("blocks and results must be dicts")
    for key in ("params", "criteria", "grid"):
        if key not in blocks:
            raise RuleInputError(f"hypothesis blocks lack {key!r}")
    params0, crit, grid = blocks["params"], blocks["criteria"], blocks["grid"]
    if not isinstance(params0, dict) or not params0:
        raise RuleInputError("blocks.params must be a non-empty dict")
    if not isinstance(crit, dict):
        raise RuleInputError("blocks.criteria must be a dict")
    if not isinstance(grid, list) or len(grid) > 2 or not all(isinstance(g, dict) for g in grid):
        raise RuleInputError("blocks.grid must be a list of <=2 dicts")
    for point in grid:
        if set(point) != set(params0):
            raise RuleInputError("grid parameter keys differ from iteration 0")
    def validate_params(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise RuleInputError("non-finite parameter")
        if isinstance(value, dict):
            for item in value.values():
                validate_params(item)
        elif isinstance(value, list):
            for item in value:
                validate_params(item)
    validate_params(params0)
    validate_params(grid)
    for key in ("min_sharpe", "max_drawdown", "min_hit_rate", "min_sign_consistent_folds"):
        if key not in crit:
            raise RuleInputError(f"kill criteria lack {key!r}")
        _finite(crit[key], f"criteria.{key}")
    if not (0.0 <= crit["max_drawdown"] <= 1.0):
        raise RuleInputError(f"criteria.max_drawdown out of [0, 1]: {crit['max_drawdown']!r}")
    if not (0.0 <= crit["min_hit_rate"] <= 1.0):
        raise RuleInputError(f"criteria.min_hit_rate out of [0, 1]: {crit['min_hit_rate']!r}")
    k = crit["min_sign_consistent_folds"]
    if isinstance(k, bool) or int(k) != k or not (0 <= k <= 4):
        raise RuleInputError(f"criteria.min_sign_consistent_folds must be an integer in [0, 4]: {k!r}")

    if results.get("status") != "ok":
        raise RuleInputError(f"results.status is {results.get('status')!r}, not 'ok'")
    for key in ("aggregate", "folds", "referee_inputs", "iteration", "iteration_history",
                "hypothesis_id", "params"):
        if key not in results:
            raise RuleInputError(f"results lack {key!r}")
    if not isinstance(results["hypothesis_id"], str) or not results["hypothesis_id"]:
        raise RuleInputError("results.hypothesis_id must be a non-empty string")
    agg = results["aggregate"]
    if not isinstance(agg, dict):
        raise RuleInputError("results.aggregate must be a dict")
    for key in ("sharpe_annualized", "max_drawdown", "hit_rate"):
        if key not in agg:
            raise RuleInputError(f"results.aggregate lacks {key!r}")
        _finite(agg[key], f"aggregate.{key}")
    if not (0.0 <= agg["max_drawdown"] <= 1.0):
        raise RuleInputError(f"aggregate.max_drawdown out of [0, 1]: {agg['max_drawdown']!r}")
    if not (0.0 <= agg["hit_rate"] <= 1.0):
        raise RuleInputError(f"aggregate.hit_rate out of [0, 1]: {agg['hit_rate']!r}")
    folds = results["folds"]
    if not isinstance(folds, list) or len(folds) != 4:
        raise RuleInputError("results.folds must have exactly 4 entries")
    for i, f in enumerate(folds, start=1):
        if not isinstance(f, dict) or "sharpe_annualized" not in f:
            raise RuleInputError(f"fold {i} lacks sharpe_annualized")
        _finite(f["sharpe_annualized"], f"folds[{i}].sharpe_annualized")
    ref = results["referee_inputs"]
    if not isinstance(ref, dict) or "deflated_sharpe" not in ref:
        raise RuleInputError("referee_inputs lack deflated_sharpe")
    dsr = _finite(ref["deflated_sharpe"], "referee_inputs.deflated_sharpe")
    if not (0.0 <= dsr <= 1.0):
        raise RuleInputError(f"referee_inputs.deflated_sharpe is a probability; got {dsr!r}")

    iteration = results["iteration"]
    if isinstance(iteration, bool) or not isinstance(iteration, int) or not (0 <= iteration <= 2):
        raise RuleInputError(f"results.iteration must be an integer in [0, 2]: {iteration!r}")
    history = results["iteration_history"]
    if not isinstance(history, list) or not history:
        raise RuleInputError("iteration_history must be a non-empty list")
    if len(history) != iteration + 1:
        raise RuleInputError(f"iteration_history has {len(history)} entries for iteration {iteration}")
    for i, entry in enumerate(history):
        if not isinstance(entry, dict) or "params" not in entry or "iteration" not in entry:
            raise RuleInputError(f"iteration_history[{i}] lacks iteration/params")
        if type(entry["iteration"]) is not int or entry["iteration"] != i:
            raise RuleInputError(f"iteration_history[{i}].iteration is {entry['iteration']!r}")
        if not isinstance(entry["params"], dict):
            raise RuleInputError(f"iteration_history[{i}].params is not a dict")
    if history[0]["params"] != params0:
        raise RuleInputError("iteration_history[0].params differs from the pre-registered iteration-0 params (R3)")
    # Follow FIRST untried grid entry, including grids containing a repeated
    # point or iteration-0 params (SPEC does not forbid those declarations).
    tried = [params0]
    for i, entry in enumerate(history[1:]):
        untried = [point for point in grid if point not in tried]
        if not untried or entry["params"] != untried[0]:
            raise RuleInputError(f"iteration_history[{i + 1}].params is not grid point {i + 1} in declared order (R3)")
        tried.append(entry["params"])
    if results["params"] != history[-1]["params"]:
        raise RuleInputError("results.params differs from the last iteration_history entry")


def validate_inputs(blocks, results) -> None:
    """Every defect in the inputs surfaces as RuleInputError — including
    shape errors that would otherwise raise KeyError/TypeError/etc. — so the
    loop can turn it into an infra-kill instead of crashing the run (R4)."""
    try:
        _validate_inputs_strict(blocks, results)
    except RuleInputError:
        raise
    except Exception as exc:  # noqa: BLE001 — any shape error is an input error
        raise RuleInputError(f"malformed rule inputs: {type(exc).__name__}: {exc}") from exc


def decide(blocks: dict, results: dict, referee_model: str = RULE_ID) -> dict:
    """SPEC §8 decision rule, implemented mechanically. The reference
    implementation for SPEC §13's acceptance test and, since v1.1, the
    authoritative decision (§10)."""
    validate_inputs(blocks, results)
    try:
        return _decide_validated(blocks, results, referee_model)
    except RuleInputError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuleInputError(f"rule evaluation failed on validated inputs: {type(exc).__name__}: {exc}") from exc


def _decide_validated(blocks: dict, results: dict, referee_model: str) -> dict:
    criteria = blocks["criteria"]
    grid = blocks["grid"]
    agg = results["aggregate"]
    folds = results["folds"]
    dsr = results["referee_inputs"]["deflated_sharpe"]
    iteration = results["iteration"]
    history = results["iteration_history"]

    agg_positive = agg["sharpe_annualized"] > 0
    consistent = sum(1 for f in folds if (f["sharpe_annualized"] > 0) == agg_positive)
    checks = [
        {"name": "min_sharpe", "required": f">= {criteria['min_sharpe']}",
         "observed": agg["sharpe_annualized"],
         "pass": agg["sharpe_annualized"] >= criteria["min_sharpe"]},
        {"name": "max_drawdown", "required": f"<= {criteria['max_drawdown']}",
         "observed": agg["max_drawdown"],
         "pass": agg["max_drawdown"] <= criteria["max_drawdown"]},
        {"name": "min_hit_rate", "required": f">= {criteria['min_hit_rate']}",
         "observed": agg["hit_rate"],
         "pass": agg["hit_rate"] >= criteria["min_hit_rate"]},
        {"name": "stability",
         "required": f">= {criteria['min_sign_consistent_folds']}/4 folds sign-consistent",
         "observed": f"{consistent}/4",
         "pass": consistent >= criteria["min_sign_consistent_folds"]},
        {"name": "deflated_sharpe", "required": f">= {DSR_THRESHOLD}",
         "observed": dsr, "pass": dsr >= DSR_THRESHOLD},
    ]

    tried = [h["params"] for h in history]
    untried = [g for g in grid if g not in tried]
    failed = ", ".join(f"{c['name']}={c['observed']} (need {c['required']})"
                       for c in checks if not c["pass"])

    if all(c["pass"] for c in checks):
        decision, iterate_params = "PROMOTE", None
        just = "All pre-registered criteria pass and DSR clears the SPEC threshold."
    elif iteration >= 2 or not untried:
        decision, iterate_params = "KILL", None
        just = f"Failed pre-registered criteria: {failed}. Grid exhausted or iteration cap reached."
    else:
        decision, iterate_params = "ITERATE", untried[0]
        just = f"Failed: {failed}. First untried pre-registered grid point: {untried[0]}."

    return {
        "hypothesis_id": results["hypothesis_id"],
        "iteration": iteration,
        "decision": decision,
        "kill_reason": "merits" if decision == "KILL" else None,
        "criteria": checks,
        "iterate_params": iterate_params,
        "justification": just,
        "referee_model": referee_model,
        "timestamp": results.get("generated_at"),
    }


def agreement(rule_decision: dict, llm_decision: dict) -> dict:
    """Compare a secondary (LLM) decision with the authoritative one."""
    same_decision = llm_decision.get("decision") == rule_decision["decision"]
    same_params = llm_decision.get("iterate_params") == rule_decision["iterate_params"]
    return {
        "agrees": bool(same_decision and same_params),
        "rule_decision": rule_decision["decision"],
        "llm_decision": llm_decision.get("decision"),
        "rule_iterate_params": rule_decision["iterate_params"],
        "llm_iterate_params": llm_decision.get("iterate_params"),
    }
