#!/usr/bin/env bash
# shakedown.sh — HUMAN-ONLY (R7, PROJECT_BRIEF §11).
#
# Runs exactly ONE real hypothesis cycle in the foreground with live CLIs,
# in THIS repo (its commits land on the real audit spine — that is the
# point: H001 becomes the first real ledger entry). Claude Code built this
# script (A7) but must never execute it.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== quantlab shakedown: ONE live hypothesis cycle, foreground ==="
if [ -d data/holdout ]; then
    echo "FATAL: data/holdout exists inside the repo (R2). Move it out first."
    exit 2
fi

echo
echo "--- preflight: CLI versions (loop preflight re-verifies flags) ---"
claude --version
codex --version

echo
echo "--- preflight: full engine test suite ---"
uv run pytest tests/ -q

RUN_ID="shakedown_$(date -u +%Y%m%d_%H%M%S)"
echo
echo "--- one live cycle (run id: ${RUN_ID}) ---"
echo "    S1 claude -> S2 codex+tests -> S3 engine -> S4 codex referee -> S5 memo"
LOOP_STATUS=0
uv run python -m orchestrator.loop --mode live --max-cycles 1 \
    --run-id "${RUN_ID}" --wall-clock-hours 1 || LOOP_STATUS=$?

# Finalize ON EVERY EXIT, success or failure: the orchestrator deliberately
# makes no run-end commit (its logger would describe that commit only AFTER
# it completes, orphaning lines in the working tree) — and even a failed
# preflight has already appended log lines. The commit is pathspec-limited
# so anything else that happens to be staged can never ride along (R5).
git add runs/ STATE.md
if ! git diff --cached --quiet -- runs/ STATE.md; then
    git commit -m "run ${RUN_ID}: toolcall log finalized" -- runs/ STATE.md
fi

if [ "${LOOP_STATUS}" -ne 0 ]; then
    echo "orchestrator exited ${LOOP_STATUS} — see STATE.md and runs/${RUN_ID}/"
    exit "${LOOP_STATUS}"
fi

echo
echo "=== shakedown results ==="
echo "--- ledger tail ---"
tail -n 3 LEDGER.md
HID=$(ls hypotheses | sort | tail -n 1)
echo
echo "--- decision (hypotheses/${HID}/decision.json) ---"
cat "hypotheses/${HID}/decision.json" 2>/dev/null || echo "(no decision.json — check STATE.md)"
echo
echo "--- artifacts ---"
echo "hypothesis card : hypotheses/${HID}/hypothesis.md"
echo "signal          : hypotheses/${HID}/signal.py"
echo "results         : hypotheses/${HID}/results.json"
echo "memo            : hypotheses/${HID}/memo.md"
echo "toolcall log    : runs/${RUN_ID}/toolcalls.jsonl"
echo "run summary     : runs/${RUN_ID}/summary.json"
echo
echo "Next (human): read the git log and every artifact above; push if"
echo "satisfied. SPEC.md is already APPROVED and FROZEN (v1.0, 2026-07-11);"
echo "a clean shakedown completes Gate H — the overnight loop may then run."
