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
