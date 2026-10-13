#!/usr/bin/env bash
# shakedown.sh — HUMAN-ONLY (R7, PROJECT_BRIEF §11).
#
# Runs exactly ONE real hypothesis cycle in the foreground with live CLIs,
# in an explicitly approved research workspace after migration review. Claude Code built this
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
# uniform launcher contract: seed a writable ~/.codex if a read-only
# staging mount exists (no-op on the host, where ~/.codex is writable)
bash "$(dirname "$0")/seed_codex_home.sh"
echo
echo "--- one live cycle (run id: ${RUN_ID}) ---"
echo "    S1 claude -> S2 codex+tests -> S3 engine -> S4 rule + secondary referee -> S5 memo"
LOOP_STATUS=0
uv run python -m orchestrator.loop --mode live --max-cycles 1 \
    --run-id "${RUN_ID}" --wall-clock-hours 1 || LOOP_STATUS=$?

# Finalize ON EVERY EXIT, success or failure: the orchestrator deliberately
# makes no run-end commit (its logger would describe that commit only AFTER
# it completes, orphaning lines in the working tree) — and even a failed
# preflight has already appended log lines. The commit is pathspec-limited
# so anything else that happens to be staged can never ride along (R5).
# LEDGER.md/PROMOTED_QUEUE.md included as the backstop for a post-append
# halt: a salvaged-but-uncommitted ledger line must never be swept into
# the next run's first commit (evaluation records supply trial history).
uv run python scripts/finalize_run.py --run-id "${RUN_ID}" || exit 70

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
echo "satisfied. SPEC v1.1 remains DRAFT; SPEC approval and historical migration"
echo "review are separate human gates before any research cutover."
