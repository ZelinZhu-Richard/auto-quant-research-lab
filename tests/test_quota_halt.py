"""Quota exhaustion is RUN-HALTING, not a per-hypothesis infra-kill
(directive 2026-08-17): the quota pool is shared with unrelated workloads,
so exhaustion can arrive mid-run from outside this system — without the
halt, an external drain burns every remaining cycle as identical kills.
Quota-shaped failure text from either CLI must become QuotaExhausted =>
QuotaHalt, with a distinct reason in STATE.md and summary.json."""

import json
from pathlib import Path
from unittest import mock

import pytest

from orchestrator.cli_runner import CliResult
from orchestrator.loop import HardStop, Loop, QuotaHalt
from orchestrator.stages import (
    QUOTA_ERROR_RE,
    LiveLlm,
    LlmUsage,
    QuotaExhausted,
    StageFailure,
    StageTimeouts,
)


def _result(stdout="", stderr="", exit_code=1) -> CliResult:
    return CliResult(command=["x"], exit_code=exit_code, stdout=stdout,
                     stderr=stderr, duration_s=1.0, timed_out=False)


def _llm() -> tuple[LiveLlm, LlmUsage]:
    usage = LlmUsage()
    return LiveLlm(log=mock.Mock(), repo_root=Path("."),
                   timeouts=StageTimeouts(), usage=usage), usage


def _bare_loop(tmp_path) -> Loop:
    return Loop(repo_root=tmp_path, mode="dry-run", run_id="t",
                max_hypotheses=1, wall_clock_hours=1.0,
                cost_ceiling_usd=1.0, max_cycles=1)


# ---------- classifier ----------

@pytest.mark.parametrize("tail", [
    "HTTP 429 Too Many Requests",
    "server responded with status 429",
    "You've hit your usage limit. Upgrade to Pro or try again later.",
    "usage_limit_reached",
    "quota exceeded for this billing period",
    "Rate limit reached for requests",
    "rate_limit_error: try again in 60s",
])
def test_quota_shaped_text_matches(tail):
    assert QUOTA_ERROR_RE.search(tail)


@pytest.mark.parametrize("tail", [
    "Error: failed to initialize: Read-only file system (os error 30)",
    "sharpe=0.4290 dsr=nan — backtest wrote no results",
    "tokens used: 1429",  # 429 inside a larger number must not trip
    "exit status 1: model produced malformed JSON",
])
def test_non_quota_text_does_not_match(tail):
    assert not QUOTA_ERROR_RE.search(tail)


# ---------- CLI wrappers route quota-shaped failures ----------

def test_claude_nonzero_exit_with_quota_tail_raises_quota_exhausted():
    llm, usage = _llm()
    payload = json.dumps({"type": "result", "is_error": True,
                          "result": "You've hit your usage limit."})
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stdout=payload, exit_code=1)):
        with pytest.raises(QuotaExhausted, match="usage limit"):
            llm.claude_text("p", "S1", 10)
    assert usage.calls == 1  # a quota-failed call is still a call


def test_claude_zero_exit_is_error_quota_payload_raises_quota_exhausted():
    llm, _ = _llm()
    payload = json.dumps({"is_error": True,
                          "result": "429 rate_limit_error"})
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stdout=payload, exit_code=0)):
        with pytest.raises(QuotaExhausted):
            llm.claude_text("p", "S1", 10)


def test_codex_nonzero_exit_with_quota_tail_raises_quota_exhausted():
    llm, usage = _llm()
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(
                        stderr="ERROR: quota exceeded, upgrade your plan",
                        exit_code=1)):
        with pytest.raises(QuotaExhausted, match="quota"):
            llm.codex_text("p", "S2", 10, model="m", effort="none")
    assert usage.calls == 1


def test_non_quota_cli_failure_still_stage_failure():
    """Regression guard: ordinary failures keep the R4 infra-kill path."""
    llm, _ = _llm()
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stderr="Read-only file system",
                                         exit_code=1)):
        with pytest.raises(StageFailure, match="Read-only"):
            llm.codex_text("p", "S2", 10, model="m")


# ---------- loop converts to a distinct halt ----------

def test_run_cycle_converts_quota_exhausted_to_quota_halt(tmp_path):
    loop = _bare_loop(tmp_path)
    with mock.patch.object(
            loop, "_s1_hypothesize",
            side_effect=QuotaExhausted("S1: claude exited 1: usage limit")), \
         mock.patch.object(loop, "_infra_kill") as infra:
        with pytest.raises(QuotaHalt, match="QUOTA EXHAUSTED at H001"):
            loop.run_cycle()
    infra.assert_not_called()  # never an R4 kill, never a ledger line
    assert not (tmp_path / "LEDGER.md").exists()


def test_quota_halt_is_a_hard_stop():
    """QuotaHalt must ride the existing clean-halt path (SPEC §12):
    run_cycle's `except HardStop: raise` and run()'s handler both catch it."""
    assert issubclass(QuotaHalt, HardStop)


def test_quota_halt_distinct_in_summary_and_state(tmp_path):
    loop = _bare_loop(tmp_path)
    with mock.patch.object(loop, "preflight"), \
         mock.patch.object(loop, "run_cycle",
                           side_effect=QuotaHalt(
                               "QUOTA EXHAUSTED at H001: S2: codex exited "
                               "1: usage limit")):
        assert loop.run() == 0
    summary = json.loads((tmp_path / "runs" / "t" / "summary.json").read_text())
    assert summary["halt_class"] == "quota-exhausted"
    assert "QUOTA EXHAUSTED" in summary["halt_reason"]
    state_text = (tmp_path / "STATE.md").read_text()
    assert "halted (quota-exhausted)" in state_text
    assert "QUOTA EXHAUSTED" in state_text


def test_ordinary_hard_stop_keeps_plain_halt_class(tmp_path):
    loop = _bare_loop(tmp_path)
    with mock.patch.object(loop, "preflight"), \
         mock.patch.object(loop, "run_cycle",
                           side_effect=HardStop("wall-clock cap reached")):
        assert loop.run() == 0
    summary = json.loads((tmp_path / "runs" / "t" / "summary.json").read_text())
    assert summary["halt_class"] == "hard-stop"
    state_text = (tmp_path / "STATE.md").read_text()
    assert "halted (quota-exhausted)" not in state_text
