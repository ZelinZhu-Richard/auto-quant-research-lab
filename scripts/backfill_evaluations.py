"""Evidence-backed legacy inventory/import. Never infers provenance from a run name.

Default is a read-only inventory of an explicit revision. --apply adds records
only when every result has a matching successful call in a run with known mode.
It does not approve migration_ready or reconcile any unresolved research record.
Legacy IDs are assigned at import, never represented as original launch IDs.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.workspace import read_workspace, require_writer, writer_lock
from orchestrator import records


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, timeout=30).stdout


def at(root, revision, path):
    return json.loads(git(root, "show", f"{revision}:{path}"))


def inventory(root: Path, revision: str) -> dict:
    revision = git(root, "rev-parse", f"{revision}^{{commit}}").strip()
    paths = git(root, "ls-tree", "-r", "--name-only", revision, "--", "hypotheses", "runs").splitlines()
    summaries = {p.rsplit("/", 1)[0]: at(root, revision, p) for p in paths if p.endswith("/summary.json")}
    calls, issues = [], []
    for p in paths:
        if not p.endswith("/toolcalls.jsonl"):
            continue
        directory = p.rsplit("/", 1)[0]
        mode = summaries.get(directory, {}).get("mode")
        kind = {"live": "research", "dry-run": "fixture"}.get(mode)
        for line_number, line in enumerate(git(root, "show", f"{revision}:{p}").splitlines(), 1):
            row = json.loads(line)
            if row.get("stage") != "S3-backtest":
                continue
            command = row.get("command", [])
            try:
                hid = command[command.index("--hypothesis") + 1]
            except (ValueError, IndexError):
                issues.append(f"unidentified S3 call: {p}:{line_number}")
                continue
            calls.append({"hypothesis_id": hid, "record_kind": kind, "run_path": directory,
                          "log_path": p, "log_line": line_number, "ts": row.get("ts"),
                          "duration_s": row.get("duration_s"), "exit_code": row.get("exit_code"),
                          "engine_command": "engine.run_backtest" in command, "matched": False})
    found, seen_results = [], set()
    # Inspect every committed results change, not commit titles or hypothesis names.
    candidates_at = []
    for sha in git(root, "log", "--reverse", "--format=%H", revision, "--", "hypotheses").splitlines():
        changed = git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "--diff-filter=AM", "-r", sha, "--", "hypotheses").splitlines()
        for path in changed:
            if re.fullmatch(r"hypotheses/H\d{3,}/results\.json", path):
                candidates_at.append((sha, path))
    for sha, path in candidates_at:
        hid = path.split("/")[1]
        result = at(root, sha, path)
        identity = (hid, records.result_hash(result))
        if identity in seen_results:
            continue
        seen_results.add(identity)
        try:
            generated = datetime.fromisoformat(result["generated_at"])
            candidates = []
            for call in calls:
                started = datetime.fromisoformat(call["ts"].replace("Z", "+00:00"))
                lag = (generated - started).total_seconds()
                if (not call["matched"] and call["hypothesis_id"] == hid
                        and call["engine_command"] and call["exit_code"] == 0
                        and call["record_kind"] and 0 <= lag <= float(call["duration_s"]) + 2):
                    candidates.append(call)
        except (KeyError, TypeError, ValueError):
            candidates = []
        if len(candidates) != 1 or result.get("status") != "ok":
            issues.append(f"uncertain provenance/result: {sha} {hid}")
            continue
        call = candidates[0]; call["matched"] = True
        found.append({"source_commit": sha, "hypothesis_id": hid,
                      "record_kind": call["record_kind"], "results": result,
                      "result_sha256": records.result_hash(result),
                      "run_path": call["run_path"], "log_path": call["log_path"], "log_line": call["log_line"]})
    for call in calls:
        if not call["matched"]:
            issues.append(f"unmatched S3 evidence: {call['log_path']}:{call['log_line']}")
    outcomes = []
    result_hids = {r["hypothesis_id"] for r in found}
    for p in paths:
        if p.endswith("/decision.json"):
            d = at(root, revision, p); hid = p.split("/")[1]
            stage = re.search(r"\bS([1-5])\b", str(d.get("justification", "")))
            before_s3 = d.get("kill_reason") == "infrastructure" and stage and int(stage.group(1)) < 3
            outcomes.append({"hypothesis_id": hid, "decision": d.get("decision"),
                             "kill_reason": d.get("kill_reason"),
                             "classification": "supported-completed-evaluation" if hid in result_hids else
                             "recorded-failure-before-S3" if before_s3 else "uncertain"})
            if not before_s3 and hid not in result_hids:
                issues.append(f"unclassified historical outcome: {hid}")
        if p.endswith("/results.json"):
            current = at(root, revision, p)
            if not any(records.result_hash(current) == r["result_sha256"] for r in found):
                issues.append(f"unmatched current result artifact: {p}")
    known = {outcome["hypothesis_id"] for outcome in outcomes}
    historical_ids = {p.split("/")[1] for p in paths if re.match(r"hypotheses/H\d{3,}/", p)}
    for hid in sorted(historical_ids - known):
        outcomes.append({"hypothesis_id": hid, "classification": "uncertain", "decision": None, "kill_reason": None})
        issues.append(f"historical hypothesis without a terminal outcome: {hid}")
    return {"source_revision": revision, "evaluations": found, "hypotheses": outcomes,
            "issues": issues, "readiness": "needs-human-cutover-review" if not issues else "blocked-uncertainty",
            "coverage_limit": "Captured committed artifacts only. Unlogged/uncommitted pre-record attempts cannot be ruled out."}


def recount(result: dict, sharpes: list[float]) -> dict:
    """Counterfactual bookkeeping from preserved sufficient statistics; no rerun."""
    agg = result["aggregate"]
    sr, g3, g4, n_days = (agg[k] for k in ("sharpe_daily", "skew", "kurtosis_raw", "n_days"))
    n = len(sharpes)
    variance = statistics.variance(sharpes) if n > 1 else 0.0
    normal = statistics.NormalDist(); gamma = 0.5772156649
    sr0 = math.sqrt(variance) * ((1-gamma)*normal.inv_cdf(1-1/n) + gamma*normal.inv_cdf(1-1/(n*math.e))) if n > 1 and variance > 0 else 0.0
    radicand = 1-g3*sr+(g4-1)/4*sr**2
    dsr = normal.cdf((sr-sr0)*math.sqrt(n_days-1)/math.sqrt(radicand)) if radicand > 0 else 0.0
    return {"label": "counterfactual corrected bookkeeping, not historical decision inputs",
            "n_trials": n, "trial_sharpes_daily": sharpes, "variance": variance,
            "sr0": sr0, "deflated_sharpe": dsr}


def apply_inventory(root: Path, plan: dict) -> list[str]:
    require_writer(root)
    # Re-derive the plan from immutable local evidence, never trust caller-provided metrics.
    actual = inventory(root, plan["source_revision"])
    if actual != plan or plan["issues"]:
        raise RuntimeError("historical evidence is uncertain or does not match the inventory")
    kind = read_workspace(root)["kind"]
    if any(r["record_kind"] != kind for r in plan["evaluations"]):
        raise RuntimeError("legacy series contains another workspace kind; human migration decision required")
    existing = records.load_evaluations(root)
    if any(r.get("source") != "backfill" for r in existing):
        raise RuntimeError("backfill must precede new v1.1 runs; refusing historical insertion")
    expected_ids = {f"E{i:04d}" for i in range(1, len(plan["evaluations"]) + 1)}
    if any(r["evaluation_id"] not in expected_ids or r["record_kind"] != kind for r in existing):
        raise RuntimeError("unexpected legacy record outside the supported inventory")
    ids, sharpes, added = [], [], []
    for index, item in enumerate(plan["evaluations"], 1):
        eid = f"E{index:04d}"  # import-order identity, explicitly NOT an original launch ID
        ids.append(eid); sharpes.append(item["results"]["aggregate"]["sharpe_daily"])
        path = records._eval_path(root, eid)
        if path.exists():
            old = records._read_json(path)
            if old.get("source_commit") != item["source_commit"] or old.get("result_sha256") != item["result_sha256"]:
                raise RuntimeError("legacy identity collision; human decision required")
            if old["status"] == "completed-legacy":
                continue
            if old["status"] != "legacy-import-pending":
                raise RuntimeError("unexpected legacy import state")
            record = old
        else:
            record = {"evaluation_id": eid, "original_evaluation_id": None,
                      "identity_origin": "assigned-at-legacy-import", "source": "backfill",
                      "source_commit": item["source_commit"], "source_revision": plan["source_revision"],
                      "hypothesis_id": item["hypothesis_id"], "iteration": item["results"]["iteration"],
                      "params": item["results"]["params"], "record_kind": kind,
                      "run_id": item["run_path"].split("/")[-1],
                      "status": "legacy-import-pending", "reserved_at": datetime.now(timezone.utc).isoformat(),
                      "result_sha256": item["result_sha256"], "binding_status": "legacy-unverifiable-at-launch",
                      "provenance": {"log_path": item["log_path"], "log_line": item["log_line"]}}
            records._write_json(path, record)
        record.update(status="completed-legacy", results=item["results"],
                      metrics={**item["results"]["aggregate"], **item["results"]["referee_inputs"]},
                      corrected_bookkeeping={**recount(item["results"], list(sharpes)), "import_evaluation_ids": list(ids)})
        records._write_json(path, record)
        added.append(eid)
    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = inventory(ROOT, args.revision)
    if args.apply:
        with writer_lock(ROOT):
            print(json.dumps({"added": apply_inventory(ROOT, plan)}, indent=2))
    else:
        # Full result evidence stays in Git; public summary contains no market rows.
        public = {**plan, "evaluations": [{k:v for k,v in item.items() if k != "results"} for item in plan["evaluations"]]}
        print(json.dumps(public, indent=2))


if __name__ == "__main__":
    main()
