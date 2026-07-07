"""LEDGER.md parsing for DSR trial inputs (SPEC §7, §11).

Line format:
#N | name | DECISION(reason) | sharpe=X dsr=Y dd=Z hit=W | tests=pass|fail
"""

import math
import re
from pathlib import Path

from engine.config import ANNUALIZATION_DAYS

_SHARPE_RE = re.compile(r"\bsharpe=([^\s|]+)")


def ledger_entry_count(ledger_path: str | Path) -> int:
    """Number of hypothesis lines (lines starting with '#<digits>')."""
    path = Path(ledger_path)
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^#\d+\s*\|", line.strip()):
            count += 1
    return count


def ledger_trial_sharpes_daily(ledger_path: str | Path) -> list[float]:
    """Per-period (daily) Sharpe of every NUMERIC prior trial: the sharpe=
    field of each ledger line, de-annualized by /sqrt(365). nan entries
    (infra-kills with no backtest) are excluded (SPEC §7)."""
    path = Path(ledger_path)
    if not path.exists():
        return []
    sharpes: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^#\d+\s*\|", line.strip()):
            continue
        match = _SHARPE_RE.search(line)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(value):
            sharpes.append(value / math.sqrt(ANNUALIZATION_DAYS))
    return sharpes
