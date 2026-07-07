import math

import pandas as pd
import pytest

from engine.metrics import (
    deflated_sharpe,
    hit_rate,
    max_drawdown,
    sharpe_annualized,
    sharpe_daily,
    skew_and_raw_kurtosis,
)


def test_sharpe_known_answer():
    r = pd.Series([0.01, -0.01, 0.02])
    # independent arithmetic, no engine code:
    mean = 0.02 / 3
    var = ((0.01 - mean) ** 2 + (-0.01 - mean) ** 2 + (0.02 - mean) ** 2) / 2
    expected_daily = mean / math.sqrt(var)
    assert abs(sharpe_daily(r) - expected_daily) < 1e-12
    assert abs(sharpe_annualized(r) - expected_daily * math.sqrt(365)) < 1e-12


def test_sharpe_degenerate_cases():
    assert sharpe_daily(pd.Series([0.01] * 5)) == 0.0  # zero variance
    assert sharpe_daily(pd.Series([0.01])) == 0.0      # too short
    assert sharpe_daily(pd.Series(dtype=float)) == 0.0


def test_max_drawdown_known_answer():
    # equity: 1.1, 0.55, 0.66 ; peak stays 1.1 ; deepest dd = 1 - 0.55/1.1 = 0.5
    r = pd.Series([0.1, -0.5, 0.2])
    assert abs(max_drawdown(r) - 0.5) < 1e-12
    assert max_drawdown(pd.Series([0.1, 0.2])) == 0.0


def test_hit_rate_excludes_no_position_days():
    r = pd.Series([0.01, -0.01, 0.0, 0.02])
    pos = pd.Series([True, True, True, False])
    # position days: +0.01 (hit), -0.01 (miss), 0.0 (miss; strict >0)
    assert abs(hit_rate(r, pos) - 1 / 3) < 1e-12
    assert hit_rate(r, pd.Series([False] * 4)) == 0.0


def test_skew_kurtosis_symmetric_two_point():
    r = pd.Series([0.02, -0.02, 0.02, -0.02])
    g3, g4 = skew_and_raw_kurtosis(r)
    assert abs(g3) < 1e-12
    assert abs(g4 - 1.0) < 1e-12  # two-point distribution has raw kurtosis 1


def test_dsr_no_deflation_for_single_trial():
    r = pd.Series([0.01, -0.005, 0.02, 0.003, -0.001] * 20)
    dsr_1 = deflated_sharpe(r, n_trials=1, trial_sharpes_daily=[sharpe_daily(r)])
    # positive sharpe, SR0 = 0 => DSR must be > 0.5
    assert dsr_1 > 0.5


def test_dsr_decreases_with_trial_count():
    r = pd.Series([0.01, -0.005, 0.02, 0.003, -0.001] * 20)
    trials = [0.02, 0.10, -0.03, sharpe_daily(r)]  # variance > 0
    dsr_small_n = deflated_sharpe(r, n_trials=4, trial_sharpes_daily=trials)
    dsr_big_n = deflated_sharpe(r, n_trials=40, trial_sharpes_daily=trials)
    assert dsr_big_n < dsr_small_n


def test_dsr_bounds_and_short_series():
    r = pd.Series([0.01, -0.005, 0.02, 0.003, -0.001] * 20)
    dsr = deflated_sharpe(r, n_trials=5, trial_sharpes_daily=[0.01, 0.02, 0.03])
    assert 0.0 <= dsr <= 1.0
    assert deflated_sharpe(pd.Series([0.01]), 1, [0.1]) == 0.0
