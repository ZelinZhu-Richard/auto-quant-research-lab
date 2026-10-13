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
# uniform launcher contract (no-op here: the dryrun service mounts no
# credential seed — the acceptance run needs none)
bash /workspace/scripts/seed_codex_home.sh || { echo "FAIL: seed script errored"; exit 1; }
# Create an absent destination under a session temp directory. The shared
# allowlist initializer starts fresh history and a new fixture Git repository.
WORK_PARENT="$(mktemp -d /tmp/quantlab-dryrun.XXXXXX)" || exit 1
WORK="$WORK_PARENT/workspace"
QUANTLAB_PYTHON="${UV_PROJECT_ENVIRONMENT:-/home/labuser/venv}/bin/python" bash /workspace/scripts/demo.sh "$WORK"
STATUS=$?
cd "$WORK" || exit 1

if [ $STATUS -ne 0 ]; then
    echo "FAIL: dry-run exited $STATUS"
    exit $STATUS
fi
if ! PORCELAIN="$(git status --porcelain)"; then
    echo "FAIL: git status failed after finalize"
    exit 1
fi
if [ -n "$PORCELAIN" ]; then
    echo "FAIL: working tree dirty after finalize:"
    echo "$PORCELAIN"
    exit 1
fi
echo "ok: working tree clean after launcher finalize"
echo "--- ledger ---"
grep "^#" LEDGER.md
echo "--- commits ---"
git log --oneline | head -5
echo "A6 ACCEPTANCE: all three checks passed"
