import pandas as pd
import pytest

from engine.errors import EngineError
from engine.loader import load_panel
from engine.universe import eligible_symbols


def date(value):
    return pd.Timestamp(value, tz="UTC")


def member(symbol="A", listed="2023-01-02T00:00:00Z", delisted=None, known=None):
    return dict(
        symbol=symbol,
        listed_at=listed,
        listing_known_at=listed,
        delisted_at=delisted,
        delisting_known_at=known,
        source="synthetic exchange notice",
    )


def test_listings_delistings_and_publication_time():
    records = [
        member(),
        member("B", delisted="2023-01-04T00:00:00Z", known="2023-01-03T00:00:00Z"),
    ]
    assert eligible_symbols(records, date("2023-01-01")) == set()
    assert eligible_symbols(records, date("2023-01-03")) == {"A", "B"}
    assert eligible_symbols(records, date("2023-01-04")) == {"A"}
    # Late archival knowledge must not be injected into an earlier decision.
    records[0]["listing_known_at"] = "2023-01-05T00:00:00Z"
    assert eligible_symbols(records, date("2023-01-03")) == {"B"}


def test_missing_or_duplicate_membership_evidence_refused():
    for records in [
        [member(), member()],
        [{"symbol": "A"}],
        [member(delisted="2023-01-03T00:00:00Z")],
    ]:
        with pytest.raises(EngineError):
            eligible_symbols(records, date("2023-01-03"))


def frame():
    return pd.DataFrame(
        {c: [1.0, 1.0, 1.0] for c in ["open", "high", "low", "close", "volume"]},
        index=pd.date_range("2023-01-01", periods=3, tz="UTC", name="date"),
    )


def test_missing_symbols_and_gaps_are_not_imputed(tmp_path):
    f = frame().iloc[[0, 2]]
    f.to_parquet(tmp_path / "A.parquet")
    with pytest.raises(EngineError, match="symbols"):
        load_panel(tmp_path, expected_symbols=["A", "B"])
    panel = load_panel(tmp_path, expected_symbols=["A"])
    assert len(panel) == 2  # loader preserves gaps; held gap aborts execution


@pytest.mark.parametrize(
    "column,value",
    [("close", float("nan")), ("close", 0.0), ("volume", -1.0), ("high", 0.5)],
)
def test_invalid_bars_refused(tmp_path, column, value):
    f = frame()
    f.loc[f.index[1], column] = value
    f.to_parquet(tmp_path / "A.parquet")
    with pytest.raises(EngineError):
        load_panel(tmp_path)


def test_membership_is_applied_at_observation_not_future_fill():
    from engine.backtest import run_walkforward
    from tests.test_execution_v2 import panel
    from tests.test_known_answer import D1, D2, toy_signal
    # All assets become listed at D0: first observation (D0-1) is still empty.
    records = [member(f'S{i:02d}', listed='2023-01-01T00:00:00Z') for i in range(15)]
    run = run_walkforward(panel(), toy_signal, D1, D2, membership=records)
    assert not run.loc[D1, 'has_positions']
    assert run.loc[D1, 'r_net'] == 0
    assert run.loc[D2, 'has_positions']
