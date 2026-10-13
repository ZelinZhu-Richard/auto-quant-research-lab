"""Disposable test subprocess; parent SIGKILLs only after a pipe handshake."""

import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.workspace import writer_lock, require_writer
from orchestrator import records, state
from orchestrator.loop import Loop
from orchestrator.stages import run_backtest
from tests.v11_support import BLOCKS, fake_result, start_hypothesis


def main():
    root, cut, ready = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    def pause(point):
        if point == cut:
            os.write(ready, point.encode() + b"\n")
            while True:
                signal.pause()
    with writer_lock(root):
        loop = Loop(root, "dry-run", "interrupted", 1, 1, 25, 1)
        hid = start_hypothesis(root)
        eid = records.reserve_evaluation(root, hid, 0, BLOCKS["params"], "test", "fixture")
        pause("before_launch")
        records.mark_launched(root, eid)
        pause("after_launch")
        blocks = dict(BLOCKS)
        if cut == "scored_terminal":
            blocks["grid"] = []
            child = run_backtest(loop.log, root, hid, 60, evaluation_id=eid,
                                 record_kind="fixture", writer_token=require_writer(root))
            assert child.exit_code == 0, child.stderr
            result = records._read_json(root / "hypotheses" / hid / "results.json")
        else:
            result = fake_result(root, eid, passing=cut != "after_iterate_commit")
        records._write_json(root / "hypotheses" / hid / "results.json", result)
        pause("after_result")
        records.complete_evaluation(root, eid, result)
        pause("after_completion")
        original_update = records.update_lifecycle
        def update(*args, **kw):
            if args[2] == "decided": pause("after_decision_before_lifecycle")
            return original_update(*args, **kw)
        records.update_lifecycle = update
        original_commit = loop._commit
        def commit(message):
            original_commit(message)
            if "/S4:" in message:
                pause("after_s4_commit")
                pause("after_iterate_commit")
                pause("scored_terminal")
        loop._commit = commit
        loop._s4_referee(hid, "card", blocks, result, canned=True)
        original_ledger, original_queue = state.append_ledger, state.append_promoted
        def ledger(*args, **kw):
            result = original_ledger(*args, **kw)
            pause("after_ledger_before_queue")
            return result
        def queue(*args, **kw):
            result = original_queue(*args, **kw)
            pause("after_queue_before_lifecycle")
            return result
        state.append_ledger, state.append_promoted = ledger, queue
        records.finalize_terminal(root, hid)
        raise AssertionError("cut point was not reached")


if __name__ == "__main__":
    main()
