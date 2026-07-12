"""Unit tests for the orchestrator's stage validators (A5).

These are the merit-path gatekeepers: hypothesis cards, signal sources, and
referee decisions must satisfy SPEC §8b/§10/§13 exactly or the cycle
infra-kills. The dry-run loop routes its mechanical referee through the
same validate_decision as live codex output.
"""

import json

import pytest

from orchestrator import dryrun
from orchestrator.stages import (
    StageFailure,
    patch_params_line,
    validate_decision,
    validate_hypothesis_md,
    validate_signal_source,
)

GRID = [{"lookback_days": 60}, {"lookback_days": 120}]
HISTORY = [{"iteration": 0, "params": {"lookback_days": 90}}]
PINNED = "test"  # expected_referee_model used across these tests


def _decision(**overrides) -> dict:
    base = {
        "hypothesis_id": "H001", "iteration": 0, "decision": "KILL",
        "kill_reason": "merits", "criteria": [], "iterate_params": None,
        "justification": "fails pre-registered min_sharpe",
        "referee_model": PINNED, "timestamp": "2026-07-08T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_valid_decisions_pass():
    validate_decision(json.dumps(_decision()), GRID, HISTORY, PINNED)
    validate_decision(json.dumps(_decision(
        decision="PROMOTE", kill_reason=None)), GRID, HISTORY, PINNED)
    validate_decision(json.dumps(_decision(
        decision="ITERATE", kill_reason=None,
        iterate_params={"lookback_days": 60})), GRID, HISTORY, PINNED)


def test_missing_key_rejected():
    incomplete = _decision()
    del incomplete["iterate_params"]
    with pytest.raises(StageFailure, match="missing keys"):
        validate_decision(json.dumps(incomplete), GRID, HISTORY, PINNED)


def test_extra_key_rejected():
    with pytest.raises(StageFailure, match="unexpected keys"):
        validate_decision(json.dumps(_decision(suggested_fix="try vol scaling")),
                          GRID, HISTORY, PINNED)


def test_referee_model_pin_enforced():
    """Model identity is pinned by the orchestrator — a referee reporting
    any other identity is rejected, not persisted."""
    with pytest.raises(StageFailure, match="pinned"):
        validate_decision(
            json.dumps(_decision(referee_model="codex gpt-5.5")),
            GRID, HISTORY, PINNED)


def test_incoherent_combinations_rejected():
    cases = [
        _decision(kill_reason=None),                      # KILL needs merits
        _decision(kill_reason="infrastructure"),          # referee never writes it
        _decision(decision="PROMOTE", kill_reason="merits"),
        _decision(decision="PROMOTE", kill_reason=None,
                  iterate_params={"lookback_days": 60}),  # iterate_params off-path
        _decision(decision="ITERATE", kill_reason=None,
                  iterate_params={"lookback_days": 120}),  # not FIRST untried
        _decision(decision="ITERATE", kill_reason=None, iterate_params=None),
        _decision(justification="   "),
    ]
    for bad in cases:
        with pytest.raises(StageFailure):
            validate_decision(json.dumps(bad), GRID, HISTORY, PINNED)


def test_iterate_with_exhausted_grid_rejected():
    history = HISTORY + [{"iteration": 1, "params": GRID[0]},
                         {"iteration": 2, "params": GRID[1]}]
    bad = _decision(decision="ITERATE", kill_reason=None,
                    iterate_params=GRID[0])
    with pytest.raises(StageFailure, match="exhausted"):
        validate_decision(json.dumps(bad), GRID, history, PINNED)


def test_mechanical_referee_passes_strict_validator():
    blocks = validate_hypothesis_md(
        dryrun.render(dryrun.CANNED[0]["hypothesis_md"], hid="H999", ts="t"))
    results = {
        "hypothesis_id": "H999", "iteration": 0, "generated_at": "t",
        "iteration_history": [{"iteration": 0, "params": blocks["params"]}],
        "aggregate": {"sharpe_annualized": -1.0, "max_drawdown": 0.5,
                      "hit_rate": 0.4},
        "folds": [{"sharpe_annualized": -1.0}] * 4,
        "referee_inputs": {"deflated_sharpe": 0.01},
    }
    decision = dryrun.mechanical_referee(blocks, results)
    validate_decision(json.dumps(decision), blocks["grid"],
                      results["iteration_history"],
                      expected_referee_model="dry-run-mechanical-referee")


def test_hypothesis_md_validation():
    good = dryrun.render(dryrun.CANNED[0]["hypothesis_md"], hid="H001", ts="t")
    blocks = validate_hypothesis_md(good)
    assert blocks["params"] == {"lookback_days": 90}
    assert len(blocks["grid"]) == 2
    with pytest.raises(StageFailure, match="JSON blocks"):
        validate_hypothesis_md("# H001 — no blocks here")
    with pytest.raises(StageFailure, match="same keys"):
        validate_hypothesis_md(good.replace(
            '[{"lookback_days": 60}, {"lookback_days": 120}]',
            '[{"other_param": 1}]'))


def test_signal_source_validation(tmp_path):
    good = dryrun.CANNED[0]["signal_py"]
    assert validate_signal_source(good) == {"lookback_days": 90}
    with pytest.raises(StageFailure, match="forbidden"):
        validate_signal_source("import os\n" + good)
    with pytest.raises(StageFailure, match="PARAMS"):
        validate_signal_source(good.replace("PARAMS = ", "P = "))
    # deterministic iteration patch
    path = tmp_path / "signal.py"
    path.write_text(good)
    patch_params_line(path, {"lookback_days": 60})
    assert validate_signal_source(path.read_text()) == {"lookback_days": 60}
