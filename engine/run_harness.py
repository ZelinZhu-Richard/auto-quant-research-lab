"""Register every S2/repair/iteration real-data harness BEFORE signal import.

No numeric score is inferred from a harness pass. The same source/params/data
identity shares the subsequent S3 statistical slot. Repaired source gets a new
slot. SIGKILL leaves a pending start and blocks further evaluation.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from engine.evidence import git, input_identity, require_committed, verify_prior_iterations
from engine.errors import EngineError
from engine.loader import data_manifest_sha256
from engine.policy import validate_iteration
from engine.trials import TrialJournal, immutable_write, read_json


def run_registered_harness(root: Path, hid: str) -> int:
    from orchestrator.stages import validate_hypothesis_md, validate_signal_source
    if not re.fullmatch(r"H\d{3,}", hid):
        raise EngineError("invalid hypothesis identifier")
    hyp = root / "hypotheses" / hid
    source = (hyp / "signal.py").read_text()
    params = validate_signal_source(source)
    blocks = validate_hypothesis_md((hyp / "hypothesis.md").read_text())
    history = read_json(hyp / "iterations.json")
    iteration = history[-1]["iteration"]
    validate_iteration(blocks, {"iteration": iteration, "params": params,
                                "iteration_history": history})
    data = root / "data" / "train_val"
    if data.is_symlink():
        raise EngineError("symlinked dataset forbidden")
    identity = input_identity(root, hid, params, iteration, data_manifest_sha256(data),
                              uncommitted_signal=True)
    journal = TrialJournal(root)
    with journal.writer():
        # Preserve the committed prefix; S2 may have an uncommitted FAILED
        # receipt from its one allowed repair, but never an unresolved attempt.
        for path in git(root, "ls-files", "trial_evidence").splitlines():
            require_committed(root, path)
        if journal.accounting()["pending_attempts"]:
            raise EngineError("unresolved attempt before harness")
        verify_prior_iterations(journal, identity)
        start = journal.start(identity, git(root, "rev-parse", "HEAD"), phase="harness")
        try:
            immutable_write(journal.directory / "sources" / f"{identity['signal_sha256']}.json",
                            {"source": source, "signal_sha256": identity["signal_sha256"]})
            environment = {**os.environ, "QUANTLAB_HYPOTHESIS": hid}
            process = subprocess.run([sys.executable, "-m", "pytest", "tests/hypothesis_harness.py",
                                      "-q", "--no-header", "-x"], cwd=root, env=environment)
            if process.returncode:
                journal.fail(start, f"harness exited {process.returncode}; no numeric score")
            else:
                journal.checked(start)
            return process.returncode
        except Exception as exc:
            journal.fail(start, f"harness infrastructure: {type(exc).__name__}: {exc}"[:300])
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    return run_registered_harness(Path(args.repo_root).resolve(), args.hypothesis)


if __name__ == "__main__":
    raise SystemExit(main())
