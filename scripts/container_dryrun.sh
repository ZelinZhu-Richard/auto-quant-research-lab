#!/usr/bin/env bash
# A6 acceptance script — runs INSIDE the lab container.
# 1. dry-run (2 cycles, mocked LLM) must pass against the real engine+data;
# 2. writing engine/ must fail (read-only mount);
# 3. reaching pypi.org must fail (internal network + proxy allowlist).
set -u

echo "=== [1/3] read-only mounts + R2 physical absence ==="
if touch /workspace/engine/_write_probe 2>/dev/null; then
    rm -f /workspace/engine/_write_probe
    echo "FAIL: engine/ is writable inside the container"
    exit 1
fi
echo "ok: engine/ write refused"
if touch /workspace/data/_write_probe 2>/dev/null; then
    rm -f /workspace/data/_write_probe
    echo "FAIL: data/ is writable inside the container"
    exit 1
fi
echo "ok: data/ write refused"
# R2 exact-allowlist check: /workspace/data must contain train_val + the
# two audit JSONs and NOTHING else (dotfiles included in the comparison).
ACTUAL="$(ls -A /workspace/data | sort | tr '\n' ' ')"
EXPECTED="download_report.json train_val universe.json "
if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "FAIL: data/ contents inside container: '$ACTUAL' (R2 requires exactly '$EXPECTED')"
    exit 1
fi
echo "ok: data/ contains EXACTLY train_val + audit JSONs (R2): $ACTUAL"

echo "=== [2/3] no package-registry egress ==="
if curl -sS --max-time 15 https://pypi.org/simple/ -o /dev/null 2>/dev/null; then
    echo "FAIL: pypi.org reachable from inside the container"
    exit 1
fi
echo "ok: pypi.org unreachable"

echo "=== [3/3] dry-run in-container ==="
WORK=/tmp/dryrun
rm -rf "$WORK"
mkdir -p "$WORK"
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    --exclude 'runs' --exclude 'data' /workspace/ "$WORK/"
mkdir -p "$WORK/data"
cp -r /workspace/data/train_val "$WORK/data/train_val"
cp /workspace/data/universe.json /workspace/data/download_report.json "$WORK/data/" 2>/dev/null || true
cd "$WORK"
git init -q && git add -A && git commit -qm "in-container dry-run baseline"
uv run python -m orchestrator.loop --mode dry-run --max-cycles 2 \
    --run-id container_dryrun
STATUS=$?
if [ $STATUS -ne 0 ]; then
    echo "FAIL: dry-run exited $STATUS"
    exit $STATUS
fi
echo "--- ledger ---"
grep "^#" LEDGER.md
echo "--- commits ---"
git log --oneline | head -5
echo "A6 ACCEPTANCE: all three checks passed"
