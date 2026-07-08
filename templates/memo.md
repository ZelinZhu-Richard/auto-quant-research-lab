# Memo — {HYPOTHESIS_ID} {short_name}

decision: {KILL(merits) | KILL(infrastructure) | PROMOTE}
date: {iso_timestamp}

## Rationale (from the hypothesis card)

{2-3 sentences. What was the idea and who was supposed to be on the other
side of the trade.}

## Method

{1 paragraph: signal definition, params run (all iterations), the fixed
engine treatment: quintile long-short, dollar-neutral, 25bps/side,
1-day lag, 4 sequential folds.}

## Results

| metric | aggregate | required |
|---|---|---|
| sharpe (ann., net) | {x} | >= {k} |
| max drawdown | {x} | <= {k} |
| hit rate | {x} | >= {k} |
| sign-consistent folds | {n}/4 | >= {k} |
| deflated sharpe (N={n_trials}) | {x} | >= 0.95 |

Per-fold sharpe: {f1}, {f2}, {f3}, {f4}.

## Decision

{One paragraph, verbatim-consistent with decision.json's justification:
which pre-registered numbers passed/failed. For infra-kills: what broke.}

## Lineage

- git commit: {hash of repo HEAD when the backtest ran}
- data manifest sha256: {from results.json}
- hypothesis card version: {card_version}
- iterations run: {list}
