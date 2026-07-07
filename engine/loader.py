"""Panel loader for data/train_val (SPEC §1).

Built against the REAL parquet schema: DatetimeIndex "date" of dtype
datetime64[ms, UTC] — millisecond resolution, not ns — with float64
open/high/low/close/volume. All date logic is resolution-agnostic:
tz-aware comparisons only, no ns assumptions.
"""

import hashlib
from pathlib import Path

import pandas as pd

from engine.errors import EngineError

COLUMNS = ["open", "high", "low", "close", "volume"]


def data_manifest_sha256(data_dir: str | Path) -> str:
    """sha256 over sorted 'filename:sha256(file)' lines — the data lineage
    stamp recorded in results.json and memos (SPEC §1)."""
    lines = []
    for f in sorted(Path(data_dir).glob("*.parquet")):
        file_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{f.name}:{file_hash}")
    if not lines:
        raise EngineError(f"no parquet files in {data_dir}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _validate_frame(symbol: str, df: pd.DataFrame) -> None:
    if list(df.columns) != COLUMNS:
        raise EngineError(f"{symbol}: columns {list(df.columns)} != {COLUMNS}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise EngineError(f"{symbol}: index is not a DatetimeIndex")
    if df.index.tz is None or str(df.index.tz) != "UTC":
        raise EngineError(f"{symbol}: index tz {df.index.tz!r} is not UTC")
    if not (df.index == df.index.normalize()).all():
        raise EngineError(f"{symbol}: non-midnight timestamps (daily bars required)")
    if df.index.name != "date":
        raise EngineError(f"{symbol}: index name {df.index.name!r} != 'date'")
    if not df.index.is_monotonic_increasing:
        raise EngineError(f"{symbol}: index not sorted")
    if not df.index.is_unique:
        raise EngineError(f"{symbol}: duplicate dates")
    if not all(df.dtypes == "float64"):
        raise EngineError(f"{symbol}: non-float64 columns {dict(df.dtypes.astype(str))}")


def load_panel(data_dir: str | Path) -> pd.DataFrame:
    """Load every {SYMBOL}.parquet into one MultiIndex (date, symbol)
    DataFrame, lexsorted date-major, columns open/high/low/close/volume.

    Tolerates late listings and calendar gaps (a symbol simply has no rows
    on missing dates); the current dataset happens to be gapless."""
    data_dir = Path(data_dir)
    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        raise EngineError(f"no parquet files in {data_dir}")
    frames: dict[str, pd.DataFrame] = {}
    for f in files:
        df = pd.read_parquet(f)
        _validate_frame(f.stem, df)
        frames[f.stem] = df
    panel = pd.concat(frames, names=["symbol", "date"])
    panel = panel.swaplevel("symbol", "date").sort_index()
    return panel
