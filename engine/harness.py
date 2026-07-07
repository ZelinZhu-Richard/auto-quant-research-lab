"""Shared signal-test harness (A3). Frozen zone: hypothesis test suites and
the S2 stage call these checks; they never reimplement them.

The four contract checks from PROJECT_BRIEF §4 S2:
  1. lookahead  — recompute on data truncated at t; any value the signal
                  reports for date t must be identical for >= 10 sampled t
                  (incl. the last date)
  2. determinism — two runs on identical input are exactly equal
  3. NaN handling — a mid-sample delisting must not crash the signal or
                  produce inf values
  4. index alignment — output is a float (date, symbol) Series whose pairs
                  all exist in the input panel

Defense layers, and what each does NOT cover:
- The engine itself slices the panel at each signal date, so a signal that
  only computes from its input can never see the future in production.
- The truncate-and-compare check here catches history-returning signals
  whose past values change when future rows arrive.
- Signals that return ONLY the last date's cross-section have no past
  values to compare; for them the engine's slice-per-call construction is
  the guarantee, and the fabrication check (no output dates beyond the
  truncation point) still applies.
- The remaining leak vector is a signal doing its own I/O (e.g. loading
  data/train_val directly and computing tomorrow's return). Every harness
  invocation therefore runs under an I/O guard that fails the signal if it
  calls open()/pandas readers/pyarrow parquet readers at compute time.
  A signal could still bypass this with exotic I/O (ctypes, sockets); the
  structural backstops are the read-only Docker mounts and R2's physical
  absence of holdout data.
"""

import builtins
import contextlib
from unittest import mock

import numpy as np
import pandas as pd

from engine.errors import EngineError


class LookaheadError(EngineError):
    """The signal's value at t changed when future rows were removed —
    it was reading the future."""


class SignalContractError(EngineError):
    """The signal violates the SPEC §2 output contract."""


@contextlib.contextmanager
def _forbid_io():
    """Fail any compute_signal that reads files at call time (purity,
    SPEC §2). Patches the common entry points: builtins.open, pandas
    readers, pyarrow parquet readers."""

    def _blocked(*_args, **_kwargs):
        raise SignalContractError(
            "signal performed file I/O during compute_signal (purity "
            "violation, SPEC §2)"
        )

    patches = [mock.patch.object(builtins, "open", _blocked)]
    for name in ("read_parquet", "read_csv", "read_json", "read_pickle",
                 "read_feather", "read_orc", "read_hdf", "read_table"):
        if hasattr(pd, name):
            patches.append(mock.patch.object(pd, name, _blocked))
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415 — optional guard target

        for name in ("read_table", "ParquetFile", "read_pandas"):
            if hasattr(pq, name):
                patches.append(mock.patch.object(pq, name, _blocked))
    except ImportError:
        pass

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


def _call(compute_signal, panel: pd.DataFrame) -> pd.Series:
    with _forbid_io():
        return compute_signal(panel)


def _cross_section(sig: pd.Series, date) -> pd.Series:
    if date in sig.index.get_level_values(0):
        return sig.xs(date, level=0).sort_index()
    return pd.Series(dtype="float64")


def assert_index_alignment(compute_signal, panel: pd.DataFrame) -> None:
    sig = _call(compute_signal, panel)
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
    first = _call(compute_signal, panel)
    second = _call(compute_signal, panel)
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
    sig = _call(compute_signal, truncated_panel)
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
    """The core leak check, for every value the signal REPORTS for a date t:
    recompute on the panel truncated at t; the cross-section at t must be
    identical to the full-panel run's cross-section at t.

    Two hard guarantees regardless of what the signal returns:
    - fabrication: a truncated run may not emit any date past its cut;
    - history consistency: any date t present in BOTH runs must agree.
    Signals that return only the last date's cross-section skip the second
    comparison by construction (nothing to compare) — the engine's
    slice-per-call design covers them (see module docstring).
    """
    dates = panel.index.get_level_values("date").unique().sort_values()
    if len(dates) - min_history - 1 < n_samples:
        raise SignalContractError(
            f"panel too short: need >= {n_samples} sampled dates after "
            f"{min_history} burn-in days, have {max(len(dates) - min_history - 1, 0)}"
        )

    eligible = dates[min_history:]
    rng = np.random.default_rng(seed)
    picks = rng.choice(len(eligible) - 1, size=n_samples - 1, replace=False)
    sampled = [eligible[int(i)] for i in picks] + [eligible[-1]]  # always test the last date

    full_sig = _call(compute_signal, panel)
    if not isinstance(full_sig, pd.Series) or not isinstance(full_sig.index, pd.MultiIndex):
        raise SignalContractError("output must be a Series with MultiIndex (date, symbol)")
    date_col = panel.index.get_level_values("date")
    for t in sampled:
        truncated = panel[date_col <= t]
        trunc_sig = _call(compute_signal, truncated)
        trunc_dates = trunc_sig.index.get_level_values(0)
        if len(trunc_sig) and (trunc_dates > t).any():
            raise LookaheadError(
                f"signal run on data truncated at {t.date()} emitted dates "
                f"beyond the truncation point — fabricated future output"
            )
        full_cross = _cross_section(full_sig, t)
        if full_cross.empty:
            continue  # last-date-only signal: nothing reported for t
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
