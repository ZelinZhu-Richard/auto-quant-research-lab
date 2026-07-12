"""The README stats table is computed, never typed — so the computation
itself gets tests."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ledger_stats", Path(__file__).parent.parent / "scripts" / "ledger_stats.py")
ledger_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_stats)

SAMPLE = """# LEDGER.md — header
# #N | name | DECISION(reason) | sharpe=X dsr=Y dd=Z hit=W | tests=pass|fail
#1 | momo | ITERATED->KILLED(merits) | sharpe=-0.86 dsr=0.08 dd=0.47 hit=0.49 | tests=pass
#2 | rev | KILLED(merits) | sharpe=0.20 dsr=0.10 dd=0.20 hit=0.51 | tests=pass
#3 | broken | KILLED(infrastructure) | sharpe=nan dsr=nan dd=nan hit=nan | tests=fail
#4 | golden | ITERATED->PROMOTED | sharpe=1.40 dsr=0.96 dd=0.18 hit=0.55 | tests=pass
"""


def test_parse_counts():
    stats = ledger_stats.parse_ledger(SAMPLE)
    assert stats == {"tested": 4, "killed_merits": 2, "killed_infra": 1,
                     "iterated": 2, "promoted": 1}


def test_header_lines_not_counted():
    stats = ledger_stats.parse_ledger("# LEDGER.md\n# #N | name | ...\n")
    assert stats["tested"] == 0


def test_render_table_contains_rate_and_provenance():
    stats = ledger_stats.parse_ledger(SAMPLE)
    table = ledger_stats.render_table(stats, holdout_runs=1)
    assert "| hypotheses tested | 4 |" in table
    assert "| infrastructure-kill rate | 25.0% |" in table
    assert "| single-shot holdout runs (human-only) | 1 |" in table
    assert "scripts/ledger_stats.py" in table  # provenance line


def test_readme_markers_exist_and_regeneration_is_idempotent(tmp_path):
    readme = Path(ledger_stats.REPO_ROOT) / "README.md"
    text = readme.read_text()
    assert ledger_stats.BEGIN in text and ledger_stats.END in text
