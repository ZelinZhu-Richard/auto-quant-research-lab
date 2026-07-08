"""A3 acceptance: the planted-leak signal (reads close(t+1)) must be CAUGHT
automatically; a clean momentum signal must pass the full harness."""

import numpy as np
import pandas as pd
import pytest

from engine.harness import (
    LookaheadError,
    PurityViolation,
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

    def io_pathlib(panel: pd.DataFrame) -> pd.Series:
        side_file.read_bytes()
        return clean_momentum(panel)

    def io_pyarrow_dataset(panel: pd.DataFrame) -> pd.Series:
        import pyarrow.dataset as pads
        full = pads.dataset(str(side_file)).to_table().to_pandas()
        closes = full.set_index(["date", "symbol"])["close"].unstack("symbol")
        leaked = closes.pct_change(fill_method=None).shift(-1).stack()
        return leaked.reindex(clean_momentum(panel).index)  # disguise

    for leaky in (io_pandas, io_open, io_pathlib, io_pyarrow_dataset):
        with pytest.raises(PurityViolation):
            run_full_harness(leaky, synthetic_panel)


def test_kernel_level_fd_guard_catches_exotic_io(synthetic_panel, tmp_path):
    """Bypasses of the Python patches (io.open_code, pyarrow.fs, raw
    listdir) must still die at the kernel layer (RLIMIT_NOFILE=0) or the
    patch layer — either way the harness fails the signal."""
    side_file = tmp_path / "sneaky3.parquet"
    synthetic_panel.to_parquet(side_file)

    def io_open_code(panel: pd.DataFrame) -> pd.Series:
        import io as _io
        _io.open_code(str(side_file)).read(10)
        return clean_momentum(panel)

    def io_pyarrow_fs(panel: pd.DataFrame) -> pd.Series:
        import pyarrow.fs as pafs
        fs = pafs.LocalFileSystem()
        fs.open_input_file(str(side_file)).read(10)
        return clean_momentum(panel)

    def io_listdir(panel: pd.DataFrame) -> pd.Series:
        import os as _os
        _os.listdir(str(tmp_path))
        return clean_momentum(panel)

    for leaky in (io_open_code, io_pyarrow_fs, io_listdir):
        with pytest.raises((PurityViolation, OSError)):
            run_full_harness(leaky, synthetic_panel)


def test_environment_access_is_caught(synthetic_panel):
    """SPEC §2 forbids environment access — nondeterministic input."""
    def env_getenv(panel: pd.DataFrame) -> pd.Series:
        __import__("os").getenv("HOME")
        return clean_momentum(panel)

    def env_environ(panel: pd.DataFrame) -> pd.Series:
        import os as _os
        _ = _os.environ.get("PATH")
        return clean_momentum(panel)

    def env_environ_getitem(panel: pd.DataFrame) -> pd.Series:
        import os as _os
        _ = _os.environ["PATH"]
        return clean_momentum(panel)

    for impure in (env_getenv, env_environ, env_environ_getitem):
        with pytest.raises(PurityViolation):
            run_full_harness(impure, synthetic_panel)


def test_environb_allowlist_serves_bytes_and_blocks_rest(synthetic_panel):
    """The bytes environment API honors the same allowlist (served as
    bytes) and blocks everything else — SPEC §2 boundary parity."""
    def env_allowed_bytes(panel: pd.DataFrame) -> pd.Series:
        import os as _os
        _os.environb.get(b"PANDAS_COPY_ON_WRITE")  # allowlisted: must not raise
        return clean_momentum(panel)

    def env_blocked_bytes(panel: pd.DataFrame) -> pd.Series:
        import os as _os
        _os.environb.get(b"PATH")
        return clean_momentum(panel)

    run_full_harness(env_allowed_bytes, synthetic_panel)
    with pytest.raises(PurityViolation):
        run_full_harness(env_blocked_bytes, synthetic_panel)


def test_non_utf8_environb_key_does_not_break_guard(synthetic_panel):
    """A non-UTF-8 bytes key in the ambient environment must not crash
    guard entry — it is simply never allowlisted."""
    import os as _os

    _os.environb[b"\xff\xfeWEIRD"] = b"1"
    try:
        run_full_harness(clean_momentum, synthetic_panel)
    finally:
        del _os.environb[b"\xff\xfeWEIRD"]


def test_engine_backtest_also_enforces_purity(synthetic_panel, tmp_path):
    """Defense in depth: S3's walk-forward itself blocks I/O, so a signal
    that somehow skipped the harness still cannot self-load data."""
    from engine.backtest import run_walkforward

    side_file = tmp_path / "sneaky2.parquet"
    synthetic_panel.to_parquet(side_file)

    def io_signal(panel: pd.DataFrame) -> pd.Series:
        pd.read_parquet(side_file)
        return clean_momentum(panel)

    dates = synthetic_panel.index.get_level_values("date").unique().sort_values()
    with pytest.raises(PurityViolation):
        run_walkforward(synthetic_panel, io_signal, start=dates[40], end=dates[45])


def _tiny_panel(n_dates: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-01", periods=n_dates, freq="D", tz="UTC")
    frames = []
    for sym in ["A", "B", "C"]:
        closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, len(dates)))
        frames.append(pd.DataFrame({
            "date": dates, "symbol": sym, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": 1.0,
        }))
    return (
        pd.concat(frames).set_index(["date", "symbol"]).sort_index()
        [["open", "high", "low", "close", "volume"]]
    )


def short_momentum(panel: pd.DataFrame) -> pd.Series:
    closes = panel["close"].unstack("symbol")
    return closes.pct_change(3, fill_method=None).stack()


def test_short_panel_refused():
    """Panels unable to support >= 10 sampled dates must fail loudly."""
    with pytest.raises(SignalContractError, match="too short"):
        assert_no_lookahead(short_momentum, _tiny_panel(15))
    # boundary: min_history(10) + n_samples(10) dates is EXACTLY enough
    with pytest.raises(SignalContractError, match="too short"):
        assert_no_lookahead(short_momentum, _tiny_panel(19))
    assert_no_lookahead(short_momentum, _tiny_panel(20))  # must not raise


def test_import_time_io_is_caught(tmp_path, synthetic_panel):
    """A signal that caches data at module IMPORT time (before any compute
    call) must be blocked too — both loaders wrap exec_module in the guard."""
    from engine.errors import EngineError
    from engine.run_backtest import _load_signal_module

    side_file = tmp_path / "cache_me.parquet"
    synthetic_panel.to_parquet(side_file)
    signal_path = tmp_path / "signal.py"
    signal_path.write_text(
        "import pandas as pd\n"
        f"CACHED = pd.read_parquet(r'{side_file}')\n"
        "PARAMS = {}\n"
        "def compute_signal(panel):\n"
        "    return CACHED['close']\n"
    )
    with pytest.raises((PurityViolation, EngineError)):
        _load_signal_module(signal_path)
