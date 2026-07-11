# H001 — xsec_momentum_skip_week

card_version: 1
created: 2026-07-11T16:53:00+00:00
author_model: claude-fable-5

## Economic rationale

Crypto markets underreact to sustained price trends at the multi-week to multi-quarter horizon because the marginal participant is retail, and retail flow exhibits the disposition effect: holders sell winners too early to lock in gains and cling to losers hoping to break even. Those disposition-driven sellers are the other side of the long leg (they supply winners below fair continuation value), and break-even-anchored holders of losing coins are the other side of the short leg (they absorb supply that should reprice lower faster). They keep paying because the behavior is a psychological bias, not an information advantage — it does not learn from losses at the population level, and crypto's continuous retail inflow replenishes the biased cohort. Skipping the most recent week sidesteps the well-documented short-horizon reversal that would otherwise contaminate the trend signal with liquidity-provision noise.

## Testable prediction

Coins ranked in the top quintile by trailing return measured from 90 to 7 calendar days ago will outperform coins in the bottom quintile over the following day, persistently enough across the four walk-forward folds to clear the pre-registered criteria after 25 bps/side costs.

## Features required

Close only. Derived quantity: skip-adjusted trailing return `close(t - skip_days) / close(t - lookback_days) - 1` per symbol, where `lookback_days` is the TOTAL window (skip period included), so maximum history required equals `lookback_days`. Symbols lacking a close at either endpoint get NaN (no opinion). Max lookback: 120 calendar days (SPEC §2), reached only at the second grid point.

## Parameters (iteration 0)

```json
{"lookback_days": 90, "skip_days": 7}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{"lookback_days": 60, "skip_days": 7}, {"lookback_days": 120, "skip_days": 7}]
```

## Kill criteria (pre-registered)

```json
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}
```

All four thresholds are the SPEC §8 approved v1.0 defaults; no tuning, so no justification is required.

## Duplication check

The ledger is empty (this is hypothesis #1), so no prior entry — killed or otherwise — can be duplicated.