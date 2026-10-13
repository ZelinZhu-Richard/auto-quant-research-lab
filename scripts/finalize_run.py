"""Commit the run tail after logging stops; preserve the launcher's loop status.

Only existing tracked/untracked paths on this allowlist may enter the commit.
A failed Git operation is never interpreted as a clean tree. No stale locks
are removed, and the same exclusive writer protocol protects finalization.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.workspace import safe_id, writer_lock

PATHS = ('runs/', 'STATE.md', 'LEDGER.md', 'PROMOTED_QUEUE.md', 'evaluations/', 'hypotheses/')


def finalize(root: Path, run_id: str) -> None:
    safe_id(run_id, r'[A-Za-z0-9][A-Za-z0-9_.-]*')
    def git(*args, check=True):
        return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True,
                              check=check, timeout=120)
    with writer_lock(root):
        if Path(git('rev-parse', '--show-toplevel').stdout.strip()).resolve() != root.resolve():
            raise RuntimeError('finalizer must run at its own repository root')
        paths = [path for path in PATHS
                 if git('ls-files', '--cached', '--others', '--exclude-standard', '--', path).stdout]
        if not paths:
            return
        git('add', '--', *paths)
        diff = git('diff', '--cached', '--quiet', '--', *paths, check=False)
        if diff.returncode == 0:
            return
        if diff.returncode != 1:
            raise RuntimeError(f'finalize diff failed ({diff.returncode}): {diff.stderr}')
        git('commit', '-m', f'run {run_id}: toolcall log finalized', '--', *paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    try:
        finalize(ROOT, args.run_id)
    except Exception as exc:
        print(f'finalization failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 70
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
