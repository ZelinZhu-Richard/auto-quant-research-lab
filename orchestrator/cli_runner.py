"""Instrumented subprocess runner (PROJECT_BRIEF §7).

Every CLI invocation the orchestrator makes goes through run_logged() and is
appended to runs/<run_id>/toolcalls.jsonl: command, cwd, duration, exit code.
This log is a first-class deliverable (Scutum raw material).
"""

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
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = -1
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timed_out = True
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
