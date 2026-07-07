
"""
Data pipeline v2 for the autonomous quant research lab.
 
Changes from v1 (and why):
- CoinGecko removed: its free tier requires a key AND limits history to ~365
  days, so point-in-time market caps from 2022 are unavailable.
- Kraken removed: its public OHLC endpoint returns only the most recent ~720
  candles (~2 years daily), silently truncating a 4-year sample.
- New source: Coinbase Exchange public API (no key, US-accessible, full
  history via pagination).
- New universe method: rank by MEDIAN DAILY DOLLAR VOLUME over the first 90
  days of the sample. Anti-survivorship property preserved (ranking uses
  2022 activity, not today's). Stated limitation: coins delisted from
  Coinbase since then are absent.
 
Run in order:
 
    uv run python data_pipeline.py download-all --start 2022-07-01
    uv run python data_pipeline.py universe     --start 2022-07-01
    uv run python data_pipeline.py split        --cutoff 2025-07-01
 
Produces:
    data/all/{SYMBOL}.parquet   # every USD pair on the exchange (raw pool)
    data/universe.json          # top-50 by median $ volume at sample start
    data/raw/{SYMBOL}.parquet   # universe coins only
    data/train_val/, data/holdout/
 
Deps: ccxt, pandas, pyarrow  (requests no longer needed)
"""
 
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
 
import pandas as pd
 
DATA = Path("data")
ALL = DATA / "all"
RAW = DATA / "raw"
TRAIN = DATA / "train_val"
HOLDOUT = DATA / "holdout"
 
# Stablecoins, wrapped/staked derivatives, exchange tokens. Not tradable
# "bets" in a cross-sectional sense -- a momentum rank on USDT is noise.
EXCLUDE = {
    "USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "USDE", "PYUSD", "USDD",
    "USDS", "USD1", "GUSD", "USDP", "FRAX", "LUSD", "EURC", "EURT", "PAX",
    "WBTC", "WETH", "WBETH", "STETH", "WSTETH", "CBBTC", "CBETH", "WEETH",
    "RETH", "METH", "EETH", "SOLVBTC", "TBTC", "EZETH", "RSETH", "SUSDE",
    "JITOSOL", "MSOL", "BNSOL", "LEO", "WLUNA", "WCFG", "WAMPL", "WAXL",
}
 
 
def _utc(datestr: str) -> pd.Timestamp:
    return pd.Timestamp(datestr, tz="utc")
 
 
def _fetch_full_history(ex, market: str, since_ms: int) -> pd.DataFrame:
    """Paginate daily candles from `since_ms` to now. Coinbase Exchange caps
    at 300 candles per request, so a 4-year daily history is ~5 pages."""
    rows, since = [], since_ms
    while True:
        batch = ex.fetch_ohlcv(market, timeframe="1d", since=since, limit=300)
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + 86_400_000
        if nxt <= since or len(batch) < 2:
            break
        since = nxt
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.normalize()
    df = df.set_index("date").drop(columns=["ts"])
    # Drop today's partial candle: an unfinished bar is lookahead bait.
    df = df[df.index < pd.Timestamp.now(tz="utc").normalize()]
    return df
 
 
def cmd_download_all(start: str, exchange_id: str = "coinbaseexchange") -> None:
    """Download daily OHLCV for EVERY active USD spot pair. The universe is
    selected afterward from this pool, using volume measured AT SAMPLE START,
    which is what makes the selection point-in-time rather than survivorship-
    biased toward today's winners."""
    import ccxt
 
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    ex.load_markets()
    ALL.mkdir(parents=True, exist_ok=True)
    since_ms = int(_utc(start).timestamp() * 1000)
 
    bases = sorted({
        m["base"] for m in ex.markets.values()
        if m.get("spot") and m.get("quote") == "USD"
        and m.get("active") and m["base"] not in EXCLUDE
    })
    print(f"{len(bases)} candidate USD pairs on {exchange_id}")
 
    report = {"ok": [], "empty": [], "failed": []}
    for i, base in enumerate(bases):
        market = f"{base}/USD"
        try:
            df = _fetch_full_history(ex, market, since_ms)
            if df.empty:
                report["empty"].append(base)
                continue
            df.to_parquet(ALL / f"{base}.parquet")
            report["ok"].append(base)
            print(f"[{i+1}/{len(bases)}] {base}: {len(df)} bars "
                  f"({df.index.min().date()} -> {df.index.max().date()})")
        except Exception as e:  # noqa: BLE001 -- log-and-continue by design
            report["failed"].append({"symbol": base, "error": str(e)[:200]})
            print(f"[{i+1}/{len(bases)}] {base}: FAILED ({e})")
            time.sleep(2)
 
    DATA.joinpath("download_report.json").write_text(json.dumps(report, indent=2))
    print(f"\nok={len(report['ok'])} empty={len(report['empty'])} "
          f"failed={len(report['failed'])} -> data/download_report.json")
 
 
def cmd_universe(start: str, window_days: int = 90, top_n: int = 50) -> None:
    """Select the universe: coins listed by (start + 30d), ranked by median
    daily dollar volume over [start, start + window]. Median, not mean, so a
    single listing-day spike can't buy a coin into the universe."""
    start_ts = _utc(start)
    window_end = start_ts + pd.Timedelta(days=window_days)
    listed_by = start_ts + pd.Timedelta(days=30)
 
    rows = []
    for f in sorted(ALL.glob("*.parquet")):
        df = pd.read_parquet(f)
        if df.index.min() > listed_by:
            continue  # listed too late -- would inject post-hoc knowledge
        win = df[(df.index >= start_ts) & (df.index < window_end)]
        if len(win) < window_days * 0.8:
            continue  # too gappy in the ranking window to trust
        dollar_vol = float((win["close"] * win["volume"]).median())
        rows.append({"symbol": f.stem, "median_daily_dollar_vol": dollar_vol,
                     "first_bar": str(df.index.min().date()),
                     "last_bar": str(df.index.max().date())})
 
    rows.sort(key=lambda r: r["median_daily_dollar_vol"], reverse=True)
    universe = rows[:top_n]
 
    RAW.mkdir(parents=True, exist_ok=True)
    for c in universe:
        (RAW / f"{c['symbol']}.parquet").write_bytes(
            (ALL / f"{c['symbol']}.parquet").read_bytes())
 
    out = {
        "as_of": start,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "method": f"top-{top_n} by median daily dollar volume over first "
                  f"{window_days} days of sample; must be listed within 30 "
                  f"days of start; stablecoins/wrapped/staked excluded",
        "stated_limitations": [
            "Coins delisted from Coinbase before today are absent from the "
            "candidate pool (survivorship residue at the venue level).",
            "Single-venue volume; cross-exchange volume not considered.",
        ],
        "coins": universe,
    }
    (DATA / "universe.json").write_text(json.dumps(out, indent=2))
    print(f"Universe: {len(universe)} coins -> data/universe.json, data/raw/")
    for c in universe[:10]:
        print(f"  {c['symbol']}: ${c['median_daily_dollar_vol']:,.0f}/day")
 
 
def cmd_split(cutoff: str) -> None:
    """Physically separate holdout. After this, MOVE data/holdout outside the
    repo. The overnight loop must be unable to peek, not told not to."""
    cut = _utc(cutoff)
    TRAIN.mkdir(parents=True, exist_ok=True)
    HOLDOUT.mkdir(parents=True, exist_ok=True)
    files = sorted(RAW.glob("*.parquet"))
    if not files:
        raise SystemExit("data/raw is empty -- run download-all and universe first")
    for f in files:
        df = pd.read_parquet(f)
        df[df.index < cut].to_parquet(TRAIN / f.name)
        df[df.index >= cut].to_parquet(HOLDOUT / f.name)
    print(f"Split {len(files)} files at {cutoff}. NOW run:")
    print("  mv data/holdout ~/quantlab_holdout_DO_NOT_MOUNT")
 
 
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download-all")
    d.add_argument("--start", required=True)
    d.add_argument("--exchange", default="coinbaseexchange")
    u = sub.add_parser("universe")
    u.add_argument("--start", required=True)
    u.add_argument("--window-days", type=int, default=90)
    u.add_argument("--top-n", type=int, default=50)
    s = sub.add_parser("split")
    s.add_argument("--cutoff", required=True)
    a = p.parse_args()
    if a.cmd == "download-all":
        cmd_download_all(a.start, a.exchange)
    elif a.cmd == "universe":
        cmd_universe(a.start, a.window_days, a.top_n)
    elif a.cmd == "split":
        cmd_split(a.cutoff)
 