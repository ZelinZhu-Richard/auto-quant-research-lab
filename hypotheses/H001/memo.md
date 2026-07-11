# Memo — H001 xsec_momentum_skip_week

decision: KILL(merits)
date: 2026-07-11T16:55:00+00:00

## Rationale (from the hypothesis card)

Crypto underreacts to multi-week trends because the marginal participant is retail, and retail exhibits the disposition effect: selling winners too early and clinging to losers anchored at break-even. Those disposition-driven sellers supply the long leg below fair continuation value, and break-even-anchored holders of losers are the other side of the short leg. Skipping the most recent week was meant to sidestep short-horizon reversal contaminating the trend signal.

## Method

Signal: skip-adjusted trailing return `close(t - skip_days) / close(t - lookback_days) - 1` per symbol (close only; NaN if either endpoint missing), ranked cross-sectionally each day. Parameters run across the pre-registered grid: iteration 0 `{lookback_days: 90, skip_days: 7}`, iteration 1 `{lookback_days: 60, skip_days: 7}`, iteration 2 `{lookback_days: 120, skip_days: 7}` (results below are iteration 2, the final iteration). Fixed engine treatment throughout: quintile long-short, dollar-neutral, 25 bps/side costs, 1-day execution lag, evaluated over 4 sequential walk-forward folds (2022-11-01 to 2025-06-30, 973 position days).

## Results

| metric | aggregate | required |
|---|---|---|
| sharpe (ann., net) | -0.86 | >= 1.0 |
| max drawdown | 0.468 | <= 0.30 |
| hit rate | 0.490 | >= 0.50 |
| sign-consistent folds | 3/4 | >= 3 |
| deflated sharpe (N=1) | 0.078 | >= 0.95 |

Per-fold sharpe: -0.74, 0.63, -1.41, -1.76.

## Decision

KILL on merits, per the referee (codex gpt-5) at iteration 2. The pre-registered minimum Sharpe of 1.0 failed with an observed annualized Sharpe of -0.8645; the pre-registered maximum drawdown of 0.30 failed with 0.4681; and the pre-registered minimum hit rate of 0.50 failed with 0.4902. The pre-registered stability requirement of at least 3 sign-consistent folds passed at 3/4, while the required deflated Sharpe of 0.95 failed with 0.0782. Because not all criteria passed and this was iteration 2 (the iteration grid was exhausted), the mechanical decision is KILL on merits.

## Lineage

- git commit: fcd2f620c0bbd1793e7f8595250e1875f5254c03
- data manifest sha256: 885599371d82fa70ed4f1faf4b9fba6d8857aca4cbb53b7ccebc7a1ad90b1a3b
- hypothesis card version: 1
- iterations run: [{"lookback_days": 90, "skip_days": 7}, {"lookback_days": 60, "skip_days": 7}, {"lookback_days": 120, "skip_days": 7}]