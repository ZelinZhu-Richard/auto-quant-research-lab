"""Cross-sectional portfolio construction (SPEC §3). Fixed in the engine;
agents cannot change any of this."""

import numpy as np
import pandas as pd

from engine.errors import EngineError

from engine.config import MIN_ASSETS_PER_LEG


def build_weights(signal_cross: pd.Series, close_cross: pd.Series) -> pd.Series:
    """Weights for one signal date.

    signal_cross: signal values indexed by symbol (NaN = no opinion).
    close_cross:  closes indexed by symbol for the same date (NaN = no bar).

    Returns weights indexed by the union of symbols in close_cross:
    top quintile +0.5/q, bottom quintile -0.5/q, else 0. Deterministic
    ordering: (signal DESC, symbol ASC). q = floor(n_valid/5); q < 3 =>
    all-zero weights (degenerate cross-section).
    """
    if not signal_cross.index.is_unique or not close_cross.index.is_unique:
        raise EngineError("duplicate portfolio symbol")
    if np.isinf(signal_cross.to_numpy()).any():
        raise EngineError("nonfinite signal")
    prices = close_cross.dropna()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise EngineError("invalid portfolio price")
    weights = pd.Series(0.0, index=close_cross.index)
    valid = signal_cross.dropna()
    valid = valid[valid.index.isin(close_cross.index)]
    valid = valid[close_cross.reindex(valid.index).notna()]
    n = len(valid)
    q = n // 5
    if q < MIN_ASSETS_PER_LEG:
        return weights
    # symbol ASC first, then stable sort by value DESC => exact SPEC order
    ordered = valid.sort_index().sort_values(ascending=False, kind="stable")
    longs = ordered.index[:q]
    shorts = ordered.index[-q:]
    weights.loc[longs] = 0.5 / q
    weights.loc[shorts] = -0.5 / q
    return weights


def rebalance_notionals(holdings: pd.Series, cash: float, target: pd.Series):
    """Self-financing rebalance to POST-fee NAV weights, with 25bps/side.

    Existing holdings are signed notionals already marked at the fill price.
    The monotone scalar equation includes fees in target sizing; a bisection
    avoids pretending fees are financed without changing portfolio weights.
    """
    from engine.config import COST_PER_SIDE
    if not holdings.index.equals(target.index) or not np.isfinite(holdings).all() or not np.isfinite(target).all() or not np.isfinite(cash):
        raise EngineError("invalid portfolio accounting input")
    if target.abs().sum() > 1+1e-12 or abs(target.sum()) > 1e-12:
        raise EngineError("target exposure invariant violated")
    equity = cash + float(holdings.sum())
    if equity <= 0 or COST_PER_SIDE*float(holdings.abs().sum()) >= equity:
        raise EngineError("insolvent portfolio cannot fund rebalance")
    target_array, held_array = target.to_numpy(), holdings.to_numpy()
    lo, hi = 0.0, equity
    for _ in range(64):
        post_fee = (lo+hi)/2
        residual = post_fee + COST_PER_SIDE*float(np.abs(target_array*post_fee-held_array).sum()) - equity
        if residual > 0:
            hi = post_fee
        else:
            lo = post_fee
    notionals = target*((lo+hi)/2)
    trades = notionals-holdings
    traded = float(trades.abs().sum())
    fee = COST_PER_SIDE*traded
    new_cash = cash-float(trades.sum())-fee
    if not np.isclose(new_cash+notionals.sum(), equity-fee, atol=1e-14, rtol=1e-12):
        raise EngineError("cash conservation failure")
    return notionals, new_cash, fee, traded
