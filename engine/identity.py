"""Evaluated bytes, separately from Git lineage. No HEAD mismatch checks."""

import hashlib
import subprocess
from pathlib import Path

from engine.loader import data_manifest_sha256

HASH_KEYS = ("signal_sha256", "spec_sha256", "data_manifest_sha256", "engine_sha256")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_identity(root: Path) -> str:
    files = sorted((root / "engine").glob("*.py"))
    if not files:
        raise ValueError("engine package missing")
    return digest("\n".join(f"{p.name}:{digest(p.read_bytes())}" for p in files).encode())


def input_identity(root: Path, hid: str, *, signal_bytes: bytes | None = None,
                   manifest: str | None = None) -> dict:
    return {
        "signal_sha256": digest(signal_bytes if signal_bytes is not None else
                                (root / "hypotheses" / hid / "signal.py").read_bytes()),
        "spec_sha256": digest((root / "SPEC.md").read_bytes()),
        "data_manifest_sha256": manifest if manifest is not None else
                                data_manifest_sha256(root / "data/train_val"),
        "engine_sha256": engine_identity(root),
    }


def git_lineage(root: Path) -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                       text=True, timeout=10)
    return p.stdout.strip() if p.returncode == 0 else "unknown"
