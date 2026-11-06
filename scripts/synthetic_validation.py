"""Deterministic toy scientific checks. No empirical error-control claim."""

import json
import math

import numpy as np
import pandas as pd

from engine.backtest import run_walkforward
from engine.harness import assert_no_lookahead, LookaheadError
from engine.metrics import (
    deflated_sharpe_from_moments,
    hit_rate,
    max_drawdown,
    sharpe_daily,
    skew_and_raw_kurtosis,
)
from engine.policy import decide


def synthetic_panel(kind="noise", days=82):
    rng = np.random.default_rng(8127)
    symbols = [f"S{i:02d}" for i in range(20)]
    dates = pd.date_range("2023-01-01", periods=days, tz="UTC")
    changes = rng.normal(0, 0.008, (days, 20))
    if kind == "positive":
        changes += np.linspace(-0.006, 0.006, 20)
    closes = 100 * np.cumprod(1 + changes, axis=0)
    return pd.DataFrame(
        [
            dict(
                date=d,
                symbol=s,
                open=closes[t, i],
                high=closes[t, i],
                low=closes[t, i],
                close=closes[t, i],
                volume=1000.0,
            )
            for t, d in enumerate(dates)
            for i, s in enumerate(symbols)
        ]
    ).set_index(["date", "symbol"])


def rank_signal(order=None):
    mapping = {f"S{i:02d}": float(i if order is None else order[i]) for i in range(20)}
    return lambda p: pd.Series(
        [mapping[s] for s in p.index.get_level_values("symbol")], index=p.index
    )


def decision_for(run, scores, n_trials):
    r = run.r_net
    sr = sharpe_daily(r)
    g3, g4 = skew_and_raw_kurtosis(r)
    dsr = deflated_sharpe_from_moments(
        sr, len(r), g3, g4, n_trials, scores, variance_floor=1 / (len(r) - 1)
    )
    result = dict(
        protocol="quantlab-v2",
        hypothesis_id="H999",
        iteration=0,
        params={"rank": 1},
        iteration_history=[dict(iteration=0, params={"rank": 1})],
        generated_at="2026-11-05T00:00:00+00:00",
        status="ok",
        aggregate=dict(
            n_days=len(r),
            sharpe_daily=sr,
            sharpe_annualized=sr * math.sqrt(365),
            max_drawdown=max_drawdown(r),
            hit_rate=hit_rate(r, run.has_positions),
            skew=g3,
            kurtosis_raw=g4,
        ),
        folds=[
            dict(
                fold=i + 1,
                n_days=len(w),
                sharpe_annualized=sharpe_daily(w) * math.sqrt(365),
            )
            for i, w in enumerate(np.array_split(r, 4))
        ],
        referee_inputs=dict(
            n_trials=n_trials,
            trial_sharpes_daily=scores,
            deflated_sharpe=dsr,
            pending_attempts=0,
            effective_independent_trials=None,
            method="all-configurations-dsr-variance-floor-v2",
        ),
    )
    blocks = dict(
        params={"rank": 1},
        grid=[],
        criteria=dict(
            min_sharpe=1.0,
            max_drawdown=0.3,
            min_hit_rate=0.5,
            min_sign_consistent_folds=3,
        ),
    )
    return decide(blocks, result)


def evaluate():
    rows = []
    for kind in ["positive", "noise"]:
        p = synthetic_panel(kind)
        signal = rank_signal()
        assert_no_lookahead(signal, p)
        run = run_walkforward(p, signal, p.index.levels[0][2], p.index.levels[0][-1])
        d = decision_for(run, [sharpe_daily(run.r_net)], 1)
        rows.append(
            dict(
                case=kind,
                truth="planted" if kind == "positive" else "null",
                decision=d["decision"],
                reason=d["justification"],
            )
        )
    # Adaptive selection of the best of 16 fixed rankings on the same noise.
    p = synthetic_panel("noise")
    rng = np.random.default_rng(477)
    runs = [
        run_walkforward(
            p,
            rank_signal(rng.permutation(20)),
            p.index.levels[0][2],
            p.index.levels[0][-1],
        )
        for _ in range(16)
    ]
    scores = [sharpe_daily(r.r_net) for r in runs]
    winner = runs[int(np.argmax(scores))]
    d = decision_for(winner, scores, len(scores))
    rows.append(
        dict(
            case="selected_noise_winner",
            truth="null",
            decision=d["decision"],
            reason=d["justification"],
            n_trials=len(scores),
        )
    )

    def tomorrow(p):
        return p.close.unstack("symbol").pct_change(fill_method=None).shift(-1).stack()

    try:
        assert_no_lookahead(tomorrow, p)
        raise AssertionError("planted leak was not caught")
    except LookaheadError:
        rows.append(
            dict(
                case="future_return_oracle",
                truth="misleading",
                decision="REJECT",
                reason="lookahead harness rejects future-dependent values",
            )
        )
    return dict(
        label="Toy fixtures only; no population false-positive guarantee",
        true_discoveries=sum(
            r["truth"] == "planted" and r["decision"] == "PROMOTE" for r in rows
        ),
        false_promotions=sum(
            r["truth"] != "planted" and r["decision"] == "PROMOTE" for r in rows
        ),
        rejections=sum(r["decision"] != "PROMOTE" for r in rows),
        cases=rows,
    )


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
