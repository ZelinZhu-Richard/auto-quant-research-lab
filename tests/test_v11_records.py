import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine import trials
from engine.identity import input_identity
from engine.workspace import WorkspaceError, assert_ready, writer_lock
from orchestrator import records
from orchestrator.loop import Loop
from tests.v11_support import BLOCKS, completed, fake_result, git_init, init_workspace, start_hypothesis


@pytest.fixture
def workspace(tmp_path):
    return init_workspace(tmp_path)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_kind_mismatch_before_any_write(workspace):
    before = snapshot(workspace)
    with pytest.raises(WorkspaceError, match="kind"):
        Loop(workspace, "live", "test", 1, 1, 1, 1)
    assert snapshot(workspace) == before


def test_second_writer_and_ownerless_mutation_refused(workspace):
    with writer_lock(workspace):
        before = snapshot(workspace)
        with pytest.raises(WorkspaceError, match="locked"):
            with writer_lock(workspace):
                pass
        assert snapshot(workspace) == before
        code = "from pathlib import Path; from orchestrator.records import reserve_hypothesis; reserve_hypothesis(Path(__import__('sys').argv[1]), 'other', 'fixture')"
        child = subprocess.run([sys.executable, "-c", code, str(workspace)],
                               capture_output=True, text=True, timeout=20)
        assert child.returncode != 0 and "ownership required" in child.stderr
        assert snapshot(workspace) == before
    assert not (workspace / ".quantlab.lock").exists()


def test_stale_lock_is_never_cleared(workspace):
    (workspace / ".quantlab.lock").write_text('{"pid":99999999,"started":"old"}')
    before = snapshot(workspace)
    with pytest.raises(WorkspaceError, match="human"):
        with writer_lock(workspace):
            pass
    assert snapshot(workspace) == before


def test_ids_never_reused_and_completed_inputs_frozen(workspace):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        eid, result = completed(workspace, hid)
        original = records._eval_path(workspace, eid).read_bytes()
        assert trials.prior_trial_count(workspace) == 1
        assert result["referee_inputs"]["trial_evaluation_ids"] == [eid]
        hid2 = start_hypothesis(workspace)
        eid2, result2 = completed(workspace, hid2)
        assert result2["referee_inputs"]["trial_evaluation_ids"] == [eid, eid2]
        assert result2["referee_inputs"]["n_trials"] == 2
        records.complete_evaluation(workspace, eid, result)
        records.recover(workspace)
        assert records._eval_path(workspace, eid).read_bytes() == original
        assert trials.prior_trial_count(workspace) == 2
        assert records.next_hypothesis_number(workspace) == 3
        with pytest.raises(RuntimeError, match="immutable"):
            records.complete_evaluation(workspace, eid, {**result, "generated_at": "changed"})


@pytest.mark.parametrize("damage", ["missing_id", "other_id", "hash", "tuple", "nan", "prior_ids", "truncated", "missing"])
def test_ambiguous_launched_results_unresolved_and_next_run_has_no_writes(workspace, damage):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        eid = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        records.mark_launched(workspace, eid)
        result = fake_result(workspace, eid)
        if damage == "missing_id": result.pop("evaluation_id")
        if damage == "other_id": result["evaluation_id"] = "E9999"
        if damage == "hash": result["inputs"]["signal_sha256"] = "0" * 64
        if damage == "tuple": result["params"] = {"lookback_days": 1}
        if damage == "nan": result["aggregate"]["sharpe_daily"] = float("nan")
        if damage == "prior_ids": result["referee_inputs"]["trial_evaluation_ids"] += [eid]
        path = workspace / "hypotheses" / hid / "results.json"
        if damage != "missing": path.write_text("{" if damage == "truncated" else json.dumps(result))
        records.recover(workspace)
        rec = records._read_json(records._eval_path(workspace, eid))
        assert rec["status"] == "unresolved"
        if damage == "hash": assert "signal_sha256" in rec["reason"]
        before = snapshot(workspace)
        assert records.recover(workspace) == []
        assert snapshot(workspace) == before
    before = snapshot(workspace)
    with pytest.raises(WorkspaceError, match="unresolved"):
        Loop(workspace, "dry-run", "new", 1, 1, 1, 1).run()
    assert snapshot(workspace) == before


def test_never_launched_cancelled_even_with_stale_result(workspace):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        eid = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        records._write_json(workspace / "hypotheses" / hid / "results.json", fake_result(workspace, eid))
        records.recover(workspace)
        assert records.load_evaluations(workspace)[0]["status"] == "cancelled"
        with pytest.raises(RuntimeError): records.mark_launched(workspace, eid)
        assert trials.prior_trial_count(workspace) == 0
        assert records.next_hypothesis_number(workspace) == 2


def test_recovery_completes_exact_result_and_keeps_copy(workspace):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        eid = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        records.mark_launched(workspace, eid)
        r = fake_result(workspace, eid)
        path = workspace / "hypotheses" / hid / "results.json"
        records._write_json(path, r)
        records.recover(workspace)
        path.write_text("overwritten by another iteration")
        rec = records.load_evaluations(workspace)[0]
        assert rec["status"] == "completed" and rec["results"] == r
        assert rec["result_sha256"] == records.result_hash(r)


def test_bookkeeping_head_is_lineage_only_but_changed_signal_blocks_launch(workspace):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        git_init(workspace)
        eid = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        subprocess.run(["git", "add", "evaluations"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "bookkeeping"], cwd=workspace, check=True, capture_output=True)
        records.mark_launched(workspace, eid)
        records.unresolved(workspace, eid, "test setup: no engine launched after boundary")
        # Test-only human simulation; never auto-used in application recovery.
        records.reconcile(workspace, eid, "cancelled", "test setup, engine never called", human_authorized=True)
        eid2 = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        (workspace / "hypotheses" / hid / "signal.py").write_text("changed")
        with pytest.raises(RuntimeError, match="input mismatch"):
            records.mark_launched(workspace, eid2)
        assert records.load_evaluations(workspace)[1]["status"] == "cancelled"


def test_manual_completion_needs_valid_bound_historical_evidence(workspace):
    with writer_lock(workspace):
        hid = start_hypothesis(workspace)
        eid = records.reserve_evaluation(workspace, hid, 0, BLOCKS["params"], "r", "fixture")
        records.mark_launched(workspace, eid)
        result = fake_result(workspace, eid)
        records.unresolved(workspace, eid, "test interruption")
        with pytest.raises(RuntimeError, match="human"):
            records.reconcile(workspace, eid, "cancelled", "no authority")
        bad = copy.deepcopy(result); bad["referee_inputs"]["n_trials"] = 2
        with pytest.raises(ValueError, match="n_trials"):
            records.reconcile(workspace, eid, "completed-manual", "test-only simulation", bad, human_authorized=True)
        rec = records.reconcile(workspace, eid, "completed-manual", "test-only simulation", result, human_authorized=True)
        assert rec["status"] == "completed-manual"
        assert trials.prior_trial_count(workspace) == 1


@pytest.mark.parametrize("kind", ["fixture", "synthetic", "replay", "research"])
def test_counts_are_scoped_to_workspace_kind(tmp_path, kind):
    root = init_workspace(tmp_path, kind)
    with writer_lock(root):
        hid = start_hypothesis(root, kind)
        completed(root, hid)
        assert trials.prior_trial_count(root) == 1
        # Foreign evidence is excluded from statistics and blocks consumers/preflight.
        foreign = records.load_evaluations(root)[0]
        foreign.update(evaluation_id="E9999", record_kind="replay" if kind != "replay" else "fixture")
        records._write_json(records._eval_path(root, "E9999"), foreign)
        assert trials.prior_trial_count(root) == 1
        with pytest.raises(WorkspaceError, match="foreign"):
            records.reserve_hypothesis(root, "r", kind)


def test_research_migration_gate_does_not_affect_fixtures(workspace):
    (workspace / "WORKSPACE.json").write_text('{"kind":"research","migration_ready":false}')
    before = snapshot(workspace)
    with pytest.raises(WorkspaceError, match="migration"):
        Loop(workspace, "live", "r", 1, 1, 1, 1).run()
    assert snapshot(workspace) == before


def test_completion_rejects_missing_fold_metrics_and_history(tmp_path):
    from tests.v11_support import fake_result
    root = init_workspace(tmp_path)
    with writer_lock(root):
        hid = start_hypothesis(root)
        eid = records.reserve_evaluation(root, hid, 0, {'lookback_days':90}, 'test', 'fixture')
        records.mark_launched(root, eid)
        for key in ('folds', 'iteration_history'):
            result = fake_result(root, eid); del result[key]
            with pytest.raises((KeyError, ValueError)):
                records.complete_evaluation(root, eid, result)
            assert records.load_evaluations(root)[0]['status'] == 'launched'


def test_recovery_finishes_interrupted_infrastructure_ledger_outcome(tmp_path):
    from orchestrator import state
    root = init_workspace(tmp_path)
    with writer_lock(root):
        hid = start_hypothesis(root)
        records._write_json(root / 'hypotheses' / hid / 'decision.json',
                            {'decision':'KILL','kill_reason':'infrastructure','referee_model':'orchestrator'})
        state.append_ledger(root, 1, 'test_signal', 'KILLED(infrastructure)', None, None, None, None, False)
        assert records.recover(root) == ['H001: finalized infrastructure lifecycle']
        assert records.recover(root) == []
        assert records._read_json(records.lifecycle_path(root, hid))['state'] == 'infra-killed'
        assert (root / 'LEDGER.md').read_text().count('#1 |') == 1
