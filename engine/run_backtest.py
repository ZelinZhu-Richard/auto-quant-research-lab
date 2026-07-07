"""S3 BACKTEST entry point (no LLM involved).

    uv run python -m engine.run_backtest --hypothesis H001

Loads hypotheses/<id>/signal.py, runs the SPEC walk-forward, and writes
hypotheses/<id>/results.json per SPEC §9. On any failure it writes
status="error: <one line>" and exits non-zero (orchestrator: infra-kill).
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from engine import config
from engine.backtest import run_walkforward
from engine.errors import EngineError
from engine.io_guard import forbid_io
from engine.ledger import ledger_entry_count, ledger_trial_sharpes_daily
from engine.loader import data_manifest_sha256, load_panel
from engine.metrics import (
    deflated_sharpe,
    hit_rate,
    max_drawdown,
    sharpe_annualized,
    sharpe_daily,
    skew_and_raw_kurtosis,
)


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def _load_signal_module(signal_path: Path):
    spec = importlib.util.spec_from_file_location("hypothesis_signal", signal_path)
    if spec is None or spec.loader is None:
        raise EngineError(f"cannot import {signal_path}")
    module = importlib.util.module_from_spec(spec)
    # forbid_io: a signal could otherwise cache future data at IMPORT time
    # and read it back during compute (import-time lookahead)
    with forbid_io():
        spec.loader.exec_module(module)
    if not hasattr(module, "compute_signal"):
        raise EngineError(f"{signal_path} does not define compute_signal")
    return module


def _window_metrics(window: pd.DataFrame) -> dict:
    net = window["r_net"]
    return {
        "n_days": int(len(window)),
        "sharpe_annualized": sharpe_annualized(net),
        "max_drawdown": max_drawdown(net),
        "hit_rate": hit_rate(net, window["has_positions"]),
    }


def build_results(hypothesis_id: str, repo_root: Path, data_dir: Path,
                  ledger_path: Path) -> dict:
    hyp_dir = repo_root / "hypotheses" / hypothesis_id
    signal_path = hyp_dir / "signal.py"
    if not signal_path.exists():
        raise EngineError(f"{signal_path} not found")

    module = _load_signal_module(signal_path)
    params = getattr(module, "PARAMS", {})

    iterations_path = hyp_dir / "iterations.json"
    if iterations_path.exists():
        iteration_history = json.loads(iterations_path.read_text(encoding="utf-8"))
        if not isinstance(iteration_history, list) or not iteration_history:
            raise EngineError("iterations.json must be a non-empty list")
        iteration = int(iteration_history[-1]["iteration"])
    else:
        iteration = 0
        iteration_history = [{"iteration": 0, "params": params}]

    panel = load_panel(data_dir)
    run = run_walkforward(
        panel, module.compute_signal, config.AGGREGATE_START, config.AGGREGATE_END
    )

    net = run["r_net"]
    agg_sharpe_daily = sharpe_daily(net)
    g3, g4 = skew_and_raw_kurtosis(net)

    n_trials = ledger_entry_count(ledger_path) + 1  # +1 IS the current trial
    trial_sharpes = ledger_trial_sharpes_daily(ledger_path) + [agg_sharpe_daily]
    dsr = deflated_sharpe(net, n_trials, trial_sharpes)

    folds = []
    for k, (fold_start, fold_end) in enumerate(config.FOLDS, start=1):
        window = run[(run.index >= fold_start) & (run.index <= fold_end)]
        folds.append({
            "fold": k,
            "start": str(fold_start.date()),
            "end": str(fold_end.date()),
            **_window_metrics(window),
        })

    return {
        "hypothesis_id": hypothesis_id,
        "iteration": iteration,
        "params": params,
        "iteration_history": iteration_history,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": {
            "git_commit": _git_commit(repo_root),
            "data_manifest_sha256": data_manifest_sha256(data_dir),
        },
        "cost_bps_per_side": 25,
        "aggregate": {
            "start": str(config.AGGREGATE_START.date()),
            "end": str(config.AGGREGATE_END.date()),
            "n_days": int(len(run)),
            "n_position_days": int(run["has_positions"].sum()),
            "sharpe_annualized": sharpe_annualized(net),
            "sharpe_daily": agg_sharpe_daily,
            "max_drawdown": max_drawdown(net),
            "hit_rate": hit_rate(net, run["has_positions"]),
            "turnover_annualized": float(run["turnover"].mean()) * config.ANNUALIZATION_DAYS,
            "mean_daily_return": float(net.mean()),
            "std_daily_return": float(net.std(ddof=1)),
            "skew": g3,
            "kurtosis_raw": g4,
        },
        "folds": folds,
        "referee_inputs": {
            "n_trials": n_trials,
            "trial_sharpes_daily": trial_sharpes,
            "deflated_sharpe": dsr,
        },
        "status": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", required=True, help="e.g. H001")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    # R2: the ONLY data source is <repo-root>/data/train_val. Deliberately
    # not overridable — no flag may point S3 at another dataset.
    data_dir = repo_root / "data" / "train_val"
    ledger_path = repo_root / "LEDGER.md"
    out_path = repo_root / "hypotheses" / args.hypothesis / "results.json"

    try:
        results = build_results(args.hypothesis, repo_root, data_dir, ledger_path)
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 — any failure => infra-kill (R4)
        one_line = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
        results = {
            "hypothesis_id": args.hypothesis,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": f"error: {one_line}",
        }
        traceback.print_exc(file=sys.stderr)
        exit_code = 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path} (status={results['status']})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
