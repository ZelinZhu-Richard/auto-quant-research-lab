"""Known-answer test (A2 acceptance): a hardcoded toy signal on a tiny
synthetic panel with HAND-COMPUTED expected PnL, exact to 1e-9.

Setup: 15 symbols S00..S14, three dates d0 < d1 < d2, all closes 100 at d0.
Toy signal: value = symbol number (S14 most attractive), constant over time.
q = floor(15/5) = 3 => longs S14,S13,S12 at +1/6; shorts S02,S01,S00 at -1/6.

Return day d1 (weights formed at d0, turnover 1.0):
  gross = (0.10 + 0.05 + 0.00)/6 - (-0.10 - 0.05 + 0.00)/6 = 0.05
  cost  = 0.0025 * 1.0
  net   = 0.0475
Return day d2 (same ranks => same weights, turnover 0):
  gross = (0.10 + 0.00 + 0.10)/6 - (-0.10 + 0.00 - 0.10)/6 = 0.4/6
  net   = 0.4/6
"""

import numpy as np
import pandas as pd

from engine.backtest import run_walkforward

D0 = pd.Timestamp("2023-01-01", tz="UTC")
D1 = pd.Timestamp("2023-01-02", tz="UTC")
D2 = pd.Timestamp("2023-01-03", tz="UTC")
SYMBOLS = [f"S{i:02d}" for i in range(15)]

CLOSES = {
    D0: {s: 100.0 for s in SYMBOLS},
    D1: {**{s: 100.0 for s in SYMBOLS},
         "S14": 110.0, "S13": 105.0, "S12": 100.0,
         "S02": 100.0, "S01": 95.0, "S00": 90.0},
    D2: {**{s: 100.0 for s in SYMBOLS},
         "S14": 121.0, "S13": 105.0, "S12": 110.0,
         "S02": 90.0, "S01": 95.0, "S00": 81.0},
}


def _panel() -> pd.DataFrame:
    rows = []
    for date, closes in CLOSES.items():
        for sym in SYMBOLS:
            close = closes[sym]
            rows.append({"date": date, "symbol": sym, "open": close,
                         "high": close, "low": close, "close": close,
                         "volume": 1000.0})
    return (
        pd.DataFrame(rows).set_index(["date", "symbol"]).sort_index()
        [["open", "high", "low", "close", "volume"]]
    )


def toy_signal(panel: pd.DataFrame) -> pd.Series:
    """Attractiveness = symbol number. Pure, deterministic, no lookahead."""
    symbols = panel.index.get_level_values("symbol")
    return pd.Series(
        [float(s[1:]) for s in symbols], index=panel.index, dtype="float64"
    )


def test_known_answer_pnl_exact():
    run = run_walkforward(_panel(), toy_signal, start=D1, end=D2)

    assert list(run.index) == [D1, D2]

    # day 1 — hand-computed constants, not derived from engine code
    assert abs(run.loc[D1, "turnover"] - 1.0) < 1e-9
    assert abs(run.loc[D1, "r_gross"] - 0.05) < 1e-9
    assert abs(run.loc[D1, "cost"] - 0.0025) < 1e-9
    assert abs(run.loc[D1, "r_net"] - 0.0475) < 1e-9
    assert run.loc[D1, "n_long"] == 3 and run.loc[D1, "n_short"] == 3

    # day 2
    assert abs(run.loc[D2, "turnover"] - 0.0) < 1e-9
    assert abs(run.loc[D2, "r_gross"] - 0.4 / 6) < 1e-9
    assert abs(run.loc[D2, "cost"] - 0.0) < 1e-9
    assert abs(run.loc[D2, "r_net"] - 0.4 / 6) < 1e-9


def test_engine_never_shows_future_rows_to_signal():
    """The walk-forward must hand compute_signal only rows <= signal date."""
    seen_max_dates = []

    def probe(panel: pd.DataFrame) -> pd.Series:
        seen_max_dates.append(panel.index.get_level_values("date").max())
        return toy_signal(panel)

    run_walkforward(_panel(), probe, start=D1, end=D2)
    # return day D1 => signal date D0; return day D2 => signal date D1
    assert seen_max_dates == [D0, D1]


def test_degenerate_date_produces_zero_return_day():
    """If a date's cross-section is too thin (q < 3), that return day has
    no positions and only pays the cost of closing the previous book."""
    panel = _panel()
    # signal returns NaN for all but 10 symbols at D1 => n=10, q=2 < 3
    def thin_signal(p: pd.DataFrame) -> pd.Series:
        sig = toy_signal(p)
        dates = sig.index.get_level_values("date")
        symbols = sig.index.get_level_values("symbol")
        sig[(dates == D1) & (symbols > "S09")] = np.nan
        return sig

    run = run_walkforward(panel, thin_signal, start=D1, end=D2)
    # D1: normal book (weights from D0). D2: no positions (thin at D1),
    # so gross = 0 and cost = closing the whole book = 0.0025 * 1.0
    assert run.loc[D2, "n_long"] == 0 and run.loc[D2, "n_short"] == 0
    assert abs(run.loc[D2, "r_gross"] - 0.0) < 1e-9
    assert abs(run.loc[D2, "turnover"] - 1.0) < 1e-9
    assert abs(run.loc[D2, "r_net"] + 0.0025) < 1e-9
    assert not run.loc[D2, "has_positions"]
