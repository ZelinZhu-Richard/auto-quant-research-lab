"""Independent review round 2: deadlines must stop and reap harness children."""
import os
import sys
import time
from pathlib import Path

import pytest

from orchestrator.cli_runner import ToolCallLog, run_logged


def test_timeout_terminates_and_reaps_hanging_harness_child(tmp_path):
    child = tmp_path / 'child.py'
    child.write_text('''import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("CHILD_PID="+str(os.getpid()), flush=True)
time.sleep(60)
''')
    wrapper = tmp_path / 'wrapper.py'
    wrapper.write_text('''import signal, subprocess, sys
from engine.run_harness import terminate_harness
from engine.trials import TrialJournal
from pathlib import Path
signal.signal(signal.SIGTERM, terminate_harness)
with TrialJournal(Path(sys.argv[2])).writer() as journal:
    journal.start({"hanging": "fixture"}, "fixture", phase="harness")
    subprocess.run([sys.executable, sys.argv[1]])
''')
    started = time.monotonic()
    result = run_logged(ToolCallLog(tmp_path/'logs'),
                        [sys.executable, '-c',
                         'import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")',
                         str(wrapper), str(child), str(tmp_path)],
                        cwd=Path(__file__).resolve().parents[1], timeout_s=2, stage='timeout-regression')
    assert result.timed_out and result.exit_code == -1
    assert time.monotonic()-started < 8
    from engine.trials import TrialJournal
    assert TrialJournal(tmp_path).accounting()["pending_attempts"] == 1
    with TrialJournal(tmp_path).writer():  # killed parent released the lock
        pass
    child_pid = int(result.stdout.split('CHILD_PID=')[1].splitlines()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
