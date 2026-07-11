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
    """SPEC §8 decision rule, implemented mechanically — the dry-run stand-in
    for the codex referee AND the reference implementation for SPEC §13's
    acceptance test ('a different model reaches the same decision')."""
    criteria = hypothesis_blocks["criteria"]
    grid = hypothesis_blocks["grid"]
    agg = results["aggregate"]
    folds = results["folds"]
    dsr = results["referee_inputs"]["deflated_sharpe"]
    iteration = results["iteration"]
    history = results["iteration_history"]

    agg_positive = agg["sharpe_annualized"] > 0
    consistent = sum(
        1 for f in folds if (f["sharpe_annualized"] > 0) == agg_positive
    )
    checks = [
        {"name": "min_sharpe", "required": f">= {criteria['min_sharpe']}",
         "observed": agg["sharpe_annualized"],
         "pass": agg["sharpe_annualized"] >= criteria["min_sharpe"]},
        {"name": "max_drawdown", "required": f"<= {criteria['max_drawdown']}",
         "observed": agg["max_drawdown"],
         "pass": agg["max_drawdown"] <= criteria["max_drawdown"]},
        {"name": "min_hit_rate", "required": f">= {criteria['min_hit_rate']}",
         "observed": agg["hit_rate"],
         "pass": agg["hit_rate"] >= criteria["min_hit_rate"]},
        {"name": "stability",
         "required": f">= {criteria['min_sign_consistent_folds']}/4 folds sign-consistent",
         "observed": f"{consistent}/4",
         "pass": consistent >= criteria["min_sign_consistent_folds"]},
        {"name": "deflated_sharpe", "required": f">= {dsr_threshold}",
         "observed": dsr, "pass": dsr >= dsr_threshold},
    ]

    tried = [h["params"] for h in history]
    untried = [g for g in grid if g not in tried]

    if all(c["pass"] for c in checks):
        decision, iterate_params = "PROMOTE", None
        just = "All pre-registered criteria pass and DSR clears the SPEC threshold."
    elif iteration >= 2 or not untried:
        decision, iterate_params = "KILL", None
        failed = ", ".join(f"{c['name']}={c['observed']} (need {c['required']})"
                           for c in checks if not c["pass"])
        just = f"Failed pre-registered criteria: {failed}. Grid exhausted or iteration cap reached."
    else:
        decision, iterate_params = "ITERATE", untried[0]
        failed = ", ".join(f"{c['name']}={c['observed']} (need {c['required']})"
                           for c in checks if not c["pass"])
        just = f"Failed: {failed}. First untried pre-registered grid point: {untried[0]}."

    return {
        "hypothesis_id": results["hypothesis_id"],
        "iteration": iteration,
        "decision": decision,
        "kill_reason": "merits" if decision == "KILL" else None,
        "criteria": checks,
        "iterate_params": iterate_params,
        "justification": just,
        "referee_model": "dry-run-mechanical-referee",
        "timestamp": results["generated_at"],
    }


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
