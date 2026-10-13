"""Workspace identity and exclusive writer ownership (process interruption model)."""

import contextlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("research", "fixture", "synthetic", "replay")
_OWNERS = {}


class WorkspaceError(RuntimeError):
    pass


def read_workspace(root: Path, expected_kind: str | None = None) -> dict:
    root = Path(root).resolve()
    try:
        value = json.loads((root / "WORKSPACE.json").read_text())
    except (OSError, ValueError) as exc:
        raise WorkspaceError("missing or invalid WORKSPACE.json") from exc
    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise WorkspaceError("WORKSPACE.json kind must be research/fixture/synthetic/replay")
    if expected_kind is not None and value["kind"] != expected_kind:
        raise WorkspaceError(f"workspace kind {value['kind']} != run kind {expected_kind}")
    return value


def safe_id(value: str, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise WorkspaceError(f"invalid identifier: {value!r}")
    return value


def assert_scoped_history(root: Path) -> None:
    """Fail closed on foreign records rather than leaking them into any consumer."""
    kind = read_workspace(root)["kind"]
    paths = list((root / "evaluations").glob("E*.json"))
    paths += list((root / "hypotheses").glob("H*/lifecycle.json"))
    for path in paths:
        record = json.loads(path.read_text())
        if record.get("record_kind") != kind:
            raise WorkspaceError(f"foreign record kind in {path.relative_to(root)}")


def assert_ready(root: Path, expected_kind: str) -> None:
    manifest = read_workspace(root, expected_kind)
    assert_scoped_history(root)
    if expected_kind == "research" and manifest.get("migration_ready") is not True:
        raise WorkspaceError("research cutover blocked: legacy migration requires human review")
    for path in (root / "evaluations").glob("E*.json"):
        status = json.loads(path.read_text()).get("status")
        if status in ("unresolved", "legacy-import-pending"):
            raise WorkspaceError(f"{status} evaluation {path.stem}; human reconciliation required")


@contextlib.contextmanager
def writer_lock(root: Path, expected_kind: str | None = None):
    root = Path(root).resolve()
    read_workspace(root, expected_kind)  # no writes before kind validation
    path = root / ".quantlab.lock"
    token = uuid.uuid4().hex
    payload = {"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(),
               "token": token}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WorkspaceError("workspace locked; if stale, a human must remove .quantlab.lock") from exc
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        _OWNERS[root] = payload
        yield token
    finally:
        _OWNERS.pop(root, None)
        # Only remove our own lock on normal unwind; SIGKILL leaves it for a human.
        try:
            if json.loads(path.read_text()) == payload:
                path.unlink()
        except (OSError, ValueError):
            pass


def require_writer(root: Path) -> str:
    root = Path(root).resolve()
    owner = _OWNERS.get(root)
    if owner is None or owner["pid"] != os.getpid():
        raise WorkspaceError("exclusive writer ownership required")
    if json.loads((root / ".quantlab.lock").read_text()) != owner:
        raise WorkspaceError("writer lock changed")
    return owner["token"]


def require_engine_lease(root: Path, token: str) -> None:
    """An engine child writes only under its still-live parent writer's lease."""
    owner = json.loads((root / ".quantlab.lock").read_text())
    if not token or owner.get("token") != token or owner.get("pid") != os.getppid():
        raise WorkspaceError("engine requires its parent writer's lease")
