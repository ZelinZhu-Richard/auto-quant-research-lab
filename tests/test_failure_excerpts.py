"""Failure-excerpt regressions from minirun_20260712 at PRODUCTION limits:
fatal errors must survive every truncation slot (tail excerpts, squeezed
STATE/commit lines), claude's stdout-JSON error reporting must be parsed
(its stderr is empty on failure), and non-object payloads must fail as
StageFailure, not AttributeError."""

import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.cli_runner import CliResult
from orchestrator.loop import _squeeze
from orchestrator.stages import _claude_failure_detail, _output_tail

FATAL = ("Error: failed to initialize in-process app-server client: "
         "Read-only file system (os error 30)")
# realistic long output: retry noise ahead of the fatal line, > any slot
CODEX_LONG_OUTPUT = (
    "WARNING: proceeding, even though we could not create PATH aliases: "
    "Read-only file system (os error 30)\n"
    + "ERROR: Reconnecting... attempt with backoff\n" * 12
    + FATAL + "\n"
)


def _result(stdout="", stderr="", exit_code=1) -> CliResult:
    return CliResult(command=["x"], exit_code=exit_code, stdout=stdout,
                     stderr=stderr, duration_s=1.0, timed_out=False)


def test_production_tail_keeps_fatal_error_head_slice_does_not():
    assert len(CODEX_LONG_OUTPUT) > 300
    detail = _output_tail(_result(stderr=CODEX_LONG_OUTPUT))  # default 300
    assert FATAL in detail
    assert FATAL not in CODEX_LONG_OUTPUT[:300]  # the pre-fix behavior


def test_tail_combines_stdout_and_stderr():
    detail = _output_tail(_result(stdout="on stdout\n", stderr="fatal on stderr"))
    assert "fatal on stderr" in detail and "on stdout" in detail


def test_squeeze_keeps_stage_prefix_and_fatal_tail():
    message = "S2: codex exited 1: " + _output_tail(_result(stderr=CODEX_LONG_OUTPUT))
    for limit in (200, 160, 60):
        squeezed = _squeeze(message, limit)
        assert len(squeezed) <= limit
        assert squeezed.startswith("S2: codex ex"[: max(10, limit // 4)])
        assert squeezed.endswith(FATAL[-(limit - max(10, limit // 4) - 1):])


def test_squeeze_short_text_untouched():
    assert _squeeze("short error", 60) == "short error"


def test_claude_error_parsed_from_stdout_json():
    # the actual H002-H004 failure shape: JSON on stdout, EMPTY stderr
    payload = json.dumps({"type": "result", "is_error": True,
                          "result": "Invalid API key · Please run /login"})
    detail = _claude_failure_detail(_result(stdout=payload, stderr=""))
    assert detail == "Invalid API key · Please run /login"


def test_claude_detail_never_empty_when_output_exists():
    detail = _claude_failure_detail(_result(stdout="plain crash text", stderr=""))
    assert detail == "plain crash text"


def test_claude_detail_non_dict_json_on_nonzero_exit_no_crash():
    """Gate arbitration finding 1 (recurrence): valid non-object JSON on a
    NONZERO exit must fall through to the tail — list, string, and number
    payloads all previously reached .get() and raised AttributeError."""
    for stdout in ("[]", '"just a string"', "123"):
        detail = _claude_failure_detail(
            _result(stdout=stdout, stderr="real error on stderr", exit_code=1))
        assert "real error on stderr" in detail  # tail fallback, no crash


def test_claude_text_nonzero_exit_with_list_json_is_stagefailure():
    """End-to-end through claude_text: the nonzero-exit path must raise
    StageFailure (which run_cycle infra-kills), never AttributeError."""
    from orchestrator.stages import LiveLlm, LlmUsage, StageFailure, StageTimeouts
    from unittest import mock

    usage = LlmUsage()
    llm = LiveLlm(log=mock.Mock(), repo_root=Path("."),
                  timeouts=StageTimeouts(), usage=usage)
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stdout="[]", stderr="boom", exit_code=1)):
        with pytest.raises(StageFailure, match="boom"):
            llm.claude_text("p", "S1", 10)
    assert usage.calls == 1  # failed call still counted


def test_claude_non_object_json_is_stagefailure_not_crash():
    """`[]` parses as JSON but is not a dict — must become StageFailure
    inside claude_text, never AttributeError (which would crash the run;
    run_cycle additionally infra-kills any unhandled exception per R4)."""
    from orchestrator.stages import LiveLlm, LlmUsage, StageFailure, StageTimeouts
    from unittest import mock

    usage = LlmUsage()
    llm = LiveLlm(log=mock.Mock(), repo_root=Path("."),
                  timeouts=StageTimeouts(), usage=usage)
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stdout="[]", exit_code=0)):
        with pytest.raises(StageFailure, match="not a JSON object"):
            llm.claude_text("p", "S1", 10)


def test_failed_claude_call_still_bills_the_ceiling():
    """SPEC §12: cost accounting must include FAILED calls — the payload
    reports total_cost_usd even when is_error is true or exit is 1."""
    from orchestrator.stages import LiveLlm, LlmUsage, StageFailure, StageTimeouts
    from unittest import mock

    usage = LlmUsage()
    llm = LiveLlm(log=mock.Mock(), repo_root=Path("."),
                  timeouts=StageTimeouts(), usage=usage)
    payload = json.dumps({"is_error": True, "result": "budget exceeded",
                          "total_cost_usd": 1.75})
    with mock.patch("orchestrator.stages.run_logged",
                    return_value=_result(stdout=payload, exit_code=1)):
        with pytest.raises(StageFailure):
            llm.claude_text("p", "S1", 10)
    assert usage.claude_cost_usd == 1.75
    assert usage.calls == 1


SEED_SCRIPT = Path(__file__).parent.parent / "scripts" / "seed_codex_home.sh"


def _run_seed(home: Path, seed: Path | None, force: bool = False):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if seed is not None:
        env["CODEX_SEED_DIR"] = str(seed)
    if force:
        env["CODEX_SEED_FORCE"] = "1"
    return subprocess.run(["bash", str(SEED_SCRIPT)], env=env,
                          capture_output=True, text=True)


def test_seed_noop_without_seed_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_seed(home, tmp_path / "absent")
    assert proc.returncode == 0
    assert not (home / ".codex").exists()


def test_seed_fails_closed_without_auth_json(tmp_path):
    """A seed dir with no auth.json must exit 1 BEFORE any S1 spend."""
    home, seed = tmp_path / "home", tmp_path / "seed"
    home.mkdir(), seed.mkdir()
    (seed / "config.toml").write_text("model = 'x'\n")
    proc = _run_seed(home, seed)
    assert proc.returncode == 1
    assert "refusing" in proc.stderr


def test_seed_copies_auth_only_with_600(tmp_path):
    home, seed = tmp_path / "home", tmp_path / "seed"
    home.mkdir(), seed.mkdir()
    (seed / "auth.json").write_text('{"tokens": "t"}')
    (seed / "config.toml").write_text("model = 'personal'\n")
    proc = _run_seed(home, seed)
    assert proc.returncode == 0
    assert (home / ".codex" / "auth.json").read_text() == '{"tokens": "t"}'
    assert not (home / ".codex" / "config.toml").exists()  # R6: auth ONLY
    assert oct((home / ".codex" / "auth.json").stat().st_mode & 0o777) == "0o600"


def test_seed_prefers_persisted_state_unless_forced(tmp_path):
    """OAuth refresh rotates tokens: the persisted volume's auth.json is
    fresher than the host seed and must not be clobbered by default."""
    home, seed = tmp_path / "home", tmp_path / "seed"
    (home / ".codex").mkdir(parents=True), seed.mkdir()
    (home / ".codex" / "auth.json").write_text("refreshed-in-container")
    (seed / "auth.json").write_text("stale-host-copy")
    proc = _run_seed(home, seed)
    assert proc.returncode == 0
    assert (home / ".codex" / "auth.json").read_text() == "refreshed-in-container"
    proc = _run_seed(home, seed, force=True)
    assert proc.returncode == 0
    assert (home / ".codex" / "auth.json").read_text() == "stale-host-copy"
