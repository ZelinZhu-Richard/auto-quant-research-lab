import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

from engine import trials
from engine.workspace import WorkspaceError, writer_lock
from orchestrator import records
from tests.v11_support import completed, git_init, init_workspace, start_hypothesis

DRIVER = Path(__file__).with_name("v11_interrupt_driver.py")


@pytest.mark.parametrize("cut", ["before_launch", "after_launch", "after_result", "after_completion",
    "after_decision_before_lifecycle", "after_s4_commit", "after_iterate_commit",
    "after_ledger_before_queue", "after_queue_before_lifecycle", "scored_terminal"])
def test_sigkill_persisted_invariants(tmp_path, cut):
    root = init_workspace(tmp_path, full=cut == "scored_terminal")
    git_init(root)
    readfd, writefd = os.pipe()
    p = subprocess.Popen([sys.executable, str(DRIVER), str(root), cut, str(writefd)],
                         pass_fds=(writefd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.close(writefd)
    try:
        ready, _, _ = select.select([readfd], [], [], 90)
        assert ready, "driver failed to reach deterministic cut point"
        assert os.read(readfd, 200).decode().strip() == cut
        p.kill()
        stdout, stderr = p.communicate(timeout=10)
        assert p.returncode == -signal.SIGKILL, stderr.decode()
    finally:
        os.close(readfd)
        if p.poll() is None:
            p.kill(); p.communicate(timeout=10)
    assert (root / ".quantlab.lock").exists()
    with pytest.raises(WorkspaceError, match="human"):
        with writer_lock(root): pass
    # Explicit HUMAN REMOVAL SIMULATION, only of this killed fixture's own lock.
    (root / ".quantlab.lock").unlink()
    decision_path = root / "hypotheses/H001/decision.json"
    decision_before = decision_path.read_bytes() if decision_path.exists() else None
    with writer_lock(root):
        records.recover(root)
        rec = records.load_evaluations(root)[0]
        expected = {"before_launch": "cancelled", "after_launch": "unresolved"}.get(cut, "completed")
        assert rec["status"] == expected
        lc = records._read_json(records.lifecycle_path(root, "H001"))
        terminal = cut in {"after_decision_before_lifecycle", "after_s4_commit",
                           "after_ledger_before_queue", "after_queue_before_lifecycle", "scored_terminal"}
        ledger = (root / "LEDGER.md").read_text()
        assert ledger.count("#1 |") == 1
        assert lc["state"] == ("closed" if terminal else "abandoned")
        if terminal:
            assert "PROMOTED" in ledger
            assert decision_path.read_bytes() == decision_before
            assert (root / "PROMOTED_QUEUE.md").read_text().count("H001 |") == 1
            assert "finalized-by-recovery" in (root / "hypotheses/H001/memo.md").read_text()
        else:
            assert "ABANDONED(" in ledger
            assert "H001 |" not in (root / "PROMOTED_QUEUE.md").read_text()
        if cut == "after_iterate_commit":
            assert "ABANDONED(interrupted-after-iterate)" in ledger
            assert decision_path.read_bytes() == decision_before
        assert trials.prior_trial_count(root) == (1 if expected == "completed" else 0)
        if cut == "scored_terminal":
            calls = [json.loads(line) for line in (root / "runs/interrupted/toolcalls.jsonl").read_text().splitlines()]
            assert len([c for c in calls if c.get("stage") == "S3-backtest"]) == 1
            assert rec["results"]["referee_inputs"]["trial_evaluation_ids"] == [rec["evaluation_id"]]
        frozen = records._eval_path(root, rec["evaluation_id"]).read_bytes()
        snapshot = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                    if p.is_file() and ".git" not in p.parts}
        assert records.recover(root) == []
        assert snapshot == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                            if p.is_file() and ".git" not in p.parts}
        assert records.next_hypothesis_number(root) == 2
        if expected == "completed":
            hid2 = start_hypothesis(root)
            completed(root, hid2)
            assert records._eval_path(root, rec["evaluation_id"]).read_bytes() == frozen
            assert trials.prior_trial_count(root) == 2


def test_unbound_decision_is_preserved_for_inspection(tmp_path):
    root = init_workspace(tmp_path)
    with writer_lock(root):
        hid = start_hypothesis(root)
        _, result = completed(root, hid)
        path = root / "hypotheses" / hid / "decision.json"
        wrong = {"decision": "PROMOTE", "evaluation_id": "E9999", "inputs": result["inputs"]}
        records._write_json(path, wrong)
        before = path.read_bytes()
        records.recover(root)
        assert not path.exists()
        assert path.with_name("decision.unbound.json").read_bytes() == before
        assert records._read_json(records.lifecycle_path(root, hid))["state"] == "abandoned"
