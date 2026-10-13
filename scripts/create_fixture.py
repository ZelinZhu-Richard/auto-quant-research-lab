"""Create a new, disjoint fixture workspace from an explicit source allowlist."""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.loader import data_manifest_sha256

PACKAGES = ("engine", "orchestrator", "scripts", "tests", "templates")
CONFIG = ("SPEC.md", "PROJECT_BRIEF.md", "pyproject.toml", "uv.lock", "conftest.py")
AUDITS = ("universe.json", "download_report.json")


def create_fixture(source: Path, destination: Path, kind: str = "fixture") -> Path:
    source, destination = source.resolve(), destination.resolve()
    if kind not in ("fixture", "synthetic", "replay"):
        raise ValueError("fixture initializer cannot create a research workspace")
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("fixture destination must be disjoint from source")
    if destination.exists():
        raise ValueError("fixture destination must not exist")
    allowed = []
    for package in PACKAGES:
        for path in (source / package).rglob("*"):
            if path.is_symlink():
                raise ValueError(f"no source symlinks permitted: {path.relative_to(source)}")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                allowed.append(path)
    allowed += [source / name for name in CONFIG]
    data_dir = source / "data/train_val"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("approved data/train_val missing; no download is performed")
    allowed += sorted(data_dir.glob("*.parquet"))
    allowed += [source / "data" / name for name in AUDITS]
    for path in allowed:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError(f"missing or linked allowlisted file: {path}")
    manifest = data_manifest_sha256(data_dir)
    destination.mkdir(parents=True)
    for path in allowed:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if data_manifest_sha256(destination / "data/train_val") != manifest:
        raise RuntimeError("data changed while copying fixture; refusing to run it")
    for name in ("evaluations", "hypotheses"):
        (destination / name).mkdir()
    for name in ("LEDGER.md", "PROMOTED_QUEUE.md", "STATE.md"):
        (destination / name).write_text(f"# {name} — fresh {kind} workspace\n")
    (destination / "WORKSPACE.json").write_text(json.dumps({
        "kind": kind, "created": datetime.now(timezone.utc).isoformat(),
        "data_manifest_sha256": manifest,
    }, indent=2) + "\n")
    (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n.quantlab.lock\ndata/train_val/\n")
    for args in (["init", "-q"], ["config", "user.name", "Quantlab Fixture"],
                 ["config", "user.email", "fixture@invalid"],
                 ["config", "commit.gpgsign", "false"],
                 ["add", "--", *PACKAGES, *CONFIG, "data/universe.json", "data/download_report.json",
                  "WORKSPACE.json", "LEDGER.md", "PROMOTED_QUEUE.md", "STATE.md", ".gitignore"],
                 ["commit", "-qm", f"initialize isolated {kind} workspace"]):
        subprocess.run(["git", *args], cwd=destination, check=True, capture_output=True, timeout=30)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(create_fixture(ROOT, args.destination))


if __name__ == "__main__":
    main()
