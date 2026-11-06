# H005 — short_horizon_reversal

card_version: 1
created: 2026-07-12T03:22:00+00:00
author_model: claude-sonnet-5

## Economic rationale

Crypto perpetual and spot markets are dominated by leveraged, trend-chasing retail flow and systematic momentum/CTA-style strategies operating on short (1-14 day) horizons. When a coin moves sharply over a week, this flow pushes price past fair value: longs pile in on strength (and get cascade-liquidated on any pullback), shorts pile in on weakness (and get squeezed on any bounce). The counterparty paying for this trade is precisely that leveraged momentum/retail flow — they buy strength and sell weakness at prices that overshoot, then are forced (by margin calls, stop-losses, or simple regret-driven unwinding) to trade back through fair value days later. We are on the other side: providing liquidity by buying the week's losers and selling the week's winners, capturing the reversion as the overshoot corrects and as market makers who absorbed the initial imbalance unwind their inventory at a markup. This is a distinct, opposite-signed mechanism from H001 (skip-week momentum, which bet on continuation); here we bet on short-horizon overreaction and reversal instead.

## Testable prediction

Over the subsequent return day, coins in the bottom quintile of trailing lookback_days close-to-close return will cross-sectionally outperform coins in the top quintile of trailing lookback_days close-to-close return, on a dollar-neutral long-short basis, net of costs.

## Features required

Only `close`. Derived quantity: trailing cumulative return
`ret(t) = close(t) / close(t - lookback_days) - 1`, computed per symbol from
data up to and including the signal date. Signal value = `-ret(t)` (lower
recent return ranks higher / more attractive to go long). Max lookback:
14 calendar days at the widest grid point, well within the 120-day SPEC
limit.

## Parameters (iteration 0)

```json
{"lookback_days": 7}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{"lookback_days": 5}, {"lookback_days": 14}]
```

## Kill criteria (pre-registered)

```json
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}
```

Using SPEC §8 v1.0 defaults with no deviation.

## Duplication check

Closest LEDGER entry is #1 `xsec_momentum_skip_week` (KILLED, merits),
which bet on continuation of medium-term momentum after skipping the most
recent week. H005 is the opposite mechanism (short-horizon reversal at
5-14 days, sign flipped, no skip-week gap) and is not a rerun of that idea.
#2-#4 are infrastructure kills with no recorded economic idea, so there is
nothing there to duplicate.