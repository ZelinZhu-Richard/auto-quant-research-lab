"""Workaround runner for data_pipeline.py download-all.

data_pipeline.py (protected file, must not be modified) paginates daily
candles until the next `since` passes "now"; Coinbase Exchange then raises
ExchangeError("Start cannot be in the future") instead of returning an empty
batch, so _fetch_full_history's `if not batch: break` never fires and every
actively-trading symbol is discarded as FAILED (observed: ok=2 of 394).

Fix, without touching the pipeline: subclass the ccxt exchange so fetch_ohlcv
returns [] for exactly this error. The pipeline's own loop then terminates
normally. All pipeline logic (pagination, dedup, partial-candle drop,
reporting) runs unmodified.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import ccxt

_orig = ccxt.coinbaseexchange


class PatchedCoinbaseExchange(_orig):
    def fetch_ohlcv(self, *args, **kwargs):
        try:
            return super().fetch_ohlcv(*args, **kwargs)
        except ccxt.BaseError as e:
            if "start cannot be in the future" in str(e).lower():
                return []
            raise


ccxt.coinbaseexchange = PatchedCoinbaseExchange

import data_pipeline as dp  # noqa: E402

dp.cmd_download_all(sys.argv[1] if len(sys.argv) > 1 else "2022-07-01")
