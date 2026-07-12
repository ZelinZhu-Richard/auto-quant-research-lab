"""Failure-excerpt regressions from minirun_20260712: fatal errors must
survive truncation (tail, not head) and claude's stdout-JSON error
reporting must be parsed (its stderr is empty on failure)."""

import json

from orchestrator.cli_runner import CliResult
from orchestrator.stages import _claude_failure_detail, _output_tail

# the actual H005 failure shape: benign warning first, fatal error last
CODEX_RO_HOME = (
    "WARNING: proceeding, even though we could not create PATH aliases: "
    "Read-only file system (os error 30)\n"
    "Error: failed to initialize in-process app-server client: "
    "Read-only file system (os error 30)\n"
)


def _result(stdout="", stderr="", exit_code=1) -> CliResult:
    return CliResult(command=["x"], exit_code=exit_code, stdout=stdout,
                     stderr=stderr, duration_s=1.0, timed_out=False)


def test_tail_keeps_the_fatal_error():
    detail = _output_tail(_result(stderr=CODEX_RO_HOME), limit=120)
    assert "failed to initialize in-process app-server client" in detail
    # and a head-slice of the same budget would NOT have kept it
    assert "failed to initialize" not in CODEX_RO_HOME[:120]


def test_tail_combines_stdout_and_stderr():
    detail = _output_tail(_result(stdout="on stdout\n", stderr="fatal on stderr"))
    assert "fatal on stderr" in detail and "on stdout" in detail


def test_claude_error_parsed_from_stdout_json():
    # the actual H002-H004 failure shape: JSON on stdout, empty stderr
    payload = json.dumps({"type": "result", "is_error": True,
                          "result": "Invalid API key · Please run /login"})
    detail = _claude_failure_detail(_result(stdout=payload, stderr=""))
    assert detail == "Invalid API key · Please run /login"


def test_claude_detail_never_empty_when_output_exists():
    # non-JSON stdout, empty stderr: falls back to the combined tail
    detail = _claude_failure_detail(_result(stdout="plain crash text", stderr=""))
    assert detail == "plain crash text"


def test_claude_detail_tail_bounded():
    payload = json.dumps({"result": "x" * 1000})
    detail = _claude_failure_detail(_result(stdout=payload), limit=100)
    assert len(detail) == 100
