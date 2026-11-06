"""Instrumented subprocess runner (PROJECT_BRIEF §7).

Every CLI invocation the orchestrator makes goes through run_logged() and is
appended to runs/<run_id>/toolcalls.jsonl: command, cwd, duration, exit code.
This log is a first-class deliverable (Scutum raw material).
"""

import contextlib
import os
import signal
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CliResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


class ToolCallLog:
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "toolcalls.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def run_logged(
    log: ToolCallLog,
    command: list[str],
    cwd: Path,
    timeout_s: int,
    stage: str,
    stdin_text: str | None = None,
) -> CliResult:
    started = time.time()
    timed_out = False
    # Each stage gets a process group: uv/wrapper/pytest descendants inherit it.
    # subprocess.run's default timeout only kills the immediate process and
    # can even hang collecting pipes still held by orphaned descendants.
    with subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout_s)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
            finally:
                # A descendant may have closed its pipes and ignored TERM;
                # kill the remaining group even if communicate already returned.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            exit_code = -1
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
    duration = time.time() - started
    log.append(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "stage": stage,
            "command": command,
            "cwd": str(cwd),
            "duration_s": round(duration, 3),
            "exit_code": exit_code,
            "timed_out": timed_out,
        }
    )
    return CliResult(command, exit_code, stdout, stderr, duration, timed_out)
