import copy
import json
import subprocess

import pytest

from engine import trials
from engine.workspace import writer_lock
from orchestrator import records
from scripts.backfill_evaluations import inventory, apply_inventory
from scripts.export_trials import render
from tests.v11_support import ROOT

BASE = "059e0952a367f5e0b4ded60cafd7fd027fa835c5"


def historical_clone(tmp_path):
    subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout", str(ROOT), str(tmp_path / "legacy")],
                   check=True, capture_output=True, timeout=30)
    root = tmp_path / "legacy"
    subprocess.run(["git", "checkout", "--detach", BASE], cwd=root, check=True, capture_output=True, timeout=20)
    (root / "WORKSPACE.json").write_text('{"kind":"research","migration_ready":false}')
    return root


def test_inventory_covers_newer_history_and_preserves_h001_imports(tmp_path):
    root = historical_clone(tmp_path)
    plan = inventory(root, BASE)
    assert plan["issues"] == []
    assert len(plan["evaluations"]) == 3
    assert all(r["record_kind"] == "research" for r in plan["evaluations"])
    assert {h["hypothesis_id"] for h in plan["hypotheses"]} == {f"H{n:03d}" for n in range(1, 8)}
    assert all(h["classification"] == "recorded-failure-before-S3" for h in plan["hypotheses"] if h["hypothesis_id"] != "H001")
    original = {str(p.relative_to(root)): p.read_bytes() for folder in ["hypotheses", "runs"]
                for p in (root / folder).rglob("*") if p.is_file()}
    with writer_lock(root):
        assert apply_inventory(root, plan) == ["E0001", "E0002", "E0003"]
        frozen = [p.read_bytes() for p in sorted((root / "evaluations").glob("*.json"))]
        assert apply_inventory(root, plan) == []
        assert frozen == [p.read_bytes() for p in sorted((root / "evaluations").glob("*.json"))]
        assert records.next_hypothesis_number(root) == 8
        assert trials.prior_trial_count(root) == 3
    recs = records.load_evaluations(root)
    for rec, evidence in zip(recs, plan["evaluations"]):
        assert rec["original_evaluation_id"] is None
        assert rec["binding_status"] == "legacy-unverifiable-at-launch"
        assert rec["source"] == "backfill" and rec["source_commit"] == evidence["source_commit"]
        assert rec["results"] == evidence["results"]
        assert rec["results"]["referee_inputs"]["n_trials"] == 1
    assert recs[-1]["corrected_bookkeeping"]["n_trials"] == 3
    assert recs[-1]["corrected_bookkeeping"]["deflated_sharpe"] == pytest.approx(.0330, abs=.0001)
    assert original == {str(p.relative_to(root)): p.read_bytes() for folder in ["hypotheses", "runs"]
                        for p in (root / folder).rglob("*") if p.is_file()}
    assert "completed-legacy" in render(root)
    assert json.loads((root / "WORKSPACE.json").read_text())["migration_ready"] is False


def test_untrusted_or_uncertain_inventory_cannot_import(tmp_path):
    root = historical_clone(tmp_path)
    plan = inventory(root, BASE)
    bad = copy.deepcopy(plan)
    bad["evaluations"][0]["results"]["aggregate"]["sharpe_daily"] = 100
    with writer_lock(root), pytest.raises(RuntimeError, match="uncertain|does not match"):
        apply_inventory(root, bad)
    assert not (root / "evaluations").exists()


def test_inventory_flags_unfinished_hypothesis_without_guessing(tmp_path):
    root = historical_clone(tmp_path)
    (root / 'hypotheses/H008').mkdir()
    (root / 'hypotheses/H008/hypothesis.md').write_text('# H008 — unknown launch history\n')
    subprocess.run(['git', 'add', 'hypotheses/H008'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'unfinished historical fixture'],
                   cwd=root, check=True, capture_output=True)
    plan = inventory(root, 'HEAD')
    assert plan['readiness'] == 'blocked-uncertainty'
    assert 'historical hypothesis without a terminal outcome: H008' in plan['issues']
    with writer_lock(root), pytest.raises(RuntimeError, match='uncertain'):
        apply_inventory(root, plan)
    assert not (root / 'evaluations').exists()
