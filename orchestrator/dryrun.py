"""DRY-RUN providers (A5 acceptance): canned hypotheses + canned signals +
a mechanical referee, so two full cycles run end-to-end with ZERO LLM calls
while every other moving part (harness tests, real engine backtest, ledger,
memos, per-stage commits, toolcall log) is exercised for real."""

import json
import re

CANNED = [
    {
        "name": "dry_momentum_90d",
        "hypothesis_md": """# {hid} — dry_momentum_90d

card_version: 1
created: {ts}
author_model: dry-run-mock

## Economic rationale

Coins that outperformed over the past quarter keep outperforming because
retail flows chase visible winners with a lag. The other side of the trade:
late mean-reversion sellers who fade strength too early and fund the
continuation.

## Testable prediction

Cross-sectional rank of trailing 90d return at t predicts the rank of
close(t)->close(t+1) returns.

## Features required

close only; trailing 90-day percentage change. Max lookback 90 days.

## Parameters (iteration 0)

```json
{{"lookback_days": 90}}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{{"lookback_days": 60}}, {{"lookback_days": 120}}]
```

## Kill criteria (pre-registered)

```json
{{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}}
```

SPEC §8 defaults, unmodified.

## Duplication check

Ledger is empty or contains no momentum variant; not a rerun.
""",
        "signal_py": '''"""Dry-run canned signal: cross-sectional price momentum."""
import pandas as pd

PARAMS = {"lookback_days": 90}


def compute_signal(panel: pd.DataFrame) -> pd.Series:
    closes = panel["close"].unstack("symbol")
    momentum = closes.pct_change(PARAMS["lookback_days"], fill_method=None)
    return momentum.stack()
''',
    },
    {
        "name": "dry_reversal_7d",
        "hypothesis_md": """# {hid} — dry_reversal_7d

card_version: 1
created: {ts}
author_model: dry-run-mock

## Economic rationale

Short-horizon crypto moves overshoot because leveraged liquidations cascade
past fair value; the bounce-back is harvestable. The other side of the
trade: forced deleveraging and stop-loss sellers who transact at any price.

## Testable prediction

The bottom quintile of trailing 7d returns outperforms the top quintile
over the next day.

## Features required

close only; trailing 7-day percentage change, negated. Max lookback 7 days.

## Parameters (iteration 0)

```json
{{"lookback_days": 7}}
```

## Iteration grid (pre-registered, ordered, max 2)

```json
[{{"lookback_days": 3}}]
```

## Kill criteria (pre-registered)

```json
{{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}}
```

SPEC §8 defaults, unmodified.

## Duplication check

Only the momentum dry-run precedes this; reversal is its opposite, not a
duplicate.
""",
        "signal_py": '''"""Dry-run canned signal: short-term cross-sectional reversal."""
import pandas as pd

PARAMS = {"lookback_days": 7}


def compute_signal(panel: pd.DataFrame) -> pd.Series:
    closes = panel["close"].unstack("symbol")
    reversal = -closes.pct_change(PARAMS["lookback_days"], fill_method=None)
    return reversal.stack()
''',
    },
]


def mechanical_referee(hypothesis_blocks: dict, results: dict,
                       dsr_threshold: float = 0.95) -> dict:
    """Compatibility helper; the shared validated rule remains authoritative."""
    from orchestrator.rule import DSR_THRESHOLD, decide
    if dsr_threshold != DSR_THRESHOLD:
        raise ValueError("SPEC DSR threshold is fixed")
    return decide(hypothesis_blocks, results, referee_model="dry-run-mechanical-referee")


def canned_memo(hypothesis_md: str, results: dict, decision: dict) -> str:
    name_match = re.search(r"# H\d+ — (\S+)", hypothesis_md)
    name = name_match.group(1) if name_match else "unknown"
    agg = results.get("aggregate", {})
    folds = results.get("folds", [])
    lines = [
        f"# Memo — {results['hypothesis_id']} {name}",
        "",
        f"decision: {decision['decision']}"
        + (f"({decision['kill_reason']})" if decision.get("kill_reason") else ""),
        f"date: {results['generated_at']}",
        "",
        "## Rationale",
        "",
        "(dry-run canned memo; see hypothesis.md for the full card)",
        "",
        "## Results",
        "",
        f"- sharpe (ann., net): {agg.get('sharpe_annualized', 'nan')}",
        f"- max drawdown: {agg.get('max_drawdown', 'nan')}",
        f"- hit rate: {agg.get('hit_rate', 'nan')}",
        f"- deflated sharpe: {results.get('referee_inputs', {}).get('deflated_sharpe', 'nan')}",
        f"- per-fold sharpe: {[round(f['sharpe_annualized'], 3) for f in folds]}",
        f"- iterations run: {[h['params'] for h in results.get('iteration_history', [])]}",
        "",
        "## Decision",
        "",
        decision["justification"],
        "",
        "## Lineage",
        "",
        f"- git commit: {results.get('engine', {}).get('git_commit', 'unknown')}",
        f"- data manifest sha256: {results.get('engine', {}).get('data_manifest_sha256', 'unknown')}",
        "- hypothesis card version: 1",
    ]
    return "\n".join(lines) + "\n"


def render(template: str, hid: str, ts: str) -> str:
    return template.format(hid=hid, ts=ts)
