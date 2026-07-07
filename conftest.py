import sys
from pathlib import Path

# Make the repo root importable (engine/, orchestrator/) regardless of how
# pytest resolves rootdir/sys.path for the tests/ directory.
sys.path.insert(0, str(Path(__file__).parent))
