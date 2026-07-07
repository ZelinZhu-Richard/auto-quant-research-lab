"""A3 acceptance: the planted-leak signal (reads close(t+1)) must be CAUGHT
automatically; a clean momentum signal must pass the full harness."""

import numpy as np
import pandas as pd
import pytest

from engine.harness import (
    LookaheadError,
    SignalContractError,
    assert_deterministic,
    assert_no_lookahead,
    run_full_harness,
)


@pytest.fixture(scope="module")
def synthetic_panel() -> pd.DataFrame:
    """20 symbols x 80 days of seeded geometric random walks."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=80, freq="D", tz="UTC")
    symbols = [f"S{i:02d}" for i in range(20)]
    frames = []
    for sym in symbols:
        closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.03, len(dates)))
        frames.append(pd.DataFrame({
            "date": dates, "symbol": sym,
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": 1000.0,
        }))
    return (
        pd.concat(frames).set_index(["date", "symbol"]).sort_index()
        [["open", "high", "low", "close", "volume"]]
    )


def clean_momentum(panel: pd.DataFrame) -> pd.Series:
    closes = panel["close"].unstack("symbol")
    return closes.pct_change(20, fill_method=None).stack()


def leaky_tomorrow_return(panel: pd.DataFrame) -> pd.Series:
    """Deliberate lookahead: signal at t is the t -> t+1 return."""
    closes = panel["close"].unstack("symbol")
    return closes.pct_change(fill_method=None).shift(-1).stack()


def leaky_centered_mean(panel: pd.DataFrame) -> pd.Series:
    """Subtler lookahead: centered rolling window peeks 2 days ahead."""
    closes = panel["close"].unstack("symbol")
    return (closes / closes.rolling(5, center=True).mean() - 1).stack()


def test_clean_momentum_passes_full_harness(synthetic_panel):
    run_full_harness(clean_momentum, synthetic_panel)


def test_planted_leak_is_caught(synthetic_panel):
    with pytest.raises(LookaheadError):
        assert_no_lookahead(leaky_tomorrow_return, synthetic_panel)


def test_centered_window_leak_is_caught(synthetic_panel):
    with pytest.raises(LookaheadError):
        assert_no_lookahead(leaky_centered_mean, synthetic_panel)


def test_nondeterministic_signal_is_caught(synthetic_panel):
    def noisy(panel: pd.DataFrame) -> pd.Series:
        closes = panel["close"].unstack("symbol")
        base = closes.pct_change(5, fill_method=None).stack()
        return base + np.random.default_rng().normal(0, 1e-6, len(base))

    with pytest.raises(SignalContractError, match="non-deterministic"):
        assert_deterministic(noisy, synthetic_panel)


def test_foreign_index_is_caught(synthetic_panel):
    def foreign(panel: pd.DataFrame) -> pd.Series:
        sig = clean_momentum(panel)
        extra = pd.Series(
            [1.0],
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2030-01-01", tz="UTC"), "GHOST")],
                names=["date", "symbol"],
            ),
        )
        return pd.concat([sig, extra])

    with pytest.raises(SignalContractError, match="absent from the input panel"):
        run_full_harness(foreign, synthetic_panel)


def test_inf_values_are_caught(synthetic_panel):
    def infinite(panel: pd.DataFrame) -> pd.Series:
        sig = clean_momentum(panel)
        sig.iloc[-1] = np.inf
        return sig

    with pytest.raises(SignalContractError, match="inf"):
        run_full_harness(infinite, synthetic_panel)


def test_last_date_only_clean_signal_passes(synthetic_panel):
    """SPEC §2 permits returning only the last date's cross-section."""
    def last_date_only(panel: pd.DataFrame) -> pd.Series:
        sig = clean_momentum(panel)
        last = sig.index.get_level_values(0).max()
        return sig[sig.index.get_level_values(0) == last]

    run_full_harness(last_date_only, synthetic_panel)


def test_fabricated_future_dates_are_caught(synthetic_panel):
    """A truncated run may never emit dates beyond its truncation point."""
    dates = synthetic_panel.index.get_level_values("date").unique().sort_values()
    all_dates, symbols = dates, ["S00", "S01"]

    def fabricator(panel: pd.DataFrame) -> pd.Series:
        # always emits the full calendar, even when handed truncated data
        idx = pd.MultiIndex.from_product(
            [all_dates, symbols], names=["date", "symbol"]
        )
        return pd.Series(1.0, index=idx)

    with pytest.raises(LookaheadError, match="fabricated"):
        assert_no_lookahead(fabricator, synthetic_panel)


def test_file_io_during_compute_is_caught(synthetic_panel, tmp_path):
    """The real leak vector: a signal that loads data itself instead of
    using the panel it was handed. Must fail the purity guard."""
    side_file = tmp_path / "sneaky.parquet"
    synthetic_panel.to_parquet(side_file)

    def io_pandas(panel: pd.DataFrame) -> pd.Series:
        full = pd.read_parquet(side_file)  # bypasses the truncation
        closes = full["close"].unstack("symbol")
        return closes.pct_change(fill_method=None).shift(-1).stack()

    def io_open(panel: pd.DataFrame) -> pd.Series:
        with open(side_file, "rb") as fh:  # noqa: PTH123
            fh.read(10)
        return clean_momentum(panel)

    with pytest.raises(SignalContractError, match="I/O"):
        run_full_harness(io_pandas, synthetic_panel)
    with pytest.raises(SignalContractError, match="I/O"):
        run_full_harness(io_open, synthetic_panel)


def test_short_panel_refused():
    """Panels unable to support >= 10 sampled dates must fail loudly."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-01", periods=15, freq="D", tz="UTC")
    frames = []
    for sym in ["A", "B", "C"]:
        closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, len(dates)))
        frames.append(pd.DataFrame({
            "date": dates, "symbol": sym, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": 1.0,
        }))
    small = (
        pd.concat(frames).set_index(["date", "symbol"]).sort_index()
        [["open", "high", "low", "close", "volume"]]
    )
    with pytest.raises(SignalContractError, match="too short"):
        assert_no_lookahead(clean_momentum, small)
