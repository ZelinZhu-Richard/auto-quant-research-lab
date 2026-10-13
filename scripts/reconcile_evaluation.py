"""Human-only reconciliation interface; never invoked by the research loop."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.workspace import writer_lock
from orchestrator.records import reconcile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation_id")
    parser.add_argument("disposition", choices=["cancelled", "completed-manual"])
    parser.add_argument("--note", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--human-authorized", action="store_true", required=True)
    parser.add_argument("--confirm-never-launched", action="store_true")
    args = parser.parse_args()
    if args.disposition == "cancelled" and not args.confirm_never_launched:
        parser.error("cancellation requires explicit human confirmation that no evaluation launched")
    if args.disposition == "completed-manual" and not args.evidence:
        parser.error("completed-manual requires supporting results JSON")
    evidence = json.loads(args.evidence.read_text()) if args.evidence else None
    with writer_lock(ROOT):
        reconcile(ROOT, args.evaluation_id, args.disposition, args.note, evidence, human_authorized=True)


if __name__ == "__main__":
    main()
