import numpy as np
import pandas as pd

PARAMS = {"lookback_days": 120, "skip_days": 7}

def compute_signal(panel: pd.DataFrame) -> pd.Series:
    close = panel["close"]
    dates = panel.index.get_level_values(0)
    symbols = panel.index.get_level_values(1)

    skip_index = pd.MultiIndex.from_arrays(
        [dates - pd.to_timedelta(PARAMS["skip_days"], unit="D"), symbols],
        names=panel.index.names,
    )
    lookback_index = pd.MultiIndex.from_arrays(
        [dates - pd.to_timedelta(PARAMS["lookback_days"], unit="D"), symbols],
        names=panel.index.names,
    )

    skip_close = close.reindex(skip_index).to_numpy(dtype=float)
    lookback_close = close.reindex(lookback_index).to_numpy(dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        values = skip_close / lookback_close - 1.0

    values[~np.isfinite(values)] = np.nan
    return pd.Series(values, index=panel.index, name="signal")