# H006 — vol_risk_premium_xsec

card_version: 1
created: 2026-07-12T03:25:00+00:00
author_model: claude-sonnet-5

## Economic rationale

Crypto perpetual/futures markets are dominated by leverage-constrained
retail and momentum-driven speculators who cannot (or will not) apply
leverage to "boring" low-volatility coins to hit their target return, so
instead they overweight high-realized-volatility coins for their
embedded convexity — the same leverage-clientele mechanism behind the
equity betting-against-beta anomaly (Frazzini & Pedersen), transplanted
to an asset class with even higher retail leverage usage via perps. This
persistent bid-up flow compresses the risk-adjusted forward returns of
high-vol coins and leaves low-vol coins comparatively neglected and
underpriced relative to their risk. The counterparty collecting the
premium is this same leverage-seeking flow: they systematically overpay
(in expectation, net of the extra risk they bear) for high-vol coins,
funding a long-low-vol/short-high-vol book. This flow is structural (driven
by leverage constraints and lottery preference, not information), so it is
expected to persist and re-price out on the same short lookback horizons
retail attention operates on, not to be arbitraged away instantly.

## Testable prediction

Ranking the 50-coin cross-section by trailing realized volatility of
daily close-to-close returns over the lookback window, the bottom quintile
(lowest realized volatility, held long) will exhibit a higher mean forward
daily net return than the top quintile (highest realized volatility, held
short), i.e. the dollar-neutral long-low/short-high-vol portfolio of
Section 3 has positive expected net return.

## Features required

`close` only: daily log returns `ln(close_t / close_{t-1})` computed over
a trailing window of `lookback_days` calendar days, from which the signal
is `-1 * realized_volatility` (std of those log returns), so lower
volatility ranks higher (more attractive / long side). No open/high/low/
volume used. Max lookback: `lookback_days` <= 120 (SPEC §2); iteration 0
uses 30 days.

## Parameters (iteration 0)

```json
{"lookback_days": 30}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{"lookback_days": 14}, {"lookback_days": 60}]
```

## Kill criteria (pre-registered)

```json
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}
```

Using the SPEC §8 v1.0 defaults unchanged — no tuning applied.

## Duplication check

Closest prior entries are #1 `xsec_momentum_skip_week` (ITERATED->
KILLED(merits), a return-based momentum signal) and #5
`short_horizon_reversal` (KILLED(infrastructure), never actually
backtested on its merits). Neither uses realized volatility as the
ranking variable — this hypothesis trades a risk-based (dispersion of
returns) cross-sectional signal rather than a directional return signal,
so it is economically distinct from both and is not a rerun of any killed
idea.