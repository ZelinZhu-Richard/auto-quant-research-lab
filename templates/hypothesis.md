# {HYPOTHESIS_ID} — {short_name}

card_version: 1
created: {iso_timestamp}
author_model: {model_id}

## Economic rationale

{Why should this edge exist? REQUIRED: who is on the other side of the
trade, and why do they keep paying? 2-6 sentences.}

## Testable prediction

{One falsifiable sentence about cross-sectional ranking behavior.}

## Features required

{Which of open/high/low/close/volume, and what derived quantities.
Max lookback: <= 120 calendar days (SPEC §2).}

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

{If any threshold differs from the SPEC §8 defaults, justify it here in
one sentence. Criteria are frozen the moment this file is written (R3).}

## Duplication check

{One line: which of the last 30 LEDGER entries are closest to this idea,
and why this is not a rerun of a killed one.}
