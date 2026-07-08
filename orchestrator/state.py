"""Four-file protocol writers (PROJECT_BRIEF §5, SPEC §11).

STATE.md — overwritten at every stage transition, max 50 lines.
LEDGER.md — append-only, one line per hypothesis.
BACKLOG.md — never read, never written by the loop.
SPEC.md — read every cycle, written by no one.
"""

import math
import re
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_state(
    repo_root: Path,
    phase: str,
    hypothesis_id: str,
    stage: str,
    iteration: int | None,
    last_error: str,
    consecutive_infra_kills: int,
) -> None:
    lines = [
        "# STATE.md — overwritten at every stage transition; max 50 lines.",
        "",
        f"phase: {phase}",
        f"current_hypothesis: {hypothesis_id}",
        f"stage: {stage}",
        f"iteration: {iteration if iteration is not None else 'none'}",
        f"last_error: {last_error or 'none'}",
        f"consecutive_infra_kills: {consecutive_infra_kills}",
        f"updated: {_now()}",
    ]
    (repo_root / "STATE.md").write_text("\n".join(lines[:50]) + "\n", encoding="utf-8")


def _fmt(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "nan"
    return f"{value:.4f}"


def append_ledger(
    repo_root: Path,
    number: int,
    name: str,
    decision: str,
    sharpe: float | None,
    dsr: float | None,
    dd: float | None,
    hit: float | None,
    tests_pass: bool,
) -> str:
    line = (
        f"#{number} | {name} | {decision} | "
        f"sharpe={_fmt(sharpe)} dsr={_fmt(dsr)} dd={_fmt(dd)} hit={_fmt(hit)} | "
        f"tests={'pass' if tests_pass else 'fail'}"
    )
    with (repo_root / "LEDGER.md").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


def ledger_tail(repo_root: Path, n: int = 30) -> str:
    """Last n hypothesis lines — the S1 context budget (PROJECT_BRIEF §5)."""
    path = repo_root / "LEDGER.md"
    if not path.exists():
        return "(ledger empty)"
    entries = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"^#\d+\s*\|", line.strip())
    ]
    return "\n".join(entries[-n:]) if entries else "(ledger empty)"


def next_hypothesis_number(repo_root: Path) -> int:
    path = repo_root / "LEDGER.md"
    if not path.exists():
        return 1
    numbers = [
        int(m.group(1))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^#(\d+)\s*\|", line.strip()))
    ]
    return (max(numbers) + 1) if numbers else 1


def append_promoted(repo_root: Path, hypothesis_id: str, name: str) -> None:
    with (repo_root / "PROMOTED_QUEUE.md").open("a", encoding="utf-8") as fh:
        fh.write(f"{hypothesis_id} | {name} | {_now()}\n")
