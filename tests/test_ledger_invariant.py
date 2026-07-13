"""Gate arbitration finding 4: one ledger line per hypothesis is an
INVARIANT — the line count is the N feeding the deflated Sharpe. A failure
AFTER the line is appended must halt the run, never append a second line."""

from unittest import mock

import pytest

from orchestrator.loop import HardStop, Loop


def _bare_loop(tmp_path) -> Loop:
    return Loop(repo_root=tmp_path, mode="dry-run", run_id="t",
                max_hypotheses=1, wall_clock_hours=1.0,
                cost_ceiling_usd=1.0, max_cycles=1)


def test_post_ledger_failure_halts_without_second_append(tmp_path):
    loop = _bare_loop(tmp_path)
    loop._ledger_written = True  # _close_cycle already appended
    with mock.patch.object(loop, "_infra_kill") as infra:
        with pytest.raises(HardStop, match="one-line-per-hypothesis"):
            loop._handle_cycle_failure(1, "H001", "x", "commit exploded", True)
    infra.assert_not_called()


def test_pre_ledger_failure_still_infra_kills(tmp_path):
    loop = _bare_loop(tmp_path)
    loop._ledger_written = False
    with mock.patch.object(loop, "_infra_kill") as infra:
        loop._handle_cycle_failure(1, "H001", "x", "S2 died", False)
    infra.assert_called_once_with(1, "H001", "x", "S2 died", False)


def test_flag_set_before_append_in_close_cycle(tmp_path):
    """Round-3 finding 2: if append_ledger dies mid-flush a partial line
    may exist — the flag must already be True so failure handling halts
    instead of appending a second line."""
    loop = _bare_loop(tmp_path)
    loop._ledger_written = False
    decision = {"decision": "KILL", "kill_reason": "merits"}
    results = {"aggregate": {}, "referee_inputs": {}}
    with mock.patch("orchestrator.loop.state.append_ledger",
                    side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            loop._close_cycle(1, "H001", "x", decision, results, True, 0)
    assert loop._ledger_written is True


def test_flag_set_before_append_in_infra_kill(tmp_path):
    loop = _bare_loop(tmp_path)
    loop._ledger_written = False
    with mock.patch("orchestrator.loop.state.append_ledger",
                    side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            loop._infra_kill(1, "H001", "x", "boom", False)
    assert loop._ledger_written is True


def test_infra_kill_handler_failure_escalates_to_hardstop(tmp_path):
    """If the failure handler itself dies (e.g. its commit), the run must
    halt cleanly with salvage — never crash out of run() unhandled."""
    loop = _bare_loop(tmp_path)
    loop._ledger_written = False
    with mock.patch.object(loop, "_infra_kill",
                           side_effect=RuntimeError("git index locked")), \
         mock.patch.object(loop, "_commit") as salvage:
        with pytest.raises(HardStop, match="infra-kill handler itself failed"):
            loop._handle_cycle_failure(1, "H001", "x", "S3 died", False)
    salvage.assert_called_once()


def test_post_ledger_halt_attempts_salvage_commit(tmp_path):
    """Round-3 finding 1: the post-append halt must try to commit the
    ledger line so it cannot be swept into the next run's first commit."""
    loop = _bare_loop(tmp_path)
    loop._ledger_written = True
    with mock.patch.object(loop, "_commit") as salvage:
        with pytest.raises(HardStop, match="one-line-per-hypothesis"):
            loop._handle_cycle_failure(1, "H001", "x", "commit exploded", True)
    salvage.assert_called_once()
    # and a salvage failure must not mask the halt
    loop2 = _bare_loop(tmp_path)
    loop2._ledger_written = True
    with mock.patch.object(loop2, "_commit", side_effect=RuntimeError("git dead")):
        with pytest.raises(HardStop, match="one-line-per-hypothesis"):
            loop2._handle_cycle_failure(1, "H001", "x", "commit exploded", True)
