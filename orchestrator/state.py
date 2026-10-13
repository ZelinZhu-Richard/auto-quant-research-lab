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

from engine.workspace import assert_scoped_history, require_writer
from orchestrator.records import atomic_text


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
    require_writer(repo_root)
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
    atomic_text(repo_root / "STATE.md", "\n".join(lines[:50]) + "\n")


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
    require_writer(repo_root)
    line = (
        f"#{number} | {name} | {decision} | "
        f"sharpe={_fmt(sharpe)} dsr={_fmt(dsr)} dd={_fmt(dd)} hit={_fmt(hit)} | "
        f"tests={'pass' if tests_pass else 'fail'}"
    )
    path = repo_root / "LEDGER.md"
    old = path.read_text() if path.exists() else ""
    existing = [row for row in old.splitlines() if re.match(rf"^#{number}\s*\|", row)]
    if existing:
        if existing != [line]:
            raise RuntimeError(f"conflicting ledger evidence for hypothesis {number}")
        return line
    # Logical append, atomic replacement: interruption cannot persist a partial line.
    atomic_text(path, old + ("" if not old or old.endswith("\n") else "\n") + line + "\n")
    return line


def ledger_tail(repo_root: Path, n: int = 30) -> str:
    """Last n hypothesis lines — the S1 context budget (PROJECT_BRIEF §5)."""
    assert_scoped_history(repo_root)
    path = repo_root / "LEDGER.md"
    if not path.exists():
        return "(ledger empty)"
    entries = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"^#\d+\s*\|", line.strip())
    ]
    return "\n".join(entries[-n:]) if entries else "(ledger empty)"


def next_hypothesis_number(repo_root: Path) -> int:
    # Retain the public helper without maintaining a ledger-only allocator.
    from orchestrator.records import next_hypothesis_number as allocated_next
    return allocated_next(repo_root)


def append_promoted(repo_root: Path, hypothesis_id: str, name: str) -> None:
    require_writer(repo_root)
    path = repo_root / "PROMOTED_QUEUE.md"
    old = path.read_text() if path.exists() else ""
    if any(row.split(" | ")[0] == hypothesis_id for row in old.splitlines()):
        return
    atomic_text(path, old + ("" if not old or old.endswith("\n") else "\n") +
                f"{hypothesis_id} | {name} | {_now()}\n")
