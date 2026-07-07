import numpy as np
import pandas as pd

from engine.portfolio import build_weights


def _symbols(n):
    return [f"S{i:02d}" for i in range(n)]


def test_quintile_weights_15_symbols():
    syms = _symbols(15)
    signal = pd.Series(np.arange(15, dtype=float), index=syms)
    closes = pd.Series(100.0, index=syms)
    w = build_weights(signal, closes)
    # q = 3: longs = highest signal (S14,S13,S12), shorts = lowest (S02,S01,S00)
    for s in ["S14", "S13", "S12"]:
        assert abs(w[s] - 0.5 / 3) < 1e-15
    for s in ["S02", "S01", "S00"]:
        assert abs(w[s] + 0.5 / 3) < 1e-15
    assert abs(w.sum()) < 1e-15            # dollar-neutral
    assert abs(w.abs().sum() - 1.0) < 1e-15  # gross exposure 1.0
    assert (w[["S03", "S07", "S11"]] == 0).all()


def test_tie_break_is_symbol_alphabetical():
    syms = _symbols(15)
    signal = pd.Series(1.0, index=syms)  # all tied
    closes = pd.Series(100.0, index=syms)
    w = build_weights(signal, closes)
    # (signal DESC, symbol ASC): longs = S00,S01,S02 ; shorts = S12,S13,S14
    assert (w[["S00", "S01", "S02"]] > 0).all()
    assert (w[["S12", "S13", "S14"]] < 0).all()


def test_nan_signal_excluded():
    syms = _symbols(16)
    values = np.arange(16, dtype=float)
    signal = pd.Series(values, index=syms)
    signal["S15"] = np.nan  # highest-value symbol has no opinion
    closes = pd.Series(100.0, index=syms)
    w = build_weights(signal, closes)
    assert w["S15"] == 0.0
    assert w["S14"] > 0  # next-best becomes the top of the book


def test_missing_close_excluded():
    syms = _symbols(16)
    signal = pd.Series(np.arange(16, dtype=float), index=syms)
    closes = pd.Series(100.0, index=syms)
    closes["S15"] = np.nan  # no bar that day
    w = build_weights(signal, closes)
    assert w["S15"] == 0.0


def test_degenerate_cross_section_returns_zero_weights():
    syms = _symbols(14)  # q = 2 < 3
    signal = pd.Series(np.arange(14, dtype=float), index=syms)
    closes = pd.Series(100.0, index=syms)
    w = build_weights(signal, closes)
    assert (w == 0.0).all()


def test_signal_symbols_outside_universe_ignored():
    syms = _symbols(15)
    signal = pd.Series(np.arange(16, dtype=float), index=syms + ["GHOST"])
    closes = pd.Series(100.0, index=syms)
    w = build_weights(signal, closes)
    assert "GHOST" not in w.index
    assert abs(w.abs().sum() - 1.0) < 1e-15
