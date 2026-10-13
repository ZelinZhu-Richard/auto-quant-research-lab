import os
import shutil
import subprocess
import sys

import pytest

from engine.workspace import WorkspaceError, writer_lock
from scripts.finalize_run import finalize
from tests.v11_support import git_init, init_workspace


def test_finalizer_lock_refusal_preserves_stale_lock_and_records(tmp_path):
    root = init_workspace(tmp_path)
    git_init(root)
    (root / '.quantlab.lock').write_text('{"pid":999999,"started":"fixture"}')
    before = (root / '.quantlab.lock').read_bytes()
    with pytest.raises(WorkspaceError, match='human'):
        finalize(root, 'refused')
    assert (root / '.quantlab.lock').read_bytes() == before


def test_finalizer_git_error_cannot_be_mistaken_for_clean_tree(tmp_path):
    root = init_workspace(tmp_path / 'workspace', full=True)
    git_init(root)
    (root / 'STATE.md').write_text('uncommitted final state\n')
    bindir = tmp_path / 'bin'; bindir.mkdir()
    stub = bindir / 'git'
    import shlex
    stub.write_text('#!/bin/sh\nif [ "$1" = "diff" ]; then exit 128; fi\nexec ' +
                    shlex.quote(shutil.which('git')) + ' "$@"\n')
    stub.chmod(0o700)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'])
    before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                            capture_output=True, text=True).stdout
    p = subprocess.run([sys.executable, 'scripts/finalize_run.py', '--run-id', 'failure'],
                       cwd=root, env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 70 and 'diff failed (128)' in p.stderr
    after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                           capture_output=True, text=True).stdout
    assert before == after and (root / 'STATE.md').read_text() == 'uncommitted final state\n'
    assert not (root / '.quantlab.lock').exists()
