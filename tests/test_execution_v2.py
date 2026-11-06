import numpy as np
import pandas as pd
import pytest

from engine.backtest import run_walkforward
from engine.errors import EngineError
from engine.metrics import max_drawdown
from engine.portfolio import rebalance_notionals
from tests.test_known_answer import _panel, D0, D1, D2, toy_signal


def panel():
    p = _panel()
    warmup = p.loc[[D0]].copy()
    warmup.index = pd.MultiIndex.from_tuples(
        [(D0 - pd.Timedelta(days=1), s) for _, s in warmup.index],
        names=["date", "symbol"],
    )
    return pd.concat([warmup, p]).sort_index()


def test_drift_actual_notional_fee_cash_known_answer():
    # One long and one short, initial cash=1. Solve x+.0025*x=1.
    h = pd.Series([0.0, 0.0], index=["L", "S"])
    w = pd.Series([0.5, -0.5], index=h.index)
    first, cash, fee, turnover = rebalance_notionals(h, 1.0, w)
    x = 1 / 1.0025
    assert first.tolist() == pytest.approx([x / 2, -x / 2])
    assert fee == pytest.approx(0.0025 * x)
    assert cash == pytest.approx(x)
    # L rises 20%, S unchanged: notionals .6*x,-.5*x; equity 1.1*x.
    drifted = first * pd.Series([1.2, 1.0], index=h.index)
    second, cash2, fee2, traded = rebalance_notionals(drifted, cash, w)
    # Both changes sell: .6*x-.5*y + .5*y-.5*x = .1*x exactly.
    assert traded == pytest.approx(0.1 * x)
    assert fee2 == pytest.approx(0.00025 * x)
    y = 1.09975 * x
    assert second.tolist() == pytest.approx([y / 2, -y / 2])
    assert cash2 == pytest.approx(y)
    assert (second.abs().sum() / (cash2 + second.sum())) == pytest.approx(1)
    assert second.sum() == pytest.approx(0)


def test_delayed_observation_and_pnl():
    seen = []

    def probe(p):
        seen.append(p.index.get_level_values("date").max())
        return toy_signal(p)

    r = run_walkforward(panel(), probe, D1, D2)
    assert seen == [D0 - pd.Timedelta(days=1), D0]
    x = 1 / 1.0025
    assert r.loc[D1, "turnover"] == pytest.approx(x)
    assert r.loc[D1, "r_gross"] == pytest.approx(0.05 * x)
    assert r.loc[D1, "r_net"] == pytest.approx(0.0475 * x)
    assert r.loc[D2, "turnover"] > 0  # unchanged ranks still rebalance drift
    assert r.loc[D2, "cost"] == pytest.approx(0.0025 * r.loc[D2, "turnover"])


def test_future_signal_bar_cannot_change_prior_position():
    original = panel()

    def price_signal(p):
        return p.close

    altered = original.copy()
    altered.loc[(D1, "S14"), "close"] = 150
    # D2 fills from D0 observations, unaffected by D1 rank spike.
    seen = []

    def record(p):
        seen.append(p.copy())
        return price_signal(p)

    run_walkforward(altered, record, D1, D2)
    assert all(p.index.get_level_values("date").max() < D1 for p in seen)


@pytest.mark.parametrize("when,symbol", [(D1, "S14"), (D2, "S14"), (D0, "S14")])
def test_missing_bar_or_delisting_aborts(when, symbol):
    p = panel().drop(index=(when, symbol))
    with pytest.raises(EngineError, match="price"):
        run_walkforward(p, toy_signal, D1, D2)


def test_disappeared_held_asset_cannot_exit_for_free():
    p = panel().drop(index=(D1, "S14"))

    def close_book(x):
        sig = toy_signal(x)
        if x.index.get_level_values("date").max() == D0:
            sig[:] = np.nan
        return sig

    with pytest.raises(EngineError, match="price"):
        run_walkforward(p, close_book, D1, D2)


def test_zero_volume_fill_and_insolvency_refused():
    p = panel()
    p.loc[(D0, "S14"), "volume"] = 0
    with pytest.raises(EngineError, match="volume"):
        run_walkforward(p, toy_signal, D1, D2)
    p = panel()
    p.loc[(D1, "S00"), "close"] = 10000  # short loss bankrupts portfolio
    with pytest.raises(EngineError, match="NAV"):
        run_walkforward(p, toy_signal, D1, D2)


def test_flatten_charges_drifted_book_and_preserves_cash():
    h = pd.Series([0.6, -0.5])
    flattened, cash, fee, traded = rebalance_notionals(h, 1.0, h * 0)
    assert traded == pytest.approx(1.1)
    assert fee == pytest.approx(0.00275)
    assert cash == pytest.approx(1.09725)
    assert flattened.sum() == 0


def test_initial_loss_drawdown_is_counted():
    assert max_drawdown(pd.Series([-0.2, 0.1])) == pytest.approx(0.2)
    assert max_drawdown(pd.Series([-0.2, 0.1]), historical=True) == 0
