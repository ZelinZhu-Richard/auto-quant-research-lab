"""Durable lifecycle/evaluation records, adapted from the v1.1 proposal.

Every mutator requires exclusive writer ownership. A persisted `launched`
boundary means the engine MAY have started; missing evidence is unresolved.
Completed records retain the full result and the exact historical trial set.
"""

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from engine.identity import HASH_KEYS, git_lineage, input_identity
from engine.trials import COMPLETED, prior_snapshot
from engine.workspace import (assert_ready, assert_scoped_history, read_workspace, require_writer,
                              safe_id)

RECORD_KINDS = ("research", "fixture", "synthetic", "replay")
LIFECYCLE_TERMINAL = ("closed", "infra-killed", "abandoned")


def _now():
    return datetime.now(timezone.utc).isoformat()


def canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def result_hash(payload) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _write_json(path: Path, payload: dict) -> None:
    atomic_text(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: record must be an object")
    return value


def lifecycle_path(root: Path, hid: str) -> Path:
    return root / "hypotheses" / safe_id(hid, r"H\d{3,}") / "lifecycle.json"


def _eval_path(root: Path, eid: str) -> Path:
    return root / "evaluations" / (safe_id(eid, r"E\d{4,}") + ".json")


def evaluations_dir(root: Path) -> Path:
    return root / "evaluations"


def load_evaluations(root: Path) -> list[dict]:
    return [_read_json(p) for p in sorted(evaluations_dir(root).glob("E*.json"))
            if re.fullmatch(r"E\d{4,}\.json", p.name)]


def load_lifecycles(root: Path) -> list[dict]:
    return [_read_json(p) for p in sorted((root / "hypotheses").glob("H*/lifecycle.json"))]


def next_hypothesis_number(root: Path) -> int:
    assert_scoped_history(root)
    numbers = [0]
    # Include pre-records directories even if they never reached the ledger.
    numbers += [int(p.name[1:]) for p in (root / "hypotheses").glob("H*")
                if re.fullmatch(r"H\d{3,}", p.name)]
    ledger = root / "LEDGER.md"
    if ledger.exists():
        numbers += [int(m.group(1)) for line in ledger.read_text().splitlines()
                    if (m := re.match(r"^#(\d+)\s*\|", line))]
    return max(numbers) + 1


def reserve_hypothesis(root: Path, run_id: str, record_kind: str) -> tuple[int, str]:
    require_writer(root)
    assert_ready(root, record_kind)
    number = next_hypothesis_number(root)
    hid = f"H{number:03d}"
    path = lifecycle_path(root, hid)
    if path.parent.exists():
        raise RuntimeError(f"refusing to reuse {hid}")
    _write_json(path, {"hypothesis_id": hid, "number": number, "record_kind": record_kind,
                       "run_id": run_id, "state": "reserved", "created": _now(),
                       "history": [{"state": "reserved", "at": _now()}]})
    return number, hid


def update_lifecycle(root: Path, hid: str, state: str, note: str = "", **fields) -> dict:
    require_writer(root)
    path = lifecycle_path(root, hid)
    value = _read_json(path)
    if value["state"] in LIFECYCLE_TERMINAL:
        if value["state"] == state:
            return value
        raise RuntimeError(f"{hid} already terminal")
    value.update(state=state, updated=_now(), **fields)
    value["history"].append({"state": state, "at": _now(), "note": note})
    _write_json(path, value)
    return value


def reserve_evaluation(root: Path, hid: str, iteration: int, params: dict,
                       run_id: str, record_kind: str) -> str:
    require_writer(root)
    assert_ready(root, record_kind)
    assert_scoped_history(root)
    if any(r["status"] in ("reserved", "launched", "unresolved")
           for r in load_evaluations(root)):
        raise RuntimeError("pending or unresolved evaluation blocks reservation")
    lc = _read_json(lifecycle_path(root, hid))
    if lc["state"] in LIFECYCLE_TERMINAL or lc["record_kind"] != record_kind:
        raise RuntimeError("hypothesis is terminal or has a different kind")
    if type(iteration) is not int or not 0 <= iteration <= 2 or not isinstance(params, dict):
        raise ValueError("invalid iteration/params")
    existing = [int(p.stem[1:]) for p in evaluations_dir(root).glob("E*.json")
                if re.fullmatch(r"E\d{4,}", p.stem)]
    eid = f"E{max(existing, default=0) + 1:04d}"
    snapshot = prior_snapshot(root)
    contributors = [_read_json(_eval_path(root, prior)) for prior in snapshot["evaluation_ids"]]
    snapshot["result_hashes"] = [r["result_sha256"] for r in contributors]
    _write_json(_eval_path(root, eid), {
        "evaluation_id": eid, "hypothesis_id": hid, "iteration": iteration,
        "params": params, "run_id": run_id, "record_kind": record_kind,
        "status": "reserved", "reserved_at": _now(), "source": "run",
        "inputs": input_identity(root, hid), "git_lineage": git_lineage(root),
        "prior_trials": snapshot,
    })
    return eid


def mark_launched(root: Path, eid: str) -> dict:
    require_writer(root)
    path = _eval_path(root, eid)
    record = _read_json(path)
    if record["status"] != "reserved":
        raise RuntimeError(f"{eid}: launch boundary already crossed or reservation terminal")
    if record["inputs"] != input_identity(root, record["hypothesis_id"]):
        cancel_evaluation(root, eid, "input changed before launch")
        raise RuntimeError("input mismatch before launch")
    record.update(status="launched", launch_boundary_at=_now())
    _write_json(path, record)  # durable BEFORE subprocess can start
    return record


def cancel_evaluation(root: Path, eid: str, reason: str) -> dict:
    require_writer(root)
    path = _eval_path(root, eid)
    rec = _read_json(path)
    if rec["status"] == "cancelled":
        return rec
    if rec["status"] != "reserved":
        raise RuntimeError("only a known never-launched reservation can be cancelled automatically")
    rec.update(status="cancelled", reason=reason, resolved_at=_now())
    _write_json(path, rec)
    return rec


def unresolved(root: Path, eid: str, reason: str) -> dict:
    require_writer(root)
    path = _eval_path(root, eid)
    rec = _read_json(path)
    if rec["status"] == "unresolved":
        return rec
    if rec["status"] != "launched":
        raise RuntimeError("only a possible launch becomes unresolved")
    rec.update(status="unresolved", reason=reason, resolved_at=_now())
    _write_json(path, rec)
    return rec


def validate_result(root: Path, rec: dict, result: dict) -> None:
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    for key in ("evaluation_id", "hypothesis_id", "iteration", "params", "record_kind"):
        if result.get(key) != rec[key] or (key == "iteration" and type(result.get(key)) is not int):
            raise ValueError(f"{key} mismatch")
    if result.get("status") != "ok":
        raise ValueError("status is not ok")
    inputs = result.get("inputs", {})
    for key in HASH_KEYS:
        if not re.fullmatch(r"[a-f0-9]{64}", str(inputs.get(key, ""))) or inputs.get(key) != rec["inputs"][key]:
            raise ValueError(f"input mismatch: {key}")
    # Revalidate the preserved prior set by identity, not by today's count.
    prior = rec["prior_trials"]
    if len(set(prior["evaluation_ids"])) != len(prior["evaluation_ids"]) or rec["evaluation_id"] in prior["evaluation_ids"]:
        raise ValueError("duplicate/current prior evaluation")
    if not len(prior["evaluation_ids"]) == len(prior["sharpes_daily"]) == len(prior["result_hashes"]):
        raise ValueError("incomplete historical prior set")
    for eid, sharpe, sha in zip(prior["evaluation_ids"], prior["sharpes_daily"], prior["result_hashes"]):
        old = _read_json(_eval_path(root, eid))
        if (old["status"] not in COMPLETED or old["record_kind"] != rec["record_kind"]
                or old["metrics"]["sharpe_daily"] != sharpe or old["result_sha256"] != sha
                or result_hash(old["results"]) != sha):
            raise ValueError(f"historical contributor mismatch: {eid}")
    agg, ref = result["aggregate"], result["referee_inputs"]
    for key in ("sharpe_annualized", "sharpe_daily", "max_drawdown", "hit_rate"):
        x = agg[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"non-finite/invalid metric {key}")
    if type(agg["n_days"]) is not int or agg["n_days"] < 2:
        raise ValueError("n_days must be at least 2")
    dsr = ref["deflated_sharpe"]
    if isinstance(dsr, bool) or not isinstance(dsr, (int, float)) or not math.isfinite(dsr) or not 0 <= dsr <= 1:
        raise ValueError("invalid deflated_sharpe")
    if type(ref["n_trials"]) is not int or ref["n_trials"] != len(prior["evaluation_ids"]) + 1:
        raise ValueError("n_trials mismatch")
    if not 0 <= agg["max_drawdown"] <= 1 or not 0 <= agg["hit_rate"] <= 1:
        raise ValueError("metric fraction outside [0,1]")
    folds = result["folds"]
    if not isinstance(folds, list) or len(folds) != 4:
        raise ValueError("four fold metrics required")
    for fold in folds:
        value = fold["sharpe_annualized"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("invalid fold metric")
    history = result["iteration_history"]
    if (not isinstance(history, list) or len(history) != rec["iteration"] + 1
            or any(type(entry["iteration"]) is not int or entry["iteration"] != i
                   or not isinstance(entry["params"], dict) for i, entry in enumerate(history))
            or history[-1]["params"] != rec["params"]):
        raise ValueError("iteration history mismatch")
    expected = {"prior_evaluation_ids": prior["evaluation_ids"],
                "prior_result_hashes": prior["result_hashes"],
                "trial_evaluation_ids": prior["evaluation_ids"] + [rec["evaluation_id"]],
                "trial_sharpes_daily": prior["sharpes_daily"] + [agg["sharpe_daily"]]}
    for key, value in expected.items():
        if ref.get(key) != value:
            raise ValueError(f"historical trial input mismatch: {key}")
    # Reject NaN/Infinity anywhere, including optional metric fields.
    canonical(result)


def complete_evaluation(root: Path, eid: str, results: dict, source: str = "run") -> dict:
    require_writer(root)
    path = _eval_path(root, eid)
    rec = _read_json(path)
    if rec["status"] in COMPLETED:
        if rec["result_sha256"] == result_hash(results):
            return rec
        raise RuntimeError("completed record is immutable; different result refused")
    if rec["status"] != "launched":
        raise RuntimeError(f"cannot complete {rec['status']} evaluation")
    validate_result(root, rec, results)
    rec.update(status="completed", completed_at=_now(), completion_source=source,
               results=results, result_sha256=result_hash(results),
               metrics={**results["aggregate"], **results["referee_inputs"]})
    _write_json(path, rec)
    return rec


def bound_decision(root: Path, hid: str) -> tuple[dict, dict] | None:
    path = lifecycle_path(root, hid).parent / "decision.json"
    if not path.exists():
        return None
    try:
        d = _read_json(path)
        rec = _read_json(_eval_path(root, d["evaluation_id"]))
        if (rec["status"] not in COMPLETED or rec["hypothesis_id"] != hid
                or d["hypothesis_id"] != hid or d["inputs"] != rec["inputs"]
                or d.get("rule_version") != "rule:spec-v1.1"
                or d["iteration"] != rec["iteration"]
                or d["decision"] not in ("KILL", "PROMOTE", "ITERATE")
                or result_hash(rec["results"]) != rec["result_sha256"]):
            return None
        validate_result(root, rec, rec["results"])
        return d, rec
    except (KeyError, ValueError, TypeError, OSError):
        return None


def _hypothesis_name(root: Path, hid: str) -> str:
    path = lifecycle_path(root, hid).parent / "hypothesis.md"
    if path.exists():
        m = re.search(rf"#\s*{hid}\s*—\s*(\S+)", path.read_text())
        if m:
            return m.group(1)
    return "unnamed"


def finalize_terminal(root: Path, hid: str, *, recovery=False) -> None:
    from orchestrator import state
    require_writer(root)
    bound = bound_decision(root, hid)
    if not bound or bound[0]["decision"] == "ITERATE":
        raise RuntimeError("no bound terminal decision")
    d, rec = bound
    lc = _read_json(lifecycle_path(root, hid))
    if lc["state"] == "closed":
        return
    hyp = lifecycle_path(root, hid).parent
    if not (hyp / "memo.md").exists():
        atomic_text(hyp / "memo.md", f"# Memo — {hid}\n\nmemo_status: finalized-by-recovery\n"
                    f"decision: {d['decision']}\nevaluation_id: {rec['evaluation_id']}\n"
                    "Recovered the preserved decision; no new scientific judgment.\n")
    agg, ref = rec["results"]["aggregate"], rec["results"]["referee_inputs"]
    label = "PROMOTED" if d["decision"] == "PROMOTE" else "KILLED(merits)"
    if rec["iteration"]:
        label = "ITERATED->" + label
    name = _hypothesis_name(root, hid)
    state.append_ledger(root, lc["number"], name, label, agg["sharpe_annualized"],
                        ref["deflated_sharpe"], agg["max_drawdown"], agg["hit_rate"], True)
    if d["decision"] == "PROMOTE":
        state.append_promoted(root, hid, name)
    update_lifecycle(root, hid, "closed", note="finalized-by-recovery" if recovery else "finalized",
                     decision=d["decision"], evaluation_id=d["evaluation_id"])


def recover(root: Path) -> list[str]:
    from orchestrator import state
    require_writer(root)
    assert_scoped_history(root)
    actions = []
    for rec in load_evaluations(root):
        eid = rec["evaluation_id"]
        if rec["status"] == "reserved":
            cancel_evaluation(root, eid, "recovery: durable launch boundary not crossed")
            actions.append(f"{eid}: cancelled (never launched)")
        elif rec["status"] == "launched":
            try:
                result = _read_json(root / "hypotheses" / rec["hypothesis_id"] / "results.json")
                complete_evaluation(root, eid, result, source="recovery")
                actions.append(f"{eid}: completed from preserved engine evidence")
            except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                unresolved(root, eid, f"recovery: {type(exc).__name__}: {exc}")
                actions.append(f"{eid}: unresolved ({exc})")
    for lc in load_lifecycles(root):
        if lc["state"] in LIFECYCLE_TERMINAL:
            continue
        hid = lc["hypothesis_id"]
        bound = bound_decision(root, hid)
        if bound and bound[0]["decision"] in ("KILL", "PROMOTE"):
            finalize_terminal(root, hid, recovery=True)
            actions.append(f"{hid}: finalized terminal decision")
            continue
        reason = "interrupted-after-iterate" if bound else "interrupted"
        decision = lifecycle_path(root, hid).parent / "decision.json"
        unbound = decision.with_name("decision.unbound.json")
        if not bound and unbound.exists() and not decision.exists():
            reason = "unbound-decision"  # interrupted after the evidence rename
        # An already appended infrastructure outcome is a completed R4 action,
        # not a new rule verdict. Finish its lifecycle after an interrupted append.
        ledger_lines = (root / "LEDGER.md").read_text().splitlines()
        existing = [line for line in ledger_lines if re.match(rf"^#{lc['number']}\s*\|", line)]
        if not bound and len(existing) == 1 and " | KILLED(infrastructure) | " in existing[0]:
            update_lifecycle(root, hid, "infra-killed", note="recovered existing infrastructure ledger outcome")
            actions.append(f"{hid}: finalized infrastructure lifecycle")
            continue
        if decision.exists() and not bound:
            if unbound.exists():
                raise RuntimeError("unbound decision evidence already exists; refusing overwrite")
            decision.replace(unbound)
            reason = "unbound-decision"
        state.append_ledger(root, lc["number"], _hypothesis_name(root, hid),
                            f"ABANDONED({reason})", None, None, None, None, False)
        update_lifecycle(root, hid, "abandoned", note=reason)
        actions.append(f"{hid}: abandoned ({reason})")
    return actions


def reconcile(root: Path, eid: str, disposition: str, note: str,
              evidence: dict | None = None, *, human_authorized=False) -> dict:
    """Explicit human command only; never called by loop/recovery."""
    require_writer(root)
    if not human_authorized or not note.strip():
        raise RuntimeError("human authorization and a reconciliation note are required")
    path = _eval_path(root, eid)
    rec = _read_json(path)
    if rec["status"] != "unresolved":
        raise RuntimeError("only unresolved evaluations can be reconciled")
    if disposition == "completed-manual":
        validate_result(root, rec, evidence)
        rec.update(results=evidence, result_sha256=result_hash(evidence),
                   metrics={**evidence["aggregate"], **evidence["referee_inputs"]})
    elif disposition != "cancelled":
        raise ValueError("disposition must be cancelled or completed-manual")
    rec.update(status=disposition, reconciliation_note=note, reconciled_at=_now(),
               reconciliation_authority="human")
    _write_json(path, rec)
    return rec
