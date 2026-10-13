"""Deterministic isolated software fixtures. No market data or live model clients."""

import copy
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from orchestrator import records

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = {"params": {"lookback_days": 90}, "grid": [{"lookback_days": 60}, {"lookback_days": 120}],
          "criteria": {"min_sharpe": 1.0, "max_drawdown": 0.30,
                       "min_hit_rate": 0.50, "min_sign_consistent_folds": 3}}


def init_workspace(root: Path, kind="fixture", *, full=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "WORKSPACE.json").write_text(json.dumps({"kind": kind, "migration_ready": True}))
    (root / "SPEC.md").write_bytes((ROOT / "SPEC.md").read_bytes())
    for package in (["engine", "orchestrator", "scripts", "tests", "templates"] if full else ["engine"]):
        shutil.copytree(ROOT / package, root / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if full:
        for name in ["PROJECT_BRIEF.md", "pyproject.toml", "uv.lock", "conftest.py"]:
            shutil.copyfile(ROOT / name, root / name)
    for name in ["LEDGER.md", "STATE.md", "PROMOTED_QUEUE.md"]:
        (root / name).write_text(f"# {name}\n")
    (root / "hypotheses").mkdir()
    (root / "evaluations").mkdir()
    data = root / "data/train_val"
    data.mkdir(parents=True)
    dates = pd.date_range("2022-07-01", "2025-06-30", tz="UTC", name="date")
    for j in range(15 if full else 1):
        values = 100.0 * np.exp(np.arange(len(dates)) * (0.0001 + j * 0.00001))
        df = pd.DataFrame({"open": values, "high": values, "low": values,
                           "close": values, "volume": np.ones(len(dates))}, index=dates)
        df.to_parquet(data / f"S{j:02d}.parquet")
    for name in ["universe.json", "download_report.json"]:
        (root / "data" / name).write_text('{"test_fixture": true}\n')
    return root


def start_hypothesis(root: Path, kind="fixture"):
    _, hid = records.reserve_hypothesis(root, "test", kind)
    hyp = root / "hypotheses" / hid
    (hyp / "signal.py").write_text("PARAMS = {'lookback_days': 90}\n"
                                   "def compute_signal(panel):\n    return panel['close']\n")
    (hyp / "hypothesis.md").write_text(f"# {hid} — test_signal\n")
    (hyp / "iterations.json").write_text(json.dumps([{"iteration": 0, "params": BLOCKS["params"]}]))
    return hid


def fake_result(root: Path, eid: str, *, passing=False) -> dict:
    rec = records._read_json(records._eval_path(root, eid))
    sharpe = 1.5 if passing else -1.0
    prior = rec["prior_trials"]
    return {"hypothesis_id": rec["hypothesis_id"], "evaluation_id": eid,
            "inputs": copy.deepcopy(rec["inputs"]), "record_kind": rec["record_kind"],
            "iteration": rec["iteration"], "params": rec["params"], "status": "ok",
            "generated_at": "2026-10-13T00:00:00Z",
            "iteration_history": [{"iteration": i, "params": p} for i,p in
                                  enumerate([BLOCKS["params"], *BLOCKS["grid"]][:rec["iteration"]+1])],
            "aggregate": {"sharpe_annualized": sharpe, "sharpe_daily": sharpe/math.sqrt(365),
                          "max_drawdown": .2 if passing else .5,
                          "hit_rate": .55 if passing else .4, "n_days": 973},
            "folds": [{"sharpe_annualized": sharpe} for _ in range(4)],
            "referee_inputs": {"n_trials": len(prior["evaluation_ids"])+1,
                               "prior_evaluation_ids": prior["evaluation_ids"],
                               "prior_result_hashes": prior["result_hashes"],
                               "trial_evaluation_ids": prior["evaluation_ids"]+[eid],
                               "trial_sharpes_daily": prior["sharpes_daily"]+[sharpe/math.sqrt(365)],
                               "deflated_sharpe": .97 if passing else .01}}


def completed(root: Path, hid: str, *, passing=False):
    kind = json.loads((root / "WORKSPACE.json").read_text())["kind"]
    eid = records.reserve_evaluation(root, hid, 0, BLOCKS["params"], "test", kind)
    records.mark_launched(root, eid)
    result = fake_result(root, eid, passing=passing)
    records._write_json(root / "hypotheses" / hid / "results.json", result)
    records.complete_evaluation(root, eid, result)
    return eid, result


def git_init(root):
    (root / ".gitignore").write_text(".quantlab.lock\n__pycache__/\n*.pyc\n.pytest_cache/\ndata/train_val/\n")
    for args in (["init", "-q"], ["config", "user.name", "Fixture"],
                 ["config", "user.email", "fixture@invalid"],
                 ["config", "commit.gpgsign", "false"], ["add", "."],
                 ["commit", "-qm", "fixture initial"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=20)
