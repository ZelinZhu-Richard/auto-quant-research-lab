"""Commit-bound evaluation evidence and independent metric reconstruction."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from engine import config
from engine.errors import EngineError
from engine.metrics import (
    hit_rate,
    max_drawdown,
    sharpe_annualized,
    sharpe_daily,
    skew_and_raw_kurtosis,
)
from engine.policy import POLICY
from engine.trials import TrialJournal, canonical, digest, read_json


def git(root: Path, *args) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise EngineError(f"git evidence check failed: {args[0]}")
    return result.stdout.strip()


def committed_bytes(root: Path, path: str, revision="HEAD") -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True, timeout=30
    )
    if result.returncode:
        raise EngineError(f"missing committed evidence: {path}")
    return result.stdout


def require_committed(root: Path, path: str) -> bytes:
    current = (root / path).read_bytes()
    if current != committed_bytes(root, path):
        raise EngineError(f"uncommitted/changed evidence: {path}")
    return current


def input_identity(
    root: Path, hid: str, params: dict, iteration: int, data_hash: str
) -> dict:
    if not re.fullmatch(r"H\d{3,}", hid):
        raise EngineError("invalid hypothesis id")
    card_path = f"hypotheses/{hid}/hypothesis.md"
    signal_path = f"hypotheses/{hid}/signal.py"
    card, signal = (
        require_committed(root, card_path),
        require_committed(root, signal_path),
    )
    # The first committed card is the pre-registration, not an editable later version.
    births = git(
        root, "log", "--reverse", "--format=%H", "--diff-filter=A", "--", card_path
    ).splitlines()
    if len(births) != 1 or card != committed_bytes(root, card_path, births[0]):
        raise EngineError("pre-registration changed after first commit")
    signal_births = git(
        root, "log", "--reverse", "--format=%H", "--diff-filter=A", "--", signal_path
    ).splitlines()
    normalize = lambda b: re.sub(rb"(?m)^PARAMS\s*=.*$", b"PARAMS = {}", b)
    if len(signal_births) != 1 or normalize(signal) != normalize(
        committed_bytes(root, signal_path, signal_births[0])
    ):
        raise EngineError("signal logic changed after initial implementation")
    sources = ["SPEC_v2.md", "data/universe.json", "pyproject.toml", "uv.lock"]
    sources += [
        p.relative_to(root).as_posix() for p in sorted((root / "engine").glob("*.py"))
    ]
    hashes = {
        p: hashlib.sha256(require_committed(root, p)).hexdigest() for p in sources
    }
    return {
        "hypothesis_id": hid,
        "iteration": iteration,
        "params": params,
        "protocol": POLICY,
        "execution_mode": "delayed_close_v2",
        "card_sha256": hashlib.sha256(card).hexdigest(),
        "signal_sha256": hashlib.sha256(signal).hexdigest(),
        "data_manifest_sha256": data_hash,
        "source_hashes": hashes,
    }


def summarize(run: pd.DataFrame) -> tuple[dict, list]:
    net = run["r_net"]

    def metrics(window):
        return {
            "n_days": len(window),
            "sharpe_annualized": sharpe_annualized(window.r_net),
            "max_drawdown": max_drawdown(window.r_net),
            "hit_rate": hit_rate(window.r_net, window.has_positions),
        }

    g3, g4 = skew_and_raw_kurtosis(net)
    aggregate = {
        "start": str(config.AGGREGATE_START.date()),
        "end": str(config.AGGREGATE_END.date()),
        **metrics(run),
        "n_position_days": int(run.has_positions.sum()),
        "sharpe_daily": sharpe_daily(net),
        "turnover_annualized": float(run.turnover.mean()) * 365,
        "mean_daily_return": float(net.mean()),
        "std_daily_return": float(net.std(ddof=1)),
        "skew": g3,
        "kurtosis_raw": g4,
    }
    folds = [
        {
            "fold": i,
            "start": str(start.date()),
            "end": str(end.date()),
            **metrics(run.loc[start:end]),
        }
        for i, (start, end) in enumerate(config.FOLDS, 1)
    ]
    return aggregate, folds


def daily_records(run):
    return [{"date": str(date.date()), **row.to_dict()} for date, row in run.iterrows()]


def verify_daily(artifact):
    records = artifact["daily"]
    run = pd.DataFrame(records).set_index("date")
    run.index = pd.to_datetime(run.index, utc=True)
    expected = pd.date_range(config.AGGREGATE_START, config.AGGREGATE_END, tz="UTC")
    if not run.index.equals(expected):
        raise EngineError("daily evidence calendar mismatch")
    if any(type(r["has_positions"]) is not bool for r in records):
        raise EngineError("invalid position flag")
    numeric = run[["r_net", "r_gross", "cost", "turnover"]].to_numpy(dtype=float)
    if (
        not np.isfinite(numeric).all()
        or (run.r_net <= -1).any()
        or (run.turnover < 0).any()
    ):
        raise EngineError("invalid daily evidence")
    if not np.allclose(
        run.cost, run.turnover * config.COST_PER_SIDE, atol=1e-14, rtol=1e-12
    ):
        raise EngineError("inconsistent fee evidence")
    if not np.allclose(run.r_net, run.r_gross - run.cost, atol=1e-14, rtol=1e-12):
        raise EngineError("inconsistent net returns")
    aggregate, folds = summarize(run)
    if canonical(aggregate) != canonical(artifact["aggregate"]) or canonical(
        folds
    ) != canonical(artifact["folds"]):
        raise EngineError("metrics disagree with daily evidence")


def verify_evidence(root: Path, hid: str, results: dict) -> dict:
    """Authenticate all upstream artifacts before binding any decision."""
    try:
        if (
            results["protocol"] != POLICY
            or results["hypothesis_id"] != hid
            or results["status"] != "ok"
        ):
            raise EngineError("invalid result provenance")
        stored = json.loads(require_committed(root, f"hypotheses/{hid}/results.json"))
        if canonical(stored) != canonical(results):
            raise EngineError("results differ from committed evidence")
        require_committed(root, f"hypotheses/{hid}/iterations.json")
        if (
            read_json(root / f"hypotheses/{hid}/iterations.json")
            != results["iteration_history"]
        ):
            raise EngineError("result iteration history differs from committed history")
        journal = TrialJournal(root)
        tracked = git(root, "ls-files", "trial_evidence").splitlines()
        for path in tracked:
            require_committed(root, path)
        events = journal.events()
        if not events or events[-1]["sha256"] != results["evidence"]["journal_head"]:
            raise EngineError("stale/missing trial snapshot")
        event = events[-1]
        trial_id = results["evidence"]["trial_id"]
        if event["kind"] != "scored" or event["trial_id"] != trial_id:
            raise EngineError("result has no scored trial receipt")
        for i in range(1, len(events) + 1):
            require_committed(root, f"trial_evidence/events/{i:08d}.json")
        artifact = read_json(root / "trial_evidence" / "scores" / f"{trial_id}.json")
        require_committed(root, f"trial_evidence/scores/{trial_id}.json")
        if digest(artifact) != event["artifact_sha256"]:
            raise EngineError("score artifact hash mismatch")
        identity = input_identity(
            root,
            hid,
            results["params"],
            results["iteration"],
            results["engine"]["data_manifest_sha256"],
        )
        if digest(identity) != trial_id or artifact["identity"] != identity:
            raise EngineError("evaluated code/card/data identity mismatch")
        # Source commit must be an ancestor containing those exact inputs.
        source_commit = results["engine"]["git_commit"]
        git(root, "merge-base", "--is-ancestor", source_commit, "HEAD")
        for path, sha in identity["source_hashes"].items():
            if (
                hashlib.sha256(committed_bytes(root, path, source_commit)).hexdigest()
                != sha
            ):
                raise EngineError("evaluated source commit mismatch")
        for path, sha in [
            (f"hypotheses/{hid}/signal.py", identity["signal_sha256"]),
            (f"hypotheses/{hid}/hypothesis.md", identity["card_sha256"]),
        ]:
            if (
                hashlib.sha256(committed_bytes(root, path, source_commit)).hexdigest()
                != sha
            ):
                raise EngineError("evaluated hypothesis commit mismatch")
        if artifact.get("harness_passed") is not True:
            raise EngineError("missing independent harness evidence")
        from engine.loader import data_manifest_sha256

        if (
            data_manifest_sha256(root / "data" / "train_val")
            != identity["data_manifest_sha256"]
        ):
            raise EngineError("current dataset differs from evaluated dataset")
        verify_daily(artifact)
        if any(
            canonical(results[k]) != canonical(artifact[k])
            for k in ("aggregate", "folds")
        ):
            raise EngineError("result metrics disagree with score artifact")
        accounting = journal.accounting()
        if any(
            canonical(results["referee_inputs"][k]) != canonical(v)
            for k, v in accounting.items()
        ):
            raise EngineError("forged trial accounting")
        if results["cost_bps_per_side"] != 25:
            raise EngineError("cost policy mismatch")
        return artifact
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise EngineError(f"untrustworthy evidence: {exc}") from exc


def verify_journal_committed(root):
    """No uncommitted history may be silently accepted as a new baseline."""
    status = git(root, "status", "--porcelain", "--", "trial_evidence")
    if status:
        raise EngineError(
            "trial evidence has uncommitted changes; reconcile before evaluation"
        )
    for path in git(root, "ls-files", "trial_evidence").splitlines():
        require_committed(root, path)


def verify_prior_iterations(journal, identity):
    """A forged history list cannot skip the earlier registered evaluations."""
    events = journal.events()
    starts = {e["attempt_id"]: e for e in events if e["kind"] == "started"}
    prior = [
        starts[e["attempt_id"]]["identity"] for e in events if e["kind"] == "scored"
    ]
    for iteration in range(identity["iteration"]):
        matches = [
            p
            for p in prior
            if p.get("hypothesis_id") == identity["hypothesis_id"]
            and p.get("iteration") == iteration
            and all(
                p.get(k) == identity[k]
                for k in (
                    "protocol",
                    "card_sha256",
                    "data_manifest_sha256",
                    "source_hashes",
                )
            )
        ]
        if not matches:
            raise EngineError("iteration lacks prior scored evidence")
