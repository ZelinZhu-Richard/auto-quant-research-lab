#!/usr/bin/env bash
# New external fixture only; never run the dry-run loop in a research checkout.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "usage: bash scripts/demo.sh /absolute/path/to/new-fixture" >&2
  exit 2
fi
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${QUANTLAB_PYTHON:-python3}"
"$PYTHON" "$SOURCE/scripts/create_fixture.py" --destination "$1"
cd "$1"
status=0
"$PYTHON" -m orchestrator.loop --mode dry-run --run-id demo --max-cycles 2 || status=$?
"$PYTHON" scripts/finalize_run.py --run-id demo || exit 70
exit "$status"
