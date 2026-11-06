"""Versioned walk-forward backtester; see SPEC_v2 for corrected timing.

Historical baseline (SPEC §3-§5):

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
from engine.universe import eligible_symbols


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


def _historical_close_v1(
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


def run_walkforward(panel, compute_signal, start, end, *, mode="delayed_close_v2", membership=None):
    """Default: observe d-2, fill close(d-1), mark close(d). Research model.

    Historical mode reproduces v1 flaws and is never promotion evidence.
    """
    if mode == "historical_close_v1":
        return _historical_close_v1(panel, compute_signal, start, end)
    if mode != "delayed_close_v2":
        raise EngineError("unknown execution mode")
    if panel.index.names != ["date", "symbol"] or not panel.index.is_unique:
        raise EngineError("invalid/duplicate panel index")
    if not panel.index.is_monotonic_increasing:
        raise EngineError("panel is not sorted")
    dates = panel.index.get_level_values("date")
    required = pd.date_range(start-pd.Timedelta(days=2), end, tz="UTC")
    if not required.isin(dates.unique()).all():
        raise EngineError("calendar incomplete: observation/fill/mark days required")
    closes = panel.close.unstack("symbol")
    volumes = panel.volume.unstack("symbol")
    holdings = pd.Series(0.0, index=closes.columns)
    cash, nav = 1.0, 1.0
    records = []
    from engine.portfolio import rebalance_notionals

    def marks(day, required_assets):
        values = closes.loc[day, required_assets]
        if not np.isfinite(values).all() or (values <= 0).any():
            raise EngineError(f"missing/invalid held or selected price at {day.date()}")
        return values

    with forbid_io():
        for day in pd.date_range(start, end, tz="UTC"):
            observation, fill = day-pd.Timedelta(days=2), day-pd.Timedelta(days=1)
            visible = panel.loc[dates <= observation].copy(deep=True)
            before = visible.copy(deep=True)
            sig = _validate_signal_output(compute_signal(visible))
            if not visible.equals(before):
                raise EngineError("signal mutated its input")
            if not sig.index.is_unique or len(sig.index.difference(visible.index)) or np.isinf(sig.to_numpy()).any():
                raise EngineError("invalid signal index/values")
            cross = sig.xs(observation, level=0) if observation in sig.index.get_level_values(0) else pd.Series(dtype=float)
            if membership is not None:
                eligible = eligible_symbols(membership, observation)
                cross = cross[cross.index.isin(eligible)]
            target = build_weights(cross, closes.loc[observation])
            held = holdings != 0
            selected = target != 0
            marks(fill, held | selected)  # disappearing assets cannot receive free exits
            pre_nav = cash + float(holdings.sum())
            if not np.isfinite(pre_nav) or pre_nav <= 0:
                raise EngineError("nonpositive portfolio NAV")
            new_holdings, new_cash, fee, traded = rebalance_notionals(holdings, cash, target)
            trading = (new_holdings - holdings).abs() > 1e-15
            volume = volumes.loc[fill, trading]
            if not np.isfinite(volume).all() or (volume <= 0).any():
                raise EngineError("no evidenced trading volume at fill")
            fill_prices = marks(fill, selected)
            end_prices = marks(day, selected)
            pnl = float((new_holdings[selected] * (end_prices/fill_prices-1)).sum())
            end_holdings = new_holdings.copy()
            end_holdings.loc[selected] *= end_prices/fill_prices
            cash = new_cash
            nav = cash + float(end_holdings.sum())
            if not np.isfinite(nav) or nav <= 0:
                raise EngineError("nonpositive portfolio NAV after marking")
            if not np.isclose(nav, pre_nav-fee+pnl, rtol=1e-12, atol=1e-14):
                raise EngineError("portfolio conservation failure")
            holdings = end_holdings
            records.append({"date": day, "r_gross": pnl/pre_nav, "cost": fee/pre_nav,
                            "r_net": (pnl-fee)/pre_nav, "turnover": traded/pre_nav,
                            "n_long": int((target > 0).sum()), "n_short": int((target < 0).sum()),
                            "has_positions": bool(selected.any()), "nav": nav, "cash": cash,
                            "gross_exposure": float(holdings.abs().sum())/nav,
                            "net_exposure": float(holdings.sum())/nav})
    return pd.DataFrame(records).set_index("date")
