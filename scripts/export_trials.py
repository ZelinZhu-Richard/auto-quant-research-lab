"""Read-only historical view by default; --write regenerates the derived TRIALS.md."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.workspace import read_workspace, writer_lock
from orchestrator import records


def render(root: Path) -> str:
    kind = read_workspace(root)["kind"]
    lines = ["# TRIALS.md — derived evaluation-record export",
             "", f"Workspace kind: {kind}. Legacy imports are labeled; original launch binding is unverified.",
             "", "| ID | Hypothesis | Iteration | Kind | Status | Source | DSR as recorded |",
             "|---|---|---|---|---|---|---|"]
    for r in records.load_evaluations(root):
        if r["record_kind"] != kind:
            continue
        dsr = r.get("results", {}).get("referee_inputs", {}).get("deflated_sharpe")
        lines.append(f"| {r['evaluation_id']} | {r['hypothesis_id']} | {r['iteration']} | "
                     f"{r['record_kind']} | {r['status']} | {r.get('source', 'run')} | {dsr} |")
        if r.get("reason"):
            lines.append(f"\n{r['evaluation_id']}: {r['reason']}\n")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        with writer_lock(ROOT):
            records.atomic_text(ROOT / "TRIALS.md", render(ROOT))
        return 0
    text = render(ROOT)
    if args.check:
        return int(not (ROOT / "TRIALS.md").exists() or (ROOT / "TRIALS.md").read_text() != text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
