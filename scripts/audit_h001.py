"""Reproduce H001's historical formula from committed result moments only.

No market data access; never updates the original experiment.
"""

import hashlib
import json
import math
import subprocess
from pathlib import Path
from statistics import NormalDist, variance

from engine.metrics import deflated_sharpe_from_moments

ROOT = Path(__file__).resolve().parents[1]


def audit():
    inventory = json.loads((ROOT / "trial_evidence/legacy_inventory.json").read_text())
    trials = inventory["trials"]
    results = []
    for t in trials:
        raw = subprocess.check_output(
            ["git", "show", f"{t['result_commit']}:hypotheses/H001/results.json"],
            cwd=ROOT,
        )
        assert hashlib.sha256(raw).hexdigest() == t["result_sha256"]
        results.append(json.loads(raw))
    a = results[-1]["aggregate"]
    scores = [r["aggregate"]["sharpe_daily"] for r in results]
    # Independent arithmetic, intentionally not using the engine formula.
    norm, n, gamma = NormalDist(), len(scores), 0.5772156649
    sr0 = math.sqrt(variance(scores)) * (
        (1 - gamma) * norm.inv_cdf(1 - 1 / n)
        + gamma * norm.inv_cdf(1 - 1 / (n * math.e))
    )
    denominator = math.sqrt(
        1
        - a["skew"] * a["sharpe_daily"]
        + (a["kurtosis_raw"] - 1) / 4 * a["sharpe_daily"] ** 2
    )
    independent = norm.cdf(
        (a["sharpe_daily"] - sr0) * math.sqrt(a["n_days"] - 1) / denominator
    )
    corrected = deflated_sharpe_from_moments(
        a["sharpe_daily"], a["n_days"], a["skew"], a["kurtosis_raw"], n, scores
    )
    assert math.isclose(corrected, independent, rel_tol=1e-14)
    return {
        "analysis": "retrospective trial-count sensitivity; original v1 returns and moments",
        "source_commits": [t["result_commit"] for t in trials],
        "settings": [t["params"] for t in trials],
        "scores_daily": scores,
        "original_n_trials": results[-1]["referee_inputs"]["n_trials"],
        "original_final_dsr": results[-1]["referee_inputs"]["deflated_sharpe"],
        "corrected_n_configurations": n,
        "trial_sharpe_variance": variance(scores),
        "expected_max_daily_sharpe": sr0,
        "corrected_historical_formula_dsr": corrected,
        "independent_calculation_dsr": independent,
        "decision": "KILL: negative Sharpe, excessive drawdown, hit-rate and DSR failures remain",
        "limitations": "Three correlated settings are not three proven independent tests. No corrected execution returns or holdout results computed.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, allow_nan=False))
