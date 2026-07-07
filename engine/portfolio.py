"""Cross-sectional portfolio construction (SPEC §3). Fixed in the engine;
agents cannot change any of this."""

import pandas as pd

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
