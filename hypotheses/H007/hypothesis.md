# H007 — xsec_amihud_illiquidity_premium

card_version: 1
created: 2026-07-12T03:23:04+00:00
author_model: claude-sonnet-5

## Economic rationale

Illiquid coins carry a compensation-for-friction premium: when a coin's order book is thin, anyone who needs to trade *now* (a leveraged trader meeting a margin call, a fund unwinding a position ahead of a redemption, an exchange auto-deleveraging a liquidation) moves the price against themselves to get filled. That price concession is a transfer to whoever is willing to take the other side and hold through the temporary dislocation — patient capital with no immediacy need. The trade's counterparty is specifically these liquidity-demanders: forced/urgent sellers in thin names who accept a worse execution price, and forced/urgent buyers who pay up, both of whom compensate the liquidity supplier (us) for warehousing the position until the price recovers toward fair value. This is the crypto analogue of the Amihud (2002) illiquidity premium in equities, and it should be structurally present in a 50-coin panel where liquidity varies enormously by market cap and listing venue.

## Testable prediction

Coins with higher trailing Amihud illiquidity (mean absolute daily return per dollar of trading volume) will cross-sectionally outperform, on a risk-adjusted basis, coins with lower trailing illiquidity over the subsequent trading day, when ranked and long/short as specified in SPEC §3.

## Features required

`close`, `volume` (for dollar volume = `close * volume`). Derived: daily simple return `r_t = close_t/close_{t-1} - 1`; Amihud illiquidity ratio per day `|r_t| / (close_t * volume_t)`; signal = mean of this ratio over the trailing lookback window (lookback_days, max 120 per SPEC §2). No `open`/`high`/`low` used. Long the most illiquid (highest ratio), short the most liquid (lowest ratio) — higher signal value = more illiquid = long side, matching SPEC §3's "higher = more attractive" convention.

## Parameters (iteration 0)

```json
{"lookback_days": 90}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{"lookback_days": 60}, {"lookback_days": 120}]
```

## Kill criteria (pre-registered)

```json
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}
```

Using SPEC v1.0 defaults unchanged — no tuning applied.

## Duplication check

Closest prior entries are #6 `vol_risk_premium_xsec` (KILLED/infrastructure, never backtested — a realized-volatility risk-premium sort, not a liquidity-cost measure) and #5 `short_horizon_reversal` (KILLED/infrastructure — a short-term mean-reversion signal on returns alone, no volume). Neither reached a backtest, and neither uses a volume-normalized price-impact construction, so this is not a rerun of a killed idea; #1 `xsec_momentum_skip_week` is a pure return-momentum signal and shares no construction with an Amihud-style illiquidity ratio.