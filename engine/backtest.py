"""Walk-forward backtester (SPEC §3-§5).

No-lookahead by construction: for each return day the signal function is
handed a panel slice ending at the SIGNAL date (the previous trading date),
so future rows are physically absent from its input. The one-day execution
lag is structural and not configurable.
"""

import numpy as np
import pandas as pd

from engine.config import COST_PER_SIDE
from engine.errors import EngineError
from engine.io_guard import forbid_io
from engine.portfolio import build_weights


def _validate_signal_output(sig: object) -> pd.Series:
    if not isinstance(sig, pd.Series):
        raise EngineError(f"compute_signal returned {type(sig).__name__}, not pd.Series")
    if not isinstance(sig.index, pd.MultiIndex) or sig.index.nlevels != 2:
        raise EngineError("compute_signal must return a Series indexed by (date, symbol)")
    try:
        values = sig.astype("float64")
    except (TypeError, ValueError) as exc:
        raise EngineError(f"signal values not numeric: {exc}") from exc
    return values


def run_walkforward(
    panel: pd.DataFrame,
    compute_signal,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Run the strategy over return days [start, end] (inclusive).

    Returns a DataFrame indexed by return day with columns:
    r_gross, cost, r_net, turnover, n_long, n_short, has_positions.
    """
    if panel.index.names != ["date", "symbol"]:
        raise EngineError(f"panel index names {panel.index.names} != ['date','symbol']")
    if not panel.index.is_monotonic_increasing:
        # the positional no-lookahead cut below requires date-major sort
        raise EngineError("panel index is not sorted; refusing to run")

    dates = panel.index.get_level_values("date").unique().sort_values()
    date_values = dates.values  # date-major sorted; used for positional cuts
    panel_date_col = panel.index.get_level_values("date").values

    # SPEC §3/§5 define return days as a complete daily calendar over
    # [start, end]. Interior OR terminal gaps must fail loudly (infra-kill),
    # never silently shorten the window or compress multi-day moves.
    return_days = dates[(dates >= start) & (dates <= end)]
    expected_days = (end - start).days + 1
    if (
        len(return_days) != expected_days
        or len(return_days) == 0
        or return_days[0] != start
        or return_days[-1] != end
    ):
        raise EngineError(
            f"return-day calendar incomplete: expected {expected_days} days "
            f"covering [{start.date()}, {end.date()}], found {len(return_days)}"
        )
    first_pos = int(np.searchsorted(date_values, return_days[0].to_numpy()))
    if first_pos == 0:
        raise EngineError("no signal date exists before the first return day")
    if dates[first_pos - 1] != start - pd.Timedelta(days=1):
        raise EngineError(
            "first signal date is not the calendar day before the window "
            f"start ({dates[first_pos - 1].date()} vs {start.date()})"
        )

    closes = panel["close"].unstack("symbol")
    symbols = closes.columns
    prev_weights = pd.Series(0.0, index=symbols)

    records = []
    # forbid_io: the signal must compute from `visible` alone — a signal
    # that re-loads the dataset itself would bypass the truncation (SPEC §2)
    with forbid_io():
        for return_day in return_days:
            records.append(
                _run_one_day(panel, compute_signal, closes, dates, date_values,
                             panel_date_col, return_day, prev_weights)
            )
            prev_weights = records[-1].pop("_weights")

    result = pd.DataFrame.from_records(records).set_index("date")
    return result


def _run_one_day(panel, compute_signal, closes, dates, date_values,
                 panel_date_col, return_day, prev_weights) -> dict:
    day_pos = int(np.searchsorted(date_values, return_day.to_numpy()))
    signal_date = dates[day_pos - 1]

    # positional cut: every row with date <= signal_date, nothing after
    row_cut = int(np.searchsorted(panel_date_col, signal_date.to_numpy(), side="right"))
    visible = panel.iloc[:row_cut]

    sig = _validate_signal_output(compute_signal(visible))
    if signal_date in sig.index.get_level_values(0):
        signal_cross = sig.xs(signal_date, level=0)
        if not signal_cross.index.is_unique:
            raise EngineError(f"duplicate symbols in signal at {signal_date}")
    else:
        signal_cross = pd.Series(dtype="float64")

    weights = build_weights(signal_cross, closes.loc[signal_date])
    turnover = float((weights - prev_weights).abs().sum())

    asset_returns = closes.loc[return_day] / closes.loc[signal_date] - 1.0
    r_gross = float((weights * asset_returns.fillna(0.0)).sum())
    cost = COST_PER_SIDE * turnover
    return {
        "date": return_day,
        "r_gross": r_gross,
        "cost": cost,
        "r_net": r_gross - cost,
        "turnover": turnover,
        "n_long": int((weights > 0).sum()),
        "n_short": int((weights < 0).sum()),
        "has_positions": bool((weights != 0).any()),
        "_weights": weights,
    }
