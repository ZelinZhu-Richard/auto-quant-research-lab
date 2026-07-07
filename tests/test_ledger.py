import math

from engine.ledger import ledger_entry_count, ledger_trial_sharpes_daily

SAMPLE = """# LEDGER.md — append-only
#1 | vol_breakout | KILLED(merits) | sharpe=0.4200 dsr=0.3100 dd=0.1800 hit=0.5100 | tests=pass
#2 | dead_cycle | KILLED(infrastructure) | sharpe=nan dsr=nan dd=nan hit=nan | tests=fail
#3 | momo_90d | PROMOTED | sharpe=1.2000 dsr=0.9600 dd=0.2100 hit=0.5400 | tests=pass
"""


def test_entry_count(tmp_path):
    path = tmp_path / "LEDGER.md"
    path.write_text(SAMPLE)
    assert ledger_entry_count(path) == 3


def test_missing_ledger_counts_zero(tmp_path):
    assert ledger_entry_count(tmp_path / "absent.md") == 0
    assert ledger_trial_sharpes_daily(tmp_path / "absent.md") == []


def test_trial_sharpes_exclude_nan_and_deannualize(tmp_path):
    path = tmp_path / "LEDGER.md"
    path.write_text(SAMPLE)
    sharpes = ledger_trial_sharpes_daily(path)
    assert len(sharpes) == 2  # the nan infra-kill is excluded
    assert abs(sharpes[0] - 0.42 / math.sqrt(365)) < 1e-12
    assert abs(sharpes[1] - 1.20 / math.sqrt(365)) < 1e-12
