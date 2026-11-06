"""Metric definitions per SPEC §6-§7. Pure functions of return series;
identical inputs give bit-identical outputs. No scipy (statistics.NormalDist
covers the normal CDF/quantile needs of the DSR)."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from engine.config import ANNUALIZATION_DAYS

_NORMAL = NormalDist()
_EULER_MASCHERONI = 0.5772156649


def sharpe_daily(returns: pd.Series) -> float:
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0.0 or not np.isfinite(sd):
        return 0.0
    return float(r.mean() / sd)


def sharpe_annualized(returns: pd.Series) -> float:
    return sharpe_daily(returns) * math.sqrt(ANNUALIZATION_DAYS)


def max_drawdown(returns: pd.Series, *, historical: bool = False) -> float:
    """Positive fraction: 0.25 means a -25% peak-to-trough on the
    compounded equity curve."""
    r = np.asarray(returns, dtype=float)
    if len(r) == 0:
        return 0.0
    equity = np.cumprod(1.0 + r)
    peaks = np.maximum.accumulate(equity) if historical else np.maximum.accumulate(
        np.r_[1.0, equity])[1:]
    drawdowns = 1.0 - equity / peaks
    return float(drawdowns.max())


def hit_rate(returns: pd.Series, position_days: pd.Series) -> float:
    """Fraction of position days with net return > 0. Days with no
    positions are excluded (SPEC §6)."""
    r = returns[position_days.astype(bool)]
    if len(r) == 0:
        return 0.0
    return float((r > 0).sum() / len(r))


def skew_and_raw_kurtosis(returns: pd.Series) -> tuple[float, float]:
    """Population-moment skewness g3 = m3/m2^1.5 and RAW (non-excess)
    kurtosis g4 = m4/m2^2 (normal -> 3), as used by the DSR (SPEC §6)."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return 0.0, 3.0
    m = r - r.mean()
    m2 = float((m**2).mean())
    if m2 == 0.0:
        return 0.0, 3.0
    g3 = float((m**3).mean() / m2**1.5)
    g4 = float((m**4).mean() / m2**2)
    return g3, g4


def deflated_sharpe(
    returns: pd.Series,
    n_trials: int,
    trial_sharpes_daily: list[float],
) -> float:
    """Deflated Sharpe Ratio per Bailey & Lopez de Prado, exactly as
    specified in SPEC §7.

    n_trials: N = ledger line count + 1 (the +1 is the current hypothesis).
    trial_sharpes_daily: per-period (daily) Sharpe of every numeric trial
    INCLUDING the current one; nan entries must already be excluded.
    """
    sr = sharpe_daily(returns)
    T = len(returns)
    if T < 2:
        return 0.0
    g3, g4 = skew_and_raw_kurtosis(returns)

    return deflated_sharpe_from_moments(sr, T, g3, g4, n_trials, trial_sharpes_daily)


def deflated_sharpe_from_moments(sr, T, g3, g4, n_trials, trial_sharpes_daily,
                                *, variance_floor=0.0) -> float:
    """DSR diagnostic. v2 supplies 1/(T-1) as a nonzero null variance floor.

    N is a search-size proxy, NOT an estimated effective independent count.
    The historical formula is retained with variance_floor=0 for audits.
    """
    if type(n_trials) is not int or n_trials < 1 or type(T) is not int or T < 2:
        raise ValueError("invalid trial count or sample size")
    numeric = list(trial_sharpes_daily)
    if not numeric or any(type(x) not in (int, float) or not math.isfinite(x)
                          for x in [sr, g3, g4, variance_floor, *numeric]):
        raise ValueError("nonfinite or missing DSR input")
    if len(numeric) > n_trials or variance_floor < 0:
        raise ValueError("inconsistent DSR trial inputs")
    variance = max(float(np.var(numeric, ddof=1)) if len(numeric) >= 2 else 0.0,
                   variance_floor)
    if n_trials >= 2 and variance > 0.0:
        sr0 = math.sqrt(variance) * (
            (1.0 - _EULER_MASCHERONI) * _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
        )
    else:
        sr0 = 0.0
    radicand = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr**2
    if radicand <= 0.0:
        return 0.0
    return float(_NORMAL.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(radicand)))
