"""S3 BACKTEST entry point (no LLM involved).

Invoked by the orchestrator with a reserved evaluation ID and writer lease.

Loads hypotheses/<id>/signal.py, runs the SPEC walk-forward, and writes
hypotheses/<id>/results.json per SPEC §9. On any failure it writes
status="error: <one line>" and exits non-zero (possible launch: unresolved).
"""

import argparse
import json
import statistics
import subprocess
import sys
import traceback
import types
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from engine import config
from engine.backtest import run_walkforward
from engine.errors import EngineError
from engine.io_guard import forbid_io
from engine.identity import input_identity, engine_identity
from engine.workspace import read_workspace, require_engine_lease, safe_id
from engine.loader import load_panel_snapshot
from orchestrator import records
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


def _load_signal_module(signal_path: Path, source: bytes | None = None):
    # Read + compile BEFORE the guard (the file itself must be readable),
    # then exec under forbid_io: a signal could otherwise cache future data
    # at IMPORT time and read it back during compute (import-time lookahead).
    try:
        code = compile(source if source is not None else signal_path.read_bytes(),
                       str(signal_path), "exec")
    except SyntaxError as exc:
        raise EngineError(f"signal.py syntax error: {exc}") from exc
    module = types.ModuleType("hypothesis_signal")
    module.__file__ = str(signal_path)
    with forbid_io():
        exec(code, module.__dict__)  # noqa: S102 — sandboxed by forbid_io
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
                  ledger_path: Path | None = None, *, evaluation_id: str) -> dict:
    hyp_dir = repo_root / "hypotheses" / hypothesis_id
    signal_path = hyp_dir / "signal.py"
    if not signal_path.exists():
        raise EngineError(f"{signal_path} not found")

    workspace = read_workspace(repo_root)
    reservation = records._read_json(records._eval_path(repo_root, evaluation_id))
    if reservation["status"] != "launched" or reservation["hypothesis_id"] != hypothesis_id:
        raise EngineError("engine requires a launched reservation for this hypothesis")
    if reservation["record_kind"] != workspace["kind"]:
        raise EngineError("reservation kind mismatch")
    if engine_identity(repo_root) != engine_identity(Path(__file__).resolve().parent.parent):
        raise EngineError("loaded engine package does not match reported engine identity")
    signal_bytes = signal_path.read_bytes()
    module = _load_signal_module(signal_path, signal_bytes)
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

    panel, manifest = load_panel_snapshot(data_dir)
    inputs = input_identity(repo_root, hypothesis_id, signal_bytes=signal_bytes, manifest=manifest)
    if inputs != reservation["inputs"]:
        raise EngineError("evaluated-input mismatch")
    if params != reservation["params"] or iteration != reservation["iteration"]:
        raise EngineError("evaluated parameters/iteration mismatch")
    run = run_walkforward(
        panel, module.compute_signal, config.AGGREGATE_START, config.AGGREGATE_END
    )

    net = run["r_net"]
    agg_sharpe_daily = sharpe_daily(net)
    g3, g4 = skew_and_raw_kurtosis(net)

    prior = reservation["prior_trials"]
    n_trials = len(prior["evaluation_ids"]) + 1
    trial_sharpes = prior["sharpes_daily"] + [agg_sharpe_daily]
    trial_variance = statistics.variance(trial_sharpes) if len(trial_sharpes) >= 2 else None
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
        "evaluation_id": evaluation_id,
        "record_kind": workspace["kind"],
        "inputs": inputs,
        "iteration": iteration,
        "params": params,
        "iteration_history": iteration_history,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": {
            "git_commit": _git_commit(repo_root),
            "data_manifest_sha256": manifest,
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
            "trial_sharpe_variance": trial_variance,
            "prior_evaluation_ids": prior["evaluation_ids"],
            "prior_result_hashes": prior["result_hashes"],
            "trial_evaluation_ids": prior["evaluation_ids"] + [evaluation_id],
            "deflated_sharpe": dsr,
        },
        "status": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", required=True, help="e.g. H001")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--record-kind", required=True)
    parser.add_argument("--writer-token", required=True)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    # Admission checks precede all writes, including error results.
    read_workspace(repo_root, args.record_kind)
    safe_id(args.hypothesis, r"H\d{3,}")
    safe_id(args.evaluation_id, r"E\d{4,}")
    require_engine_lease(repo_root, args.writer_token)
    # R2: the ONLY data source is <repo-root>/data/train_val. Deliberately
    # not overridable — no flag may point S3 at another dataset.
    data_dir = repo_root / "data" / "train_val"
    ledger_path = repo_root / "LEDGER.md"
    out_path = repo_root / "hypotheses" / args.hypothesis / "results.json"

    try:
        results = build_results(args.hypothesis, repo_root, data_dir, ledger_path,
                                evaluation_id=args.evaluation_id)
        reservation = records._read_json(records._eval_path(repo_root, args.evaluation_id))
        records.validate_result(repo_root, reservation, results)
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 — preserve error evidence; parent halts
        one_line = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
        results = {
            "hypothesis_id": args.hypothesis,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": f"error: {one_line}",
        }
        traceback.print_exc(file=sys.stderr)
        exit_code = 1

    require_engine_lease(repo_root, args.writer_token)
    records._write_json(out_path, results)
    print(f"wrote {out_path} (status={results['status']})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
