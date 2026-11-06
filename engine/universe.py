"""Point-in-time membership from supplied evidence, never inferred from bars.

This module ships no listing/delisting history. Current-active markets and
first/last observed candles are insufficient evidence for a historical universe.
"""

import pandas as pd
from engine.errors import EngineError


def _date(value):
    try:
        date = pd.Timestamp(value)
        if date.tz is None or str(date.tz) != "UTC" or date != date.normalize():
            raise ValueError("require UTC midnight")
        return date
    except (ValueError, TypeError) as exc:
        raise EngineError("invalid membership date") from exc


def eligible_symbols(records: list[dict], as_of: pd.Timestamp) -> set[str]:
    if not isinstance(records, list) or not records:
        raise EngineError("missing historical membership records")
    eligible, seen = set(), set()
    for record in records:
        try:
            symbol = record["symbol"]
            if not isinstance(symbol, str) or not symbol or symbol in seen:
                raise EngineError("duplicate/invalid membership symbol")
            seen.add(symbol)
            if not isinstance(record["source"], str) or not record["source"].strip():
                raise EngineError("membership source evidence required")
            listed, known = (
                _date(record["listed_at"]),
                _date(record["listing_known_at"]),
            )
            delisted = (
                _date(record["delisted_at"]) if record.get("delisted_at") else None
            )
            delist_known = (
                _date(record["delisting_known_at"])
                if record.get("delisting_known_at")
                else None
            )
            if (delisted is None) != (delist_known is None) or (
                delisted is not None and delisted <= listed
            ):
                raise EngineError("inconsistent delisting evidence")
            if as_of >= max(listed, known) and (
                delisted is None or as_of < max(delisted, delist_known)
            ):
                eligible.add(symbol)
        except (KeyError, TypeError) as exc:
            raise EngineError("incomplete membership evidence") from exc
    return eligible
