"""S3 BACKTEST entry point (no LLM involved).

    uv run python -m engine.run_backtest --hypothesis H001

Loads hypotheses/<id>/signal.py, runs the SPEC walk-forward, and writes
hypotheses/<id>/results.json per SPEC §9. On any failure it writes
status="error: <one line>" and exits non-zero (orchestrator: infra-kill).
"""

import argparse
import json
import sys
import traceback
import types
from datetime import datetime, timezone
from pathlib import Path


from engine import config
from engine.backtest import run_walkforward
from engine.errors import EngineError
from engine.io_guard import forbid_io
from engine.loader import data_manifest_sha256, load_panel



def _load_signal_module(signal_path: Path):
    # Read + compile BEFORE the guard (the file itself must be readable),
    # then exec under forbid_io: a signal could otherwise cache future data
    # at IMPORT time and read it back during compute (import-time lookahead).
    try:
        code = compile(signal_path.read_text(encoding="utf-8"),
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


def build_results(hypothesis_id: str, repo_root: Path, data_dir: Path,
                  ledger_path: Path) -> dict:
    from engine.evidence import (input_identity, git, summarize, daily_records,
                                 verify_journal_committed, verify_prior_iterations)
    from engine.trials import TrialJournal, read_json
    from engine.policy import POLICY, validate_iteration
    from engine.metrics import deflated_sharpe_from_moments
    from orchestrator.stages import validate_hypothesis_md, validate_signal_source

    hyp_dir = repo_root / "hypotheses" / hypothesis_id
    source = (hyp_dir / "signal.py").read_text()
    params = validate_signal_source(source)
    blocks = validate_hypothesis_md((hyp_dir / "hypothesis.md").read_text())
    history = read_json(hyp_dir / "iterations.json")
    iteration = history[-1]["iteration"]
    validate_iteration(blocks, {"iteration": iteration, "params": params,
                                "iteration_history": history})
    if data_dir.resolve() != (repo_root / "data" / "train_val").resolve() or data_dir.is_symlink():
        raise EngineError("only local data/train_val is permitted")
    manifest = data_manifest_sha256(data_dir)
    identity = input_identity(repo_root, hypothesis_id, params, iteration, manifest)
    commit = git(repo_root, "rev-parse", "HEAD")
    journal = TrialJournal(repo_root)
    with journal.writer():
        verify_journal_committed(repo_root)
        accounting = journal.accounting()
        if accounting["pending_attempts"]:
            raise EngineError("unresolved trial attempts require human reconciliation")
        verify_prior_iterations(journal, identity)
        start = journal.start(identity, commit)
        try:
            module = _load_signal_module(hyp_dir / "signal.py")
            if module.PARAMS != params:
                raise EngineError("executed parameters differ from pre-registration")
            universe = read_json(repo_root / "data" / "universe.json")
            symbols = [coin["symbol"] for coin in universe["coins"]]
            panel = load_panel(data_dir, expected_symbols=symbols)
            if panel.index.get_level_values("date").max() > config.AGGREGATE_END:
                raise EngineError("train_val contains dates beyond the development cutoff")
            from engine.harness import run_full_harness
            run_full_harness(module.compute_signal, panel)
            run = run_walkforward(panel, module.compute_signal,
                                  config.AGGREGATE_START, config.AGGREGATE_END,
                                  mode="delayed_close_v2", membership=universe.get("membership"))
            if data_manifest_sha256(data_dir) != manifest:
                raise EngineError("dataset changed during evaluation")
            aggregate, folds = summarize(run)
            artifact = {"identity": identity, "harness_passed": True, "aggregate": aggregate,
                        "folds": folds, "daily": daily_records(run)}
            receipt = journal.finish(start, artifact)
        except Exception as exc:
            journal.fail(start, f"{type(exc).__name__}: {exc}".splitlines()[0][:300])
            raise
        accounting = journal.accounting()
        dsr = deflated_sharpe_from_moments(
            aggregate["sharpe_daily"], aggregate["n_days"], aggregate["skew"],
            aggregate["kurtosis_raw"], accounting["n_trials"],
            accounting["trial_sharpes_daily"], variance_floor=1/(aggregate["n_days"]-1))
        return {
            "protocol": POLICY, "hypothesis_id": hypothesis_id, "iteration": iteration,
            "params": params, "iteration_history": history,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "engine": {"git_commit": commit, "data_manifest_sha256": manifest},
            "execution_mode": "delayed_close_v2", "cost_bps_per_side": 25,
            "aggregate": aggregate, "folds": folds,
            "referee_inputs": {**accounting, "deflated_sharpe": dsr},
            "evidence": {"trial_id": start["trial_id"], "journal_head": receipt["sha256"]},
            "status": "ok",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", required=True, help="e.g. H001")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    import re
    if not re.fullmatch(r"H\d{3,}", args.hypothesis):
        parser.error("invalid hypothesis identifier")
    repo_root = Path(args.repo_root).resolve()
    # R2: the ONLY data source is <repo-root>/data/train_val. Deliberately
    # not overridable — no flag may point S3 at another dataset.
    data_dir = repo_root / "data" / "train_val"
    ledger_path = repo_root / "LEDGER.md"
    out_path = repo_root / "hypotheses" / args.hypothesis / "results.json"

    if out_path.exists():
        from engine.trials import read_json
        existing = read_json(out_path)
        if existing.get("protocol") != "quantlab-v2":
            raise EngineError("refusing to overwrite historical results; use a new hypothesis")

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
