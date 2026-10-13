import json
from pathlib import Path
import subprocess
import sys

import pytest

from engine.run_backtest import build_results
from engine.workspace import writer_lock, require_writer
from orchestrator import records
from orchestrator.loop import Loop
from orchestrator.stages import run_backtest
from scripts.create_fixture import create_fixture
from tests.v11_support import BLOCKS, ROOT, init_workspace, git_init, start_hypothesis


def test_engine_binds_actual_signal_bytes_and_data_and_scores_itself_once(tmp_path):
    root = init_workspace(tmp_path, full=True)
    git_init(root)
    with writer_lock(root):
        hid = start_hypothesis(root)
        eid = records.reserve_evaluation(root, hid, 0, BLOCKS["params"], "test", "fixture")
        # A Git bookkeeping commit between reservation and scoring is legitimate.
        subprocess.run(["git", "add", "hypotheses", "evaluations"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "reserve"], cwd=root, check=True, capture_output=True)
        records.mark_launched(root, eid)
        loop = Loop(root, "dry-run", "test", 1, 1, 25, 1)
        call = run_backtest(loop.log, root, hid, 60, evaluation_id=eid,
                            record_kind="fixture", writer_token=require_writer(root))
        assert call.exit_code == 0, call.stderr
        result = json.loads((root / "hypotheses" / hid / "results.json").read_text())
        records.complete_evaluation(root, eid, result)
        assert result["inputs"] == records.load_evaluations(root)[0]["inputs"]
        assert result["referee_inputs"]["n_trials"] == 1
        assert result["referee_inputs"]["trial_evaluation_ids"] == [eid]
        assert result["engine"]["git_commit"] != records.load_evaluations(root)[0]["git_lineage"]
        assert len(result["folds"]) == 4 and result["aggregate"]["n_days"] == 973
        with pytest.raises(RuntimeError, match="boundary"):
            records.mark_launched(root, eid)


@pytest.mark.parametrize("changed", ["signal", "spec", "data", "engine"])
def test_changed_evaluated_input_rejected_by_engine(tmp_path, changed):
    root = init_workspace(tmp_path, full=True)
    with writer_lock(root):
        hid = start_hypothesis(root)
        eid = records.reserve_evaluation(root, hid, 0, BLOCKS["params"], "test", "fixture")
        records.mark_launched(root, eid)
        paths = {"signal": root / "hypotheses" / hid / "signal.py", "spec": root / "SPEC.md",
                 "engine": root / "engine/config.py"}
        if changed == "data":
            import pandas as pd
            p = root / "data/train_val/S00.parquet"
            df = pd.read_parquet(p); df["close"] *= 1.01; df.to_parquet(p)
        else:
            with paths[changed].open("a") as fh: fh.write("\n# changed bytes\n")
        with pytest.raises(Exception, match="mismatch|does not match"):
            build_results(hid, root, root / "data/train_val", evaluation_id=eid)


def test_engine_kind_mismatch_has_no_file_writes(tmp_path):
    root = init_workspace(tmp_path, full=True)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    child = subprocess.run([sys.executable, "-m", "engine.run_backtest", "--repo-root", str(root),
                            "--hypothesis", "H001", "--evaluation-id", "E0001",
                            "--record-kind", "research", "--writer-token", "no-lease"],
                           cwd=root, capture_output=True, text=True, timeout=20)
    assert child.returncode != 0 and "kind" in child.stderr
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_clean_fixture_initializer_and_full_fake_model_cycle(tmp_path):
    source = init_workspace(tmp_path / "source", full=True)
    # Dirty synthetic source history is deliberately excluded by the allowlist.
    (source / "LEDGER.md").write_text("#999 | research_secret_id | PROMOTED\n")
    (source / "hypotheses/H999").mkdir()
    (source / "hypotheses/H999/lifecycle.json").write_text('{"record_kind":"research"}')
    (source / "runs").mkdir()
    (source / "runs/private.log").write_text("research secret")
    fixture = create_fixture(source, tmp_path / "fixture")
    assert not (fixture / "runs").exists()
    assert not list((fixture / "hypotheses").iterdir())
    assert not list((fixture / "evaluations").iterdir())
    assert "999" not in (fixture / "LEDGER.md").read_text()
    assert len(subprocess.run(["git", "log", "--oneline"], cwd=fixture,
                             capture_output=True, text=True, check=True).stdout.splitlines()) == 1
    manifest = json.loads((fixture / "WORKSPACE.json").read_text())
    assert manifest["kind"] == "fixture" and len(manifest["data_manifest_sha256"]) == 64
    loop = Loop(fixture, "dry-run", "acceptance", 2, 1, 25, 2)
    assert loop.run() == 0
    recs = records.load_evaluations(fixture)
    assert recs and all(r["status"] == "completed" and r["record_kind"] == "fixture" for r in recs)
    for n, rec in enumerate(recs, 1):
        assert rec["results"]["referee_inputs"]["n_trials"] == n
        assert len(rec["results"]["referee_inputs"]["trial_evaluation_ids"]) == n
    calls = [json.loads(line) for line in (fixture / "runs/acceptance/toolcalls.jsonl").read_text().splitlines()]
    s3 = [call for call in calls if call.get("stage") == "S3-backtest"]
    assert len(s3) == len(recs)
    ids = [call["command"][call["command"].index("--evaluation-id") + 1] for call in s3]
    assert sorted(ids) == sorted(r["evaluation_id"] for r in recs)
    ledger = (fixture / "LEDGER.md").read_text()
    assert sum(line.startswith(("#1 |", "#2 |")) for line in ledger.splitlines()) == 2
    assert "#999 |" not in ledger and "research_secret_id" not in ledger
    assert (source / "LEDGER.md").read_text() == "#999 | research_secret_id | PROMOTED\n"


def test_initializer_refuses_source_alias_existing_destination_and_links(tmp_path):
    source = init_workspace(tmp_path / "source", full=True)
    with pytest.raises(ValueError, match="disjoint"):
        create_fixture(source, source / "nested")
    existing = tmp_path / "existing"; existing.mkdir()
    with pytest.raises(ValueError, match="not exist"):
        create_fixture(source, existing)
    (source / "scripts/foreign-link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        create_fixture(source, tmp_path / "safe")
    assert not (tmp_path / "safe").exists()


def test_demo_finalizes_on_failure_and_excludes_unrelated_staged_files(tmp_path):
    import os
    import shlex
    source = init_workspace(tmp_path / 'source', full=True)
    destination = tmp_path / 'demo'
    fake_python = tmp_path / 'fake-python'
    fake_python.write_text('#!/bin/sh\n' +
        'if [ "$1" = "-m" ]; then\n' +
        '  mkdir -p runs/failure\n  echo test > runs/failure/summary.json\n' +
        '  echo unrelated > unrelated.txt\n  git add unrelated.txt\n  exit 13\nfi\n' +
        'exec ' + shlex.quote(sys.executable) + ' "$@"\n')
    fake_python.chmod(0o700)
    env = dict(os.environ, QUANTLAB_PYTHON=str(fake_python))
    child = subprocess.run(['bash', str(source / 'scripts/demo.sh'), str(destination)],
                           env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode == 13, child.stdout + child.stderr
    changed = subprocess.run(['git', 'show', '--format=', '--name-only', 'HEAD'], cwd=destination,
                             capture_output=True, text=True, check=True).stdout
    assert 'runs/failure/summary.json' in changed and 'unrelated.txt' not in changed
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=destination,
                            capture_output=True, text=True, check=True).stdout
    assert status == 'A  unrelated.txt\n'
