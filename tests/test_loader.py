from pathlib import Path

import pandas as pd
import pytest

from engine.errors import EngineError
from engine.loader import data_manifest_sha256, load_panel

DATA_DIR = Path(__file__).parent.parent / "data" / "train_val"

needs_data = pytest.mark.skipif(
    not DATA_DIR.exists(), reason="data/train_val not present (gitignored)"
)


@needs_data
def test_load_real_panel():
    panel = load_panel(DATA_DIR)
    assert panel.index.names == ["date", "symbol"]
    assert list(panel.columns) == ["open", "high", "low", "close", "volume"]
    assert panel.index.is_monotonic_increasing
    assert len(panel.index.get_level_values("symbol").unique()) == 50
    dates = panel.index.get_level_values("date")
    assert dates.min() == pd.Timestamp("2022-07-01", tz="UTC")
    assert dates.max() == pd.Timestamp("2025-06-30", tz="UTC")
    assert (panel.dtypes == "float64").all()


@needs_data
def test_manifest_is_deterministic():
    assert data_manifest_sha256(DATA_DIR) == data_manifest_sha256(DATA_DIR)
    assert len(data_manifest_sha256(DATA_DIR)) == 64


def test_loader_rejects_bad_schema(tmp_path):
    bad = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.DatetimeIndex(
            ["2023-01-01", "2023-01-02"], tz="UTC", name="date"
        ),
    )
    bad.to_parquet(tmp_path / "BAD.parquet")
    with pytest.raises(EngineError, match="columns"):
        load_panel(tmp_path)


def test_loader_rejects_naive_index(tmp_path):
    bad = pd.DataFrame(
        {c: [1.0, 2.0] for c in ["open", "high", "low", "close", "volume"]},
        index=pd.DatetimeIndex(["2023-01-01", "2023-01-02"], name="date"),
    )
    bad.to_parquet(tmp_path / "NAIVE.parquet")
    with pytest.raises(EngineError, match="tz-aware"):
        load_panel(tmp_path)


def test_empty_dir_raises(tmp_path):
    with pytest.raises(EngineError, match="no parquet files"):
        load_panel(tmp_path)
