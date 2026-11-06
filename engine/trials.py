"""Append-only trial evidence. No inference of independent tests from counts.

The orchestrator/engine is the writer; LLM subprocesses remain read-only.
Git anchors detect edits/deletions of committed events. Hashes are integrity
checks, not signatures against a compromised host or malicious grader.
"""

import contextlib
import fcntl
import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from engine.errors import EngineError


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_loads(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EngineError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise EngineError(f"nonfinite JSON: {value}")

    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, OSError) as exc:
        raise EngineError("invalid evidence JSON") from exc


def read_json(path: Path):
    return strict_loads(path.read_bytes())


def immutable_write(path: Path, value) -> None:
    """Publish complete bytes without overwrite; fsync before atomic hard link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value) + b"\n"
    if path.exists():
        if path.read_bytes() != payload:
            raise EngineError(f"immutable evidence conflict: {path.name}")
        return
    temp = path.parent / (".pending-" + uuid.uuid4().hex)
    try:
        with temp.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)  # fails if another publisher won
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


class TrialJournal:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / "trial_evidence"

    @contextlib.contextmanager
    def writer(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".writer.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EngineError("another evaluation owns the trial journal") from exc
            try:
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def events(self) -> list[dict]:
        events, previous, attempts = [], None, {}
        for index, path in enumerate(
            sorted((self.directory / "events").glob("*.json")), 1
        ):
            event = read_json(path)
            if path.name != f"{index:08d}.json" or event.get("previous") != previous:
                raise EngineError("trial journal discontinuity")
            claimed = event.get("sha256")
            payload = {k: v for k, v in event.items() if k != "sha256"}
            if claimed != digest(payload):
                raise EngineError("trial journal hash mismatch")
            attempt = event.get("attempt_id")
            if event.get("kind") == "started":
                if attempt in attempts or event.get("trial_id") != digest(
                    event["identity"]
                ):
                    raise EngineError("invalid trial start")
                attempts[attempt] = event
            elif event.get("kind") in ("scored", "failed"):
                start = attempts.get(attempt)
                if (
                    not start
                    or start.get("terminal")
                    or start["trial_id"] != event.get("trial_id")
                ):
                    raise EngineError("invalid trial terminal event")
                start["terminal"] = True
                if event["kind"] == "scored" and (
                    type(event.get("score_daily")) not in (float, int)
                    or not math.isfinite(event["score_daily"])
                ):
                    raise EngineError("nonfinite trial score")
            else:
                raise EngineError("invalid trial event kind")
            # Do not mutate hashed event records with bookkeeping.
            events.append(read_json(path))
            previous = claimed
        return events

    def append(self, kind: str, **fields) -> dict:
        events = self.events()
        payload = {
            "kind": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "previous": events[-1]["sha256"] if events else None,
            **fields,
        }
        event = {**payload, "sha256": digest(payload)}
        immutable_write(
            self.directory / "events" / f"{len(events) + 1:08d}.json", event
        )
        self.events()  # validate transition before returning authorization material
        return event

    def start(self, identity: dict, git_commit: str) -> dict:
        return self.append(
            "started",
            attempt_id=uuid.uuid4().hex,
            trial_id=digest(identity),
            identity=identity,
            git_commit=git_commit,
        )

    def finish(self, start: dict, artifact: dict) -> dict:
        trial_id = start["trial_id"]
        immutable_write(self.directory / "scores" / f"{trial_id}.json", artifact)
        return self.append(
            "scored",
            attempt_id=start["attempt_id"],
            trial_id=trial_id,
            score_daily=artifact["aggregate"]["sharpe_daily"],
            artifact_sha256=digest(artifact),
        )

    def fail(self, start: dict, error: str) -> dict:
        return self.append(
            "failed",
            attempt_id=start["attempt_id"],
            trial_id=start["trial_id"],
            error=error,
        )

    def accounting(self, through: str | None = None) -> dict:
        events = self.events()
        if through is not None:
            matches = [i for i, e in enumerate(events) if e["sha256"] == through]
            if len(matches) != 1:
                raise EngineError("unknown trial snapshot")
            events = events[: matches[0] + 1]
        starts = {e["attempt_id"]: e for e in events if e["kind"] == "started"}
        terminal = {e["attempt_id"] for e in events if e["kind"] != "started"}
        scored = {}
        for event in events:
            if event["kind"] == "scored":
                old = scored.get(event["trial_id"])
                if old and (old["artifact_sha256"], old["score_daily"]) != (
                    event["artifact_sha256"],
                    event["score_daily"],
                ):
                    raise EngineError("non-deterministic exact replay")
                scored[event["trial_id"]] = event
        legacy_path = self.directory / "legacy_inventory.json"
        legacy = read_json(legacy_path) if legacy_path.exists() else {"trials": []}
        from engine.ledger import ledger_entry_count

        if ledger_entry_count(self.root / "LEDGER.md") and not legacy_path.exists():
            raise EngineError(
                "legacy history requires an audited inventory before v2 evaluation"
            )
        if legacy_path.exists():
            ledger = (self.root / "LEDGER.md").read_bytes()
            prefix = ledger[: legacy["ledger_prefix_bytes"]]
            if hashlib.sha256(prefix).hexdigest() != legacy["ledger_prefix_sha256"]:
                raise EngineError("historical ledger prefix changed")
        prior = [
            t["score_daily"] for t in legacy["trials"] if t["score_daily"] is not None
        ]
        return {
            "attempted": len(starts) + len(legacy["trials"]),
            "successfully_scored_attempts": sum(e["kind"] == "scored" for e in events)
            + len(prior),
            "unique_scored": len(scored) + len(prior),
            "n_trials": len({e["trial_id"] for e in starts.values()})
            + len(legacy["trials"]),
            "effective_independent_trials": None,
            "pending_attempts": len(starts.keys() - terminal),
            "trial_sharpes_daily": prior + [e["score_daily"] for e in scored.values()],
            "method": "all-configurations-dsr-variance-floor-v2",
        }
