"""Shared signal-test harness (A3). Frozen zone: hypothesis test suites and
the S2 stage call these checks; they never reimplement them.

The four contract checks from PROJECT_BRIEF §4 S2:
  1. lookahead  — recompute on data truncated at t; the cross-section at t
                  must be identical for >= 10 sampled t (incl. the last date)
  2. determinism — two runs on identical input are exactly equal
  3. NaN handling — a mid-sample delisting must not crash the signal or
                  produce inf values
  4. index alignment — output is a float (date, symbol) Series whose pairs
                  all exist in the input panel
"""

import numpy as np
import pandas as pd

from engine.errors import EngineError


class LookaheadError(EngineError):
    """The signal's value at t changed when future rows were removed —
    it was reading the future."""


class SignalContractError(EngineError):
    """The signal violates the SPEC §2 output contract."""


def _cross_section(sig: pd.Series, date) -> pd.Series:
    if date in sig.index.get_level_values(0):
        return sig.xs(date, level=0).sort_index()
    return pd.Series(dtype="float64")


def assert_index_alignment(compute_signal, panel: pd.DataFrame) -> None:
    sig = compute_signal(panel)
    if not isinstance(sig, pd.Series):
        raise SignalContractError(f"returned {type(sig).__name__}, not pd.Series")
    if not isinstance(sig.index, pd.MultiIndex) or sig.index.nlevels != 2:
        raise SignalContractError("output index must be MultiIndex (date, symbol)")
    try:
        values = sig.astype("float64")
    except (TypeError, ValueError) as exc:
        raise SignalContractError(f"values not numeric: {exc}") from exc
    if np.isinf(values.to_numpy()).any():
        raise SignalContractError("output contains inf values")
    foreign = sig.index.difference(panel.index)
    if len(foreign) > 0:
        raise SignalContractError(
            f"{len(foreign)} output (date, symbol) pairs absent from the "
            f"input panel, e.g. {foreign[0]}"
        )


def assert_deterministic(compute_signal, panel: pd.DataFrame) -> None:
    first = compute_signal(panel)
    second = compute_signal(panel)
    try:
        pd.testing.assert_series_equal(first, second, check_exact=True)
    except AssertionError as exc:
        raise SignalContractError(f"non-deterministic output: {exc}") from exc


def assert_nan_handling(compute_signal, panel: pd.DataFrame) -> None:
    """Simulate a mid-sample delisting: the last symbol loses its final
    third of rows. The signal must not crash and must not emit inf."""
    dates = panel.index.get_level_values("date").unique().sort_values()
    symbols = panel.index.get_level_values("symbol").unique()
    victim = symbols[-1]
    cutoff = dates[int(len(dates) * 2 / 3)]
    mask = ~(
        (panel.index.get_level_values("symbol") == victim)
        & (panel.index.get_level_values("date") >= cutoff)
    )
    truncated_panel = panel[mask]
    sig = compute_signal(truncated_panel)
    if not isinstance(sig, pd.Series):
        raise SignalContractError("delisting panel: output is not a Series")
    values = sig.astype("float64").to_numpy()
    if np.isinf(values).any():
        raise SignalContractError("delisting panel: output contains inf")


def assert_no_lookahead(
    compute_signal,
    panel: pd.DataFrame,
    n_samples: int = 10,
    seed: int = 20260707,
    min_history: int = 10,
) -> None:
    """The core leak check. For sampled dates t: recompute the signal on the
    panel truncated at t and require the cross-section AT t to be identical
    to the full-panel run's. Any use of rows after t changes (or removes)
    the value at t and fails here."""
    dates = panel.index.get_level_values("date").unique().sort_values()
    if len(dates) <= min_history + 1:
        raise SignalContractError("panel too short for the lookahead check")

    eligible = dates[min_history:]
    rng = np.random.default_rng(seed)
    picks = rng.choice(len(eligible) - 1, size=min(n_samples - 1, len(eligible) - 1),
                       replace=False)
    sampled = [eligible[int(i)] for i in picks] + [eligible[-1]]  # always test the last date

    full_sig = compute_signal(panel)
    date_col = panel.index.get_level_values("date")
    for t in sampled:
        truncated = panel[date_col <= t]
        trunc_sig = compute_signal(truncated)
        full_cross = _cross_section(full_sig, t)
        trunc_cross = _cross_section(trunc_sig, t)
        try:
            pd.testing.assert_series_equal(
                full_cross, trunc_cross, check_exact=False, rtol=1e-12, atol=1e-15
            )
        except AssertionError as exc:
            raise LookaheadError(
                f"signal at {t.date()} changes when rows after {t.date()} are "
                f"removed — the signal reads the future. Detail: {str(exc)[:400]}"
            ) from exc


def run_full_harness(compute_signal, panel: pd.DataFrame) -> None:
    """All four checks; raises on the first violation."""
    assert_index_alignment(compute_signal, panel)
    assert_deterministic(compute_signal, panel)
    assert_nan_handling(compute_signal, panel)
    assert_no_lookahead(compute_signal, panel)
