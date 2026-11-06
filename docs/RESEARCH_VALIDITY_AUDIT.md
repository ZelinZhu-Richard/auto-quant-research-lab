# Research validity audit — 2026-11-05

This is an engineering correction, not a market discovery. SPEC v1.0 was
insufficient: its trial-count and execution semantics cannot substantiate the
scientific claims previously attached to them. SPEC_v2 is a proposed amendment
requiring human approval before live runs. No holdout or private sealed benchmark
was opened, copied, scored, or used. No model was upgraded.

## Findings and behavioral changes

| Issue / root cause | Correction and principal files |
|---|---|
| A: schema/model checks accepted financially false decisions | `engine/policy.py` owns decisions. `orchestrator/stages.py` checks all binding fields; `loop.py` and `state.py` authenticate evidence again before terminal logging/queueing. S4 no longer asks an LLM for authorization; S5 can explain the outcome. Malformed evidence is an infrastructure failure, distinct from a merit rejection. |
| A: results and lineage were assertions rather than verified inputs | `engine/evidence.py` checks Git-bound source/card/score/journal hashes, immutable registration, iteration order, current dataset hash and metric reconstruction from daily evidence. S3 reruns the existing adversarial signal harness. |
| B: one LEDGER line per hypothesis concealed iterations | `engine/trials.py` records attempts before execution, immutable score artifacts and a hash chain. Exact replay adds an attempt, not another configuration. Failed scoring counts; interrupted attempts block authorization until reconciled. `run_backtest.py` uses full-precision trial scores, never rounded LEDGER numbers. Historical parser in `engine/ledger.py` remains a compatibility utility, not the new correction source. |
| B: zero empirical variance disabled deflation | v2 uses an explicitly labeled iid-null variance floor in `metrics.py`. All configurations count as a penalty proxy; effective independent trials remain unknown. This is not a dependence/adaptive-selection calibration. |
| C: knowing close(t) and filling there was called an execution lag | `backtest.py` now defaults to observation at t, modeled close fill at t+1, return through t+2. The old method remains explicitly `historical_close_v1`, inadmissible for v2 authorization. |
| C: previous targets replaced actual drifted holdings | `portfolio.py` solves post-fee NAV sizing, charges 25bps on actual buys/sells, and conserves signed holdings plus cash. Includes bankruptcy checks. |
| C: `fillna(0)` hid missing marks/free delisting exits | v2 aborts for missing/invalid held or selected marks, even on a proposed liquidation. Zero-volume fills abort. No invented recovery values. |
| C: drawdown omitted initial wealth | v2 includes initial NAV=1 as a peak; the old function behavior is available with `historical=True`. |
| D: current-active exchange market pool was described as point-in-time | `data_pipeline.py` labels survivorship and records ranking availability after its window for future datasets. `engine/universe.py` supports supplied timestamped membership evidence. No historical listing dataset is claimed or supplied. |
| D: weak bar/symbol validation | `loader.py` validates finite OHLCV, positive prices, OHLC ranges, nonnegative volume, expected symbols and no symlinked market files. Gaps stay gaps; trading code decides whether they invalidate a held position. |
| E: no synthetic scientific end-to-end checks | `scripts/synthetic_validation.py` and tests cover a planted positive, pure noise, selection of a noise winner, and a future-return oracle. Counts and reasons are in `synthetic_validation.json`. |
| Verification: interrupted IDs/evidence could be reused or omitted from finalize | IDs reserve existing hypothesis directories; launchers include `trial_evidence/` in finalization. Dry-run clones retain Git provenance. Existing Docker/network isolation, read-only engine, quota halt and Git review gates remain. |

## Original versus retrospective H001

Recovered original results from commits `7528fb9`, `e3e8a32`, `cf20ab7` (90, 60,
120 days). Their daily Sharpes are -0.07314299849486205,
-0.07190604045993973, -0.04525104057410526. The final original result reports
N=1 and DSR **0.07815576975893723**.

Using all three full-precision scores with the **unchanged v1 DSR formula**:
sample variance 0.0002483300667912103; expected maximum daily Sharpe
0.013438912740225422; retrospective DSR **0.03298780269897206**. An independent
`statistics.NormalDist` calculation agrees. The reported approximation 0.0330
is reproduced, not assumed. This is an accounting sensitivity on original v1
returns, not an execution-corrected backtest. The three settings are correlated;
N=3 is not proof of independent testing. H001 remains KILL on merits.

Reproduce without market data:

```sh
PYTHONPATH=. python scripts/audit_h001.py
PYTHONPATH=. python scripts/synthetic_validation.py
```

Original `hypotheses/H001/*`, `SPEC.md`, LEDGER, prior runs and committed data
metadata are byte-identical to local baseline `059e095`; see
`historical_preservation.json`. Existing train_val was copied only from its
explicit development directory; its manifest matches H001:
`885599371d82fa70ed4f1faf4b9fba6d8857aca4cbb53b7ccebc7a1ad90b1a3b`.
No existing dataset was regenerated. The new fixtures are exact copies of the
three Git result blobs, with original hashes in `trial_evidence/legacy_inventory.json`.

The branch preserves 21 local safety/history commits above public `b494a06`
without rewriting them. The full PR therefore includes those pre-existing
commits as well as this audit's changes; review the entire range.

## Verification record

- Baseline: **96 passed, 2 skipped** (training data absent in initial checkout).
- Current full suite: **157 passed**, zero skipped after copying the explicitly
  allowlisted train_val files. Command: `python -m pytest tests/ -q`.
- Original H001 signal harness: **4 passed**, using only train_val; no H001
  backtest or historical score overwrite. Command:
  `QUANTLAB_HYPOTHESIS=H001 python -m pytest tests/hypothesis_harness.py -q`.
- Ruff `check --select F engine orchestrator scripts tests data_pipeline.py`:
  passed. No project type-checker is configured. Compileall, shell syntax,
  Compose configuration validation and `git diff --check`: passed.
- Toy suite: **1 planted positive promoted, 0 false promotions, 3 rejected**.
  These four cases provide regression coverage only, not population control.
- Integration tests run actual engine -> Git commits -> referee -> queue on
  synthetic data. They reject forged metrics/criteria/registration, stale or
  deleted receipts, changed code/data/card, missing marks, and unauthorized
  queue calls; verify exact replay and failed-attempt accounting.
- Container acceptance on final implementation `1fa701f`: read-only engine/data
  checks and blocked pypi.org egress passed; two full mocked cycles completed,
  both KILL on merits, with a clean final Git tree. Existing locally built
  images were used (no dependency or model upgrade). Independent review gate
  returned PASS in round 3; see `reviews/round3.md`.

## Scientific limits and next-run requirements

The [DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) uses
independent trials and estimated trial-score dispersion. Our configuration
count and variance floor are transparent screening approximations, not a
proof of familywise error control. Adaptive generation, correlated returns,
optional stopping, repeated data reuse and unknown external searches remain.
Historical pre-S3 infrastructure kills have no numeric score and are disclosed
separately; unlogged attempts cannot be recovered from Git.

The [Coinbase candle contract](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)
warns of incomplete history and absent candles for intervals with no ticks.
Missing bars cannot establish a free liquidation price. Exact daily closes do
not establish liquidity, borrow, capacity or execution. Financing, borrow fees,
short availability, interest, market impact and short rebates remain zero-cost
or unconstrained research assumptions. Positive-volume stale vendor marks may
be undetectable from OHLCV alone. The current-active candidate pool remains
survivor-biased, and no reliable historical membership source was reconstructed.

Hashes protect integrity against accidental/agent edits within the existing
read-only model workflow, not a compromised host or a hostile administrator
rewriting both Git and the grader. Python purity guards are not a complete
sandbox against intentionally malicious generated Python; retain the container
boundary, physical data exclusion and independent reviews.

Before another large run: approve SPEC_v2 in a committed `protocol_approval.json`
with `protocol: quantlab-v2`, `approved_by_human: true` and the exact
`spec_sha256`; review historical trial coverage; pre-register a search family
and budget; reconcile every pending attempt; supply validated historical
membership and executable-cost/borrow assumptions if making investability
claims; independently calibrate selection/dependence using public or synthetic
data; rebuild/verify isolation and perform the human-supervised shakedown.
No approval file is fabricated by this change.

For a crash, preserve its started event and artifacts. A human must inspect the
attempt and append a matching failed reconciliation event (or reconstruct the
exact scored receipt with evidence), then commit before resumption. Never edit,
delete or silently complete a pending event. A retry with identical identity
gets a new attempt ID and the same statistical slot.


## Independent review round 1

The restricted read-only Codex reviewer returned FAIL with three new findings:
(1) S3 reused the full-panel harness's signal state; (2) the harness could mutate
its caller's panel; (3) S2/repair/iteration harnesses executed before durable
trial registration. All were accepted. Fixes reset signal module state for
every scoring observation, isolate/copy harness inputs and reject mutation,
and register every harness invocation through `engine/run_harness.py` with
immutable tested source snapshots. New regressions reproduce cached future
rankings, idempotent price mutation, and inspect the start event BEFORE the
harness child is invoked. Failed checks retain their statistical configuration
slot; successful checks do not fabricate a numeric score.

The first reviewer could inspect source but its restricted runtime could not
launch Git's developer-tools wrapper. It therefore did not claim a complete
branch-diff review. The rerun grants read access to the installed Git toolchain
(still no market data or other project access) to complete that verification.


## Independent review round 2

The reviewer verified all round-1 fixes and the unchanged historical artifacts,
then reported one NEW finding: the stage timeout killed only the immediate
harness wrapper, leaving pytest potentially running. `cli_runner.py` now gives
each stage a process group, sends TERM then KILL to the group, and reaps the
parent. `run_harness.py` handles TERM with a BaseException so Python's subprocess
context kills/reaps pytest without fabricating a terminal trial result. The
regression launches a TERM-ignoring child, verifies it is gone after the deadline,
and verifies the durable unresolved attempt and released writer lock. No prior
finding recurred.


## Final verification and disposition

Round 3 returned **PASS**, with no NEW or RECURRENT findings, after reviewing
`b494a06..1fa701f`. The reviewer verified the complete candidate and historical
preservation; it did not rerun tests and does not claim otherwise. All three
verdicts are retained in `reviews/`. Code is at `1fa701f`; subsequent changes
only record verification documentation.

Final checks: **157/157 default tests**, **4/4 separate H001 harness checks**,
Ruff F, compileall, shell syntax, Compose validation and whitespace checks all
passed. Final Docker acceptance also passed **3/3 checks** and completed two
mocked full cycles (both merit kills, zero infrastructure kills), including
registered harness attempts, immutable trial artifacts, per-stage commits and
a clean finalized tree. The credential-free acceptance used existing local
images. Its log is `container-acceptance-final.txt`; test summaries and
`verification.json` record the exact candidate and archive checksum. The
container evidence archive remains in the session's `workspace/` audit folder,
separate from the original experiments. No live LLM research or human-only
shakedown/holdout run was performed.

Review and deployment are separate: this PR must not be merged automatically,
and the human adoption/calibration/shakedown requirements above still apply.
