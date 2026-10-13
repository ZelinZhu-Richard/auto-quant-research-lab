# Historical migration status — human review required

SPEC_STATUS: DRAFT
RESEARCH_CUTOVER: HOLD

The captured local-main baseline is
`059e0952a367f5e0b4ded60cafd7fd027fa835c5`, including the 21 local
commits beyond the older proposal base. Every pre-existing hypothesis,
run log, summary, ledger, queue and state file is preserved unchanged.
No source research records are modified by this candidate.

## Supported committed history

| Hypotheses | Evidence | Disposition |
|---|---|---|
| H001 | Three distinct committed S3 results; each matches the successful engine command and timestamp interval in the shakedown toolcall log; its summary declares live mode | Three additive `completed-legacy` research records, E0001–E0003 |
| H002–H004 | Recorded S1 failures, live-mode run summary and no recorded S3 calls or results | Preserve outcomes; no evaluation fabricated |
| H005–H007 | Recorded S2 failures, live-mode run summary and no recorded S3 calls or results | Preserve outcomes; no evaluation fabricated |

The inventory scans every committed hypothesis results-file change, current
artifacts and S3 toolcall entries. Provenance comes from command, timestamps
and the recorded run mode, not a run or hypothesis name. No supported fixture
or uncertain completed evaluation appears in this captured history. The
software refuses automatic import when a result, call, outcome or workspace
kind is ambiguous. A synthetic unfinished-history test demonstrates that gate.

This inventory covers captured committed evidence. It cannot establish that
an unlogged or uncommitted pre-v1.1 attempt never happened, nor can it recreate
input-binding evidence absent at launch. Those limitations require a human
cutover decision even though the known three completed evaluations are covered.
The next hypothesis identity is H008 and the next evaluation identity E0004;
old ledger counters are not reset. Legacy daily Sharpes are eligible prior
trials only after a human approves migration readiness.

## Import semantics and review

The importer is additive and refuses insertion after any new v1.1 evaluation.
It re-derives provenance from an explicit local revision before any writes.
IDs are assigned at import and explicitly carry no original evaluation ID.
The source commit and canonical result hash are recorded before completion.
`completed-legacy` preserves the full original result, including its original
N and DSR. Counterfactual corrected bookkeeping is stored in a separate field
and explained in `hypotheses/H001/CORRECTION_v1.1.md`. Repeating the same import
is a no-op; partial imports remain visible until that same evidence is accepted.
`TRIALS.md` displays recorded DSR, never recalculating it from later history.

`WORKSPACE.json` deliberately retains `migration_ready: false`. Human review
must assess inventory completeness and any undocumented attempts before changing
that flag in an approved integration. Software tests do not approve research
migration, SPEC, merge, publication or a research run.

## Human-only recovery actions

A stale `.quantlab.lock` is never cleared by software. A human must verify the
writer is stopped before removing it. Do not treat age or an unavailable
process listing as proof. The next loop recovery, under an exclusive lock,
will cancel known prelaunch reservations, preserve acceptable results, or mark
possible launches unresolved. Unresolved records prevent another run.

For a human-authorized reconciliation in the intended workspace only:

```text
python scripts/reconcile_evaluation.py E#### completed-manual --human-authorized --note "evidence and decision" --evidence /path/to/preserved-result.json
python scripts/reconcile_evaluation.py E#### cancelled --human-authorized --confirm-never-launched --note "evidence that execution never began"
```

These are documented interfaces, not authorization to execute them during
this repair. Completion requires matching IDs, input hashes, tuple and finite
result evidence; cancellation requires a human finding that launch never occurred.
Neither recovery nor an agent should guess an ambiguous disposition.
