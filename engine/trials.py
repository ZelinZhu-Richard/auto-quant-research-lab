"""Kind-scoped completed evaluations; immutable historical sets are reserved once."""

import json
import math
import re
from pathlib import Path

from engine.workspace import read_workspace

COMPLETED = ("completed", "completed-manual", "completed-legacy")


def completed_records(repo_root: str | Path) -> list[dict]:
    root = Path(repo_root)
    kind = read_workspace(root)["kind"]
    records = []
    for path in sorted((root / "evaluations").glob("E*.json")):
        if not re.fullmatch(r"E\d{4,}\.json", path.name):
            continue
        record = json.loads(path.read_text())
        if record.get("record_kind") == kind and record.get("status") in COMPLETED:
            value = record["metrics"]["sharpe_daily"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid completed metric in {path.name}")
            records.append(record)
    return records


def prior_snapshot(root: Path) -> dict:
    records = completed_records(root)
    return {"evaluation_ids": [r["evaluation_id"] for r in records],
            "sharpes_daily": [r["metrics"]["sharpe_daily"] for r in records]}


def prior_trial_count(repo_root: str | Path) -> int:
    return len(completed_records(repo_root))


def prior_trial_sharpes_daily(repo_root: str | Path) -> list[float]:
    return [r["metrics"]["sharpe_daily"] for r in completed_records(repo_root)]
