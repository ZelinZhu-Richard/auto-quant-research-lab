"""Exercise actual engine -> Git evidence -> referee -> queue with toy data."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from engine import config
from engine.evidence import verify_evidence
from engine.errors import EngineError
from engine.run_backtest import build_results
from engine.trials import TrialJournal
from orchestrator.loop import Loop
from orchestrator.stages import StageFailure, validate_hypothesis_md
from orchestrator.state import append_promoted
from scripts.synthetic_validation import synthetic_panel

SOURCE = Path(__file__).resolve().parents[1]


def commit(root, message="fixture"):
    for args in [("add", "-A"), ("commit", "-qm", message)]:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def evaluated(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir()
    for directory in ["engine", "orchestrator"]:
        (root / directory).mkdir()
        for path in (SOURCE / directory).glob("*.py"):
            shutil.copyfile(path, root / directory / path.name)
    for file in ["SPEC_v2.md", "SPEC.md", "pyproject.toml", "uv.lock", ".gitignore"]:
        shutil.copyfile(SOURCE / file, root / file)
    (root / "tests").mkdir()
    shutil.copyfile(SOURCE / "tests/hypothesis_harness.py", root / "tests/hypothesis_harness.py")
    shutil.copyfile(SOURCE / "conftest.py", root / "conftest.py")
    dates = pd.date_range("2023-01-03", periods=80, tz="UTC")
    monkeypatch.setattr(config, "AGGREGATE_START", dates[0])
    monkeypatch.setattr(config, "AGGREGATE_END", dates[-1])
    monkeypatch.setattr(
        config, "FOLDS", tuple((dates[i], dates[i + 19]) for i in range(0, 80, 20))
    )
    data = root / "data/train_val"
    data.mkdir(parents=True)
    p = synthetic_panel("positive")
    for symbol in p.index.get_level_values("symbol").unique():
        p.xs(symbol, level="symbol").to_parquet(data / f"{symbol}.parquet")
    (root / "data/universe.json").write_text(
        json.dumps({"coins": [{"symbol": f"S{i:02d}"} for i in range(20)]})
    )
    (root / "LEDGER.md").write_text("# Historical ledger\n")
    (root / "trial_evidence").mkdir()
    prefix = (root / "LEDGER.md").read_bytes()
    (root / "trial_evidence/legacy_inventory.json").write_text(
        json.dumps(
            {
                "trials": [],
                "ledger_prefix_bytes": len(prefix),
                "ledger_prefix_sha256": hashlib.sha256(prefix).hexdigest(),
            }
        )
    )
    hyp = root / "hypotheses/H001"
    hyp.mkdir(parents=True)
    card = """# H001 — synthetic
## Parameters (iteration 0)
```json
{"rank": 1}
```
## Iteration grid (pre-registered, ordered, max 2)
```json
[]
```
## Kill criteria (pre-registered)
```json
{"min_sharpe": 1.0, "max_drawdown": 0.3, "min_hit_rate": 0.5, "min_sign_consistent_folds": 3}
```
"""
    (hyp / "hypothesis.md").write_text(card)
    (hyp / "signal.py").write_text("""import pandas as pd
PARAMS = {"rank": 1}
def compute_signal(panel):
    return pd.Series([float(s[1:]) for s in panel.index.get_level_values("symbol")],index=panel.index)
""")
    (hyp / "iterations.json").write_text('[{"iteration": 0, "params": {"rank": 1}}]')
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True
    )
    commit(root)
    result = build_results("H001", root, data, root / "LEDGER.md")
    (hyp / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    commit(root, "S3 score")
    return root, card, result


def loop(root):
    return Loop(root, "dry-run", "integration", 1, 1.0, 1.0, 1)


def test_real_engine_evidence_referee_and_promotion(evaluated):
    root, card, r = evaluated
    verify_evidence(root, "H001", r)
    lab = loop(root)
    d = lab._s4_referee("H001", card, validate_hypothesis_md(card), r, True)
    assert d["decision"] == "PROMOTE"
    lab._s5_memo("H001", card, r, d, True)
    lab._close_cycle(1, "H001", "synthetic", d, r, True, 0)
    assert "H001 | synthetic" in (root / "PROMOTED_QUEUE.md").read_text()
    assert TrialJournal(root).accounting()["n_trials"] == 1


@pytest.mark.parametrize(
    "target", ["result", "score", "event", "source", "card", "data", "deleted_tail"]
)
def test_tampered_or_deleted_committed_evidence_rejected(evaluated, target):
    root, card, r = evaluated
    if target == "result":
        r["aggregate"]["sharpe_annualized"] = 99
    elif target == "score":
        path = next((root / "trial_evidence/scores").glob("*.json"))
        path.write_text("{}")
    elif target == "event":
        path = root / "trial_evidence/events/00000001.json"
        path.write_text("{}")
    elif target == "source":
        with (root / "engine/metrics.py").open("a") as stream:
            stream.write("\n# changed grader\n")
    elif target == "card":
        (root / "hypotheses/H001/hypothesis.md").write_text(card.replace("1.0", "0.0"))
        commit(root, "changed registration")
    elif target == "data":
        path = root / "data/train_val/S00.parquet"
        frame = pd.read_parquet(path)
        frame["volume"] *= 2
        frame.to_parquet(path)
    else:
        (root / "trial_evidence/events/00000002.json").unlink()
    with pytest.raises(EngineError):
        verify_evidence(root, "H001", r)


def test_forged_committed_metrics_and_queue_bypass_rejected(evaluated):
    root, card, r = evaluated
    r["referee_inputs"]["deflated_sharpe"] = 0.0
    (root / "hypotheses/H001/results.json").write_text(json.dumps(r))
    commit(root, "forged model numbers")
    # Evidence hash reconstruction accepts neither a manual queue call nor a forged decision.
    with pytest.raises((EngineError, StageFailure)):
        lab = loop(root)
        lab._s4_referee("H001", card, validate_hypothesis_md(card), r, True)
    with pytest.raises((EngineError, StageFailure)):
        append_promoted(root, "H001", "forged")


def test_pending_attempt_blocks_merit_decision(evaluated):
    root, card, r = evaluated
    j = TrialJournal(root)
    with j.writer():
        j.start({"another": "unresolved"}, "fixture")
    commit(root, "interrupted attempt")
    with pytest.raises(EngineError, match="snapshot"):
        verify_evidence(root, "H001", r)


def test_exact_replay_counts_attempt_not_new_statistical_trial(evaluated):
    root, card, first = evaluated
    second = build_results('H001', root, root/'data/train_val', root/'LEDGER.md')
    assert second['evidence']['trial_id'] == first['evidence']['trial_id']
    assert second['referee_inputs']['attempted'] == 2
    assert second['referee_inputs']['n_trials'] == 1
    assert second['aggregate'] == first['aggregate']


def test_s4_rejects_forged_registration_and_terminal_decision(evaluated):
    root, card, result = evaluated
    lab = loop(root)
    blocks = validate_hypothesis_md(card)
    blocks['criteria']['min_sharpe'] = 100
    with pytest.raises(StageFailure, match='registration'):
        lab._s4_referee('H001', card, blocks, result, True)
    decision = lab._s4_referee('H001',card,validate_hypothesis_md(card),result,True)
    decision['criteria'][0]['pass'] = False
    with pytest.raises(StageFailure, match='terminal decision'):
        lab._close_cycle(1,'H001','synthetic',decision,result,True,0)
    assert not (root/'PROMOTED_QUEUE.md').exists()


def test_failed_evaluation_is_recorded_before_rejection(evaluated):
    root, card, result = evaluated
    p = root/'data/train_val/S19.parquet'
    frame = pd.read_parquet(p)
    frame.drop(frame.index[10]).to_parquet(p)
    with pytest.raises(EngineError, match='price'):
        build_results('H001',root,root/'data/train_val',root/'LEDGER.md')
    accounting = TrialJournal(root).accounting()
    assert accounting['attempted'] == 2
    assert accounting['n_trials'] == 2
    assert accounting['unique_scored'] == 1
    assert accounting['pending_attempts'] == 0
    assert TrialJournal(root).events()[-1]['kind'] == 'failed'


def test_harness_attempt_is_durable_before_child_execution(evaluated, monkeypatch):
    from engine.run_harness import run_registered_harness
    from types import SimpleNamespace
    root, _, first = evaluated
    def child(*args, **kwargs):
        journal = TrialJournal(root)
        assert journal.events()[-1]['phase'] == 'harness'
        assert journal.accounting()['pending_attempts'] == 1
        return SimpleNamespace(returncode=1)
    # Patch only the subprocess called by the wrapper, preserving Git tools.
    import engine.run_harness as runner
    original = runner.subprocess.run
    def dispatch(command, *args, **kwargs):
        return child(command, *args, **kwargs) if '-m' in command else original(command, *args, **kwargs)
    monkeypatch.setattr(runner.subprocess, 'run', dispatch)
    assert run_registered_harness(root, 'H001') == 1
    a = TrialJournal(root).accounting()
    assert a['attempted'] == 2 and a['n_trials'] == 1
    assert a['pending_attempts'] == 0
    assert TrialJournal(root).events()[-1]['kind'] == 'failed'


def test_actual_registered_harness_pass_has_no_additional_score(evaluated):
    from engine.run_harness import run_registered_harness
    root, _, result = evaluated
    assert run_registered_harness(root, 'H001') == 0
    a = TrialJournal(root).accounting()
    assert a['attempted'] == 2 and a['n_trials'] == 1
    assert a['successfully_scored_attempts'] == 1 and a['pending_attempts'] == 0
    assert TrialJournal(root).events()[-1]['kind'] == 'checked'
