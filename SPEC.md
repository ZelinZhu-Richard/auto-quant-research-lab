# SPEC.md — Research Specification
#
# STATUS: v1.1 — DRAFT. Not approved for research cutover.
# The v1.0 scientific contracts, cost model, criteria and threshold are
# unchanged. This draft repairs bookkeeping, identity and recovery.
# Human approval and a new run series are required before research use.

## 1. Data contract

- Source: `data/train_val/{SYMBOL}.parquet`, 50 symbols (see
  data/universe.json). This is the ONLY data the system may read (R2).
- Per file: pandas DataFrame, DatetimeIndex `date` of dtype
  `datetime64[ms, UTC]` (millisecond resolution — do not assume ns), daily
  UTC-midnight bars, columns `open, high, low, close, volume` all float64.
  `volume` is in base-currency units; dollar volume = `close * volume`.
- Sample: 2022-07-01 through 2025-06-30 inclusive (1096 bars per symbol,
  gapless in this dataset; the loader must tolerate gaps and late listings
  for generality).
- Data lineage: the engine computes `sha256` of every parquet file at load
  and exposes a manifest hash (sha256 of the sorted `filename:filehash`
  lines). results.json and memo.md record it.

## 2. Signal contract (the one shared interface)

```python
def compute_signal(panel: pd.DataFrame) -> pd.Series
```

- `panel`: MultiIndex `(date, symbol)` DataFrame, lexsorted, columns
  `open, high, low, close, volume`, containing ONLY rows with
  `date <= evaluation date` (the engine slices before calling — this is
  enforced by construction, not by trust).
- Returns: `pd.Series` indexed by `(date, symbol)`, float; higher = more
  attractive. `NaN` = no opinion; that asset is excluded from that date's
  ranking. Only the LAST date's cross-section is used by the engine per
  call during walk-forward evaluation; returning history is permitted.
- Purity: no I/O of any kind (no file, network, environment access), no
  randomness unless seeded with a fixed literal, no state between calls,
  no mutation of `panel`.
- Purity enforcement boundary (normative): purity is enforced by four
  layers — (i) Python-level guards on the common read entry points
  (open/io/os/pathlib/pandas/pyarrow/numpy/socket) AND on environment
  access — os.environ/os.getenv/os.environb raise inside guarded regions
  except for a frozen allowlist of library-internal keys
  (PYARROW_IGNORE_TIMEZONE, PANDAS_COPY_ON_WRITE) that pandas itself
  consults at call time, served from a snapshot taken at guard entry;
  (ii) kernel-level RLIMIT_NOFILE=0 inside every guarded
  region, so ANY new file-descriptor allocation fails regardless of API
  (covers io.open_code, pyarrow C++ filesystems, ctypes, os.listdir);
  (iii) the harness truncate-and-compare check; (iv) container read-only
  mounts + egress allowlist + physical absence of holdout (R2). Both the
  S2 harness and the S3 engine loop run signal code (including its module
  import) inside layers (i)+(ii). ACCEPTED RESIDUAL: fd-less metadata
  syscalls (os.stat — cannot read prices) and a signal deliberately
  raising its own rlimit; the threat model is careless generated code,
  and layer (iv) is the adversarial backstop. A reviewer verifying purity
  verifies THIS boundary, not the unattainable absolute.
- Maximum lookback: 120 calendar days (declared in hypothesis.md; the
  first scored date has 123 days of history available).

## 3. Portfolio construction (fixed in the engine; agents cannot change it)

Terminology used throughout this SPEC: a **signal date** `t` is a date on
which weights are formed from close(t) and all prior data; a **return day**
`t+1` is the day those weights earn the close(t) → close(t+1) return. All
evaluation windows in Section 5 are stated in RETURN DAYS. For a window of
return days `[d_start, d_end]`, signal dates run `[d_start - 1 day,
d_end - 1 day]`; the engine never needs a close after `d_end`, so the last
train_val bar (2025-06-30) is the last return day and NO data beyond
train_val is ever referenced.

For each signal date `t`:

1. Valid set = symbols with non-NaN signal at `t` AND a close at `t`.
   Let `n` = |valid set|, `q = floor(n / 5)`.
2. If `q < 3`: no positions formed at `t` (degenerate cross-section; the
   corresponding return day gets portfolio return from zero weights, i.e.
   0 minus any turnover cost, and is excluded from hit-rate).
3. Deterministic ordering: sort valid symbols by
   `(signal value DESC, symbol name ASC)`. Top `q` are LONG, bottom `q`
   SHORT. Ties broken by the symbol-name sort — no randomness.
4. Weights: each long `+0.5/q`, each short `-0.5/q`. Gross exposure 1.0,
   net exposure 0.0 (dollar-neutral), equal weight within each leg.
5. EXECUTION LAG (the engine's core no-lookahead guarantee, not
   configurable): weights `w(t)` are computed from data up to and including
   close(t) and earn the close(t) → close(t+1) simple return, credited to
   return day t+1:
   `r_gross(t+1) = Σ_i w_i(t) * (close_i(t+1) / close_i(t) - 1)`.
   If a symbol has no close at t+1 (mid-sample delisting/gap), its return
   that day is 0 (position assumed flat-exited at last close; conservative
   and deterministic). By the window convention above this rule never
   fires at the end of train_val — the boundary case cannot arise.

## 4. Costs (fixed in the engine)

- 25 bps per side, charged on turnover, every rebalance (daily). Not
  optional, not a parameter agents may touch.
- `turnover(t) = Σ_i |w_i(t) - w_i(t-1)|` with `w(before first date) = 0`
  (so the initial build costs `0.0025 * 1.0`). Buys and sells each appear
  in the sum, which is what "per side" means here.
- `r_net(t+1) = r_gross(t+1) - 0.0025 * turnover(t)`.

## 5. Walk-forward protocol

- Burn-in (history available to signals, never scored):
  2022-07-01 → 2022-10-31.
- Four sequential evaluation folds over train_val, stated in RETURN DAYS,
  dates inclusive (per Section 3, each fold's signal dates start one day
  earlier — F1's first signal date is 2022-10-31, inside burn-in):
  - F1: 2022-11-01 → 2023-06-30
  - F2: 2023-07-01 → 2024-02-29
  - F3: 2024-03-01 → 2024-10-31
  - F4: 2024-11-01 → 2025-06-30
- Aggregate window (return days): 2022-11-01 → 2025-06-30. Its last signal
  date is 2025-06-29; no close beyond train_val is ever needed.
- The information set expands continuously: at each date `t` the signal
  sees all data from 2022-07-01 through `t`. NOTE (stated assumption):
  signals here have no fitted parameters — all parameters are
  pre-registered in the hypothesis's iteration grid (R3) — so "expanding-
  window walk-forward" means sequential evaluation sub-periods over an
  expanding information set, not model refitting.
- Per-fold metrics are computed on each fold's daily net returns;
  aggregate metrics on the full window's daily net returns.

## 6. Metrics (exact definitions; engine/metrics.py implements these)

Let `r_1..r_T` be daily NET portfolio returns over a window (calendar-day
series as produced by Section 3; days with no positions contribute r=0 and
are excluded from hit rate only).

- `sharpe_annualized = mean(r) / std(r, ddof=1) * sqrt(365)`; 0 if
  `std == 0`. Risk-free rate = 0. Crypto trades every calendar day, hence
  365 not 252.
- `max_drawdown`: with equity curve `E_t = Π (1 + r_s)`,
  `max_drawdown = max(1 - E_t / max_{s<=t} E_s)` — reported as a POSITIVE
  fraction (0.25 = -25% peak-to-trough).
- `hit_rate = #(r > 0) / #(days with positions)`.
- `turnover_annualized = mean(daily turnover) * 365` (gross multiples per
  year; 25x means the gross book turns 25 times a year).
- `skew`, `kurtosis`: with central moments `m_k = mean((r - mean(r))^k)`,
  `skew g3 = m3 / m2^1.5` and RAW kurtosis `g4 = m4 / m2^2` — the
  population-moment (uncorrected) estimators, exactly as used in Bailey &
  López de Prado's DSR derivation. No Fisher bias correction; non-excess
  convention (normal => g4 = 3). These feed the DSR.
- Determinism: all metrics are pure functions of the return series;
  identical inputs give bit-identical outputs.

## 7. Deflated Sharpe Ratio (Bailey & López de Prado)

Purpose: account for completed evaluations in the workspace. Parameter
variants remain correlated; this repair does not calibrate error control.

Inputs: daily net returns of the aggregate window (length `T`, per-period
sharpe `sr = mean(r)/std(r, ddof=1)`, skewness `g3`, raw kurtosis `g4`),
and the trial count `N`.

- **G1 (trial inputs).** For evaluation E in a workspace, E's recorded statistical inputs are:
  the eligible prior completed evaluations of that workspace's kind, plus E exactly once when
  E produces a valid result. The record preserves the contributing evaluation identities (or
  an equivalent verifiable reference to that historical set). Once recorded, E's `n_trials`,
  trial set, and computed DSR never change because later evaluations complete. Recovery and
  exports never add E again after completion and never recalculate an old decision with later
  history.

- At reservation, freeze the ordered prior completed evaluation IDs,
  daily Sharpes and full-result hashes of the workspace's kind. Eligible
  states are `completed`, `completed-manual` and labeled `completed-legacy`.
  `N` is this set's size plus the current evaluation exactly once.
- `V` is sample variance (`ddof=1`) of the preserved prior daily Sharpes
  plus the current daily Sharpe. Each completed parameter variant counts;
  cancelled, unresolved and pre-backtest infrastructure failures do not.
  With fewer than two Sharpes or zero variance, `SR0 = 0`; N alone then
  does not change DSR. Do not infer a monotonically rising bar or calibrated
  familywise error control from this bookkeeping.
- Historical imports preserve the original results, including their old
  trial inputs. An explicitly labeled counterfactual recount is separate;
  it never overwrites an old result or decision or claims launch binding.
- Expected max trial Sharpe under N trials:
  `SR0 = sqrt(V) * ((1 - g) * z(1 - 1/N) + g * z(1 - 1/(N*e)))`
  where `g = 0.5772156649` (Euler–Mascheroni), `z` = standard normal
  quantile (inverse CDF), `e` = Euler's number.
- `DSR = Phi( ((sr - SR0) * sqrt(T - 1)) / sqrt(1 - g3*sr + ((g4 - 1)/4) * sr^2) )`
  where `Phi` = standard normal CDF. If the denominator radicand is <= 0,
  DSR = 0.0 (pathological tails; fail-safe toward killing).
- Implementation: `engine/metrics.py::deflated_sharpe(returns, n_trials,
  trial_sharpes)` — plain math (no scipy; use `statistics.NormalDist`).

## 8. Kill criteria — template and approved v1.0 defaults

Every hypothesis.md MUST pre-register concrete numbers for all four, before
any data is touched (R3). Defaults below are the approved v1.0 values; a
hypothesis may tune them only WITH WRITTEN JUSTIFICATION in hypothesis.md,
and never after S1.

| criterion | approved v1.0 default | judged on |
|---|---|---|
| min annualized Sharpe (after costs) | >= 1.0 | aggregate window |
| max drawdown | <= 0.30 | aggregate window |
| min hit rate | >= 0.50 | aggregate window |
| stability | `(fold_sharpe > 0) == (aggregate_sharpe > 0)` for >= 3 of 4 folds | folds |

Hit-rate floor rationale (amended 0.52 → 0.50 at Gate H approval,
2026-07-11, per the approving human): the floor is a SANITY BOUND, not an
edge requirement. A dollar-neutral long-short book with positively skewed
payoffs — large winners, small losers — can clear every other criterion
below a 52% hit rate, so 0.52 would systematically bias against
skewed-payoff hypotheses. 0.50 excludes only worse-than-coin-flip
consistency; the edge requirement lives in the Sharpe and DSR criteria.

Sign convention (deterministic, incl. exact zero): a Sharpe is "positive"
iff strictly `> 0`; zero counts as non-positive. A fold is sign-consistent
iff `(fold_sharpe > 0) == (aggregate_sharpe > 0)`. A flat (all-zero) result
is thus sign-consistent everywhere but fails the min-Sharpe criterion —
fail-safe toward killing.

Plus one SPEC-global criterion (not tunable per hypothesis):

| criterion | approved v1.0 default |
|---|---|
| deflated Sharpe ratio | >= 0.95 to PROMOTE |

Decision rule (referee MUST follow exactly; zero discretion — two referees
given the same inputs MUST emit the same decision; the ONLY judgment
inputs are the pre-registered criteria, the DSR, and the iteration
bookkeeping mandated by PROJECT_BRIEF Section 4, per R3):
1. ALL four pre-registered criteria pass AND DSR >= 0.95 → PROMOTE.
2. Else, if `results.json:iteration == 2`, OR the declared grid (Section
   8b) has no entry absent from `results.json:iteration_history` →
   KILL(merits).
3. Else → ITERATE, with `iterate_params` = the FIRST grid entry (in the
   exact order declared in hypothesis.md) whose params do not appear in
   `iteration_history` (no referee choice).
- Referee never proposes fixes, new features, or new ideas (R3, Section 4
  S4). Its justification must cite the pre-registered numbers and observed
  values.

## 8b. Machine-readable pre-registration blocks (make refereeing mechanical)

hypothesis.md MUST contain these two fenced JSON blocks, verbatim headers:

Under `## Parameters (iteration 0)`:

```json
{"lookback_days": 90}
```

Under `## Iteration grid (pre-registered, ordered, max 2)`:

```json
[{"lookback_days": 60}, {"lookback_days": 120}]
```

- The grid is an ORDERED list of 0, 1, or 2 param dicts, each with the
  same keys as iteration 0. Empty list = no iterations permitted.
- Under `## Kill criteria (pre-registered)` a third block:

```json
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.50,
 "min_sign_consistent_folds": 3}
```

- S1 writes these blocks once; S2/S4 parse them; nothing rewrites them
  after S1 (R3). The orchestrator validates their presence and shape
  before S2 starts; malformed blocks → infra-kill.

## 9. results.json schema (written by S3; engine output is the only source)

```json
{
  "hypothesis_id": "H008",
  "evaluation_id": "E0004",
  "record_kind": "research",
  "inputs": {
    "signal_sha256": "<sha256 of executed signal bytes>",
    "spec_sha256": "<sha256 of SPEC bytes>",
    "data_manifest_sha256": "<sha256 of sorted parquet file hashes>",
    "engine_sha256": "<sha256 of engine/*.py contents>"
  },
  "iteration": 0,
  "params": {"lookback_days": 90},
  "iteration_history": [{"iteration": 0, "params": {"lookback_days": 90}}],
  "generated_at": "2026-07-07T12:00:00+00:00",
  "engine": {"git_commit": "<repo HEAD at run>", "data_manifest_sha256": "<hex>"},
  "cost_bps_per_side": 25,
  "aggregate": {
    "start": "2022-11-01", "end": "2025-06-30",
    "n_days": 973, "n_position_days": 970,
    "sharpe_annualized": 0.0, "sharpe_daily": 0.0,
    "max_drawdown": 0.0, "hit_rate": 0.0,
    "turnover_annualized": 0.0,
    "mean_daily_return": 0.0, "std_daily_return": 0.0,
    "skew": 0.0, "kurtosis_raw": 3.0
  },
  "folds": [
    {"fold": 1, "start": "2022-11-01", "end": "2023-06-30",
     "n_days": 0, "sharpe_annualized": 0.0, "max_drawdown": 0.0,
     "hit_rate": 0.0}
  ],
  "referee_inputs": {
    "n_trials": 4,
    "prior_evaluation_ids": ["E0001", "E0002", "E0003"],
    "prior_result_hashes": ["<sha256>", "<sha256>", "<sha256>"],
    "trial_evaluation_ids": ["E0001", "E0002", "E0003", "E0004"],
    "trial_sharpes_daily": [-0.047, -0.070, -0.045, 0.0],
    "deflated_sharpe": 0.0
  },
  "status": "ok"
}
```

All floats full precision (no rounding in the file). `folds` always has
exactly 4 entries. `status` is `"ok"` or `"error: <one line>"` (an error
status after a possible S3 launch leaves the evaluation unresolved and
halts; it is never evidence that no evaluation occurred). Atomic results
are bound to their reservation by ID, tuple and all four hashes. Git HEAD
is lineage only: a later bookkeeping commit does not invalidate inputs.

## 10. decision.json schema

`orchestrator/rule.py` is the sole merits authority, in every mode.
The orchestrator atomically writes its validated output before invoking
any optional secondary referee. It binds `evaluation_id`, `inputs` and
`rule_version: "rule:spec-v1.1"` to an immutable completed evaluation.
The lifecycle then enters `decided` with the same ID and decision. The
separate `referee_llm.json` records agreement, disagreement, invalid output
or unavailability; it cannot change or erase the rule decision.

Before a bound decision, explicit rule input errors and ordinary stage
failures may produce an orchestrator `KILL(infrastructure)`, with empty
criteria and no merits judgment. Quota and budget stops halt the run.
After a bound terminal decision, the verdict stands through all later
failures. `_infra_kill` refuses to overwrite any bound rule decision.

```json
{
  "hypothesis_id": "H001",
  "iteration": 0,
  "decision": "KILL",
  "kill_reason": "merits",
  "criteria": [
    {"name": "min_sharpe", "required": ">= 1.0", "observed": 0.42, "pass": false},
    {"name": "max_drawdown", "required": "<= 0.30", "observed": 0.18, "pass": true},
    {"name": "min_hit_rate", "required": ">= 0.50", "observed": 0.51, "pass": true},
    {"name": "stability_3_of_4", "required": "3/4 folds sign-consistent", "observed": "2/4", "pass": false},
    {"name": "deflated_sharpe", "required": ">= 0.95", "observed": 0.31, "pass": false}
  ],
  "iterate_params": null,
  "justification": "One paragraph citing the pre-registered numbers above.",
  "referee_model": "rule:spec-v1.1",
  "rule_version": "rule:spec-v1.1",
  "evaluation_id": "E0004",
  "inputs": {"signal_sha256": "<hex>", "spec_sha256": "<hex>",
             "data_manifest_sha256": "<hex>", "engine_sha256": "<hex>"},
  "timestamp": "2026-07-07T12:00:00+00:00"
}
```

- `decision` ∈ {"KILL", "ITERATE", "PROMOTE"} (exactly one).
- `kill_reason` ∈ {"merits", "infrastructure", null} — "infrastructure"
  only ever via the orchestrator path above.
- `iterate_params`: the exact pre-registered grid point to run next, or
  null. Must equal one of the grid points declared in hypothesis.md.

## 11. LEDGER.md line format (append-only; one line per hypothesis)

```
#N | name | DECISION(reason) | sharpe=X dsr=Y dd=Z hit=W | tests=pass|fail
```

- `#N`: durable 1-based hypothesis identity, not a count of successful backtests.
  A reserved identity is never reused, even if its cycle does not finish.
- `DECISION(reason)`: `KILLED(merits)`, `KILLED(infrastructure)`,
  `PROMOTED`, `ITERATED->KILLED(...)`, `ABANDONED(interrupted)`,
  `ABANDONED(interrupted-after-iterate)` or `ABANDONED(unbound-decision)` — final state only, one line
  per hypothesis regardless of iterations.
- `sharpe` = aggregate annualized net Sharpe (4 decimals), `dsr` = deflated
  Sharpe (4 decimals), `dd` = max drawdown fraction (4 decimals), `hit` =
  hit rate (4 decimals). For infra-kills where no backtest ran, all four
  are `nan`.
- `tests=pass` iff the S2 test suite passed.

## 11b. Durable records, workspace boundary and recovery

- `WORKSPACE.json` declares one of `research`, `fixture`, `synthetic` or
  `replay`. Loop and engine reject a kind mismatch before any file write.
  All consumers use that workspace: trial inputs, ledger prompt context,
  identities, promotion queue, statistics, lifecycle and Git history.
  Foreign-kind records fail admission. Research additionally requires
  explicit human migration review (`migration_ready: true`); the candidate
  ships with this flag false. Never set it automatically.
- A workspace has one writer. `.quantlab.lock` is exclusively created
  with PID, start time and ownership token before recovery or mutations.
  The engine child requires its parent's lease. A second writer is refused.
  A stale lock remains after SIGKILL; only a human may clear it after
  verifying the previous writer is stopped. There is no automatic clearing.
- Hypothesis IDs are reserved in `hypotheses/H###/lifecycle.json` before
  S1. Allocation includes existing directories and ledger IDs, including
  pre-v1.1 history. Terminal lifecycle states: `closed`, `infra-killed`,
  `abandoned`. IDs are never recycled.
- Evaluation IDs are reserved in `evaluations/E####.json` before S3,
  recording hypothesis, iteration, params, run, kind, time, evaluated-input
  hashes, Git lineage and frozen prior trials. The persisted `launched`
  boundary precedes the subprocess and means it MAY have started.
- Recovery cancels a still-`reserved` evaluation as known never launched.
  For `launched`, only a valid, matching, finite `status: ok` result permits
  completion; the full result and its canonical SHA256 are preserved.
  Missing, truncated, foreign-ID, tuple or hash mismatch evidence becomes
  `unresolved`, visible in TRIALS and statistics, and blocks further work.
  Completed records are immutable; repeated completion only accepts the
  identical full result. Recovery never launches another evaluation.
- Human reconciliation only: `scripts/reconcile_evaluation.py` requires
  explicit authorization and a note. `completed-manual` requires matching
  result evidence; `cancelled` requires confirmation that launch never
  happened. Neither the loop nor recovery invokes this command. Legacy
  imports use separate `completed-legacy` records, import-assigned IDs and
  evidence-backed provenance; they do not invent original launch IDs.
- A decision written before its lifecycle update remains authoritative
  when bound to a completed evaluation and matching hashes. Otherwise it
  is preserved as `decision.unbound.json` and the hypothesis is abandoned.
  Terminal finalization reads that exact evaluation (never the latest one),
  writes a missing recovery memo, appends the ledger, appends the promotion
  queue if needed, then closes the lifecycle. Ledger number, queue ID and
  terminal lifecycle checks make repeating those steps idempotent.
- S5 failures retain the decision and produce `memo_status: failed`,
  counted in `summary.json:memo_failures`. Recovery memos are marked
  `finalized-by-recovery`. An interrupted ITERATE is preserved as an
  instruction, ends as abandoned and is never automatically resumed;
  completed evaluations remain eligible trials.
- **Supported failure model:** process interruption, including SIGKILL,
  and restart. Writes use temporary files, fsync and atomic replacement.
  This version makes no guarantee against arbitrary disk corruption or
  power loss during a write; it has not tested directory-fsync durability.

## 12. Hard stops for the overnight loop (enforced by orchestrator/loop.py)

| stop | approved v1.0 value |
|---|---|
| max hypotheses per run | 30 |
| wall-clock cap | 6 hours (checked between stages; loop exits cleanly) |
| consecutive infrastructure kills | 3 → halt + diagnosis written to STATE.md |
| max iterations per hypothesis | 2 (also enforced by referee protocol) |
| per-call claude budget | `--max-budget-usd 2.00` |
| per-run LLM cost ceiling | $25.00 summed over claude-reported `total_cost_usd`; codex usage reported only when present in CLI stdout/stderr; missing usage is null |
| per-stage subprocess timeout | S1 600s, S2 1200s, S3 900s, S4 600s, S5 600s |

On a handled hard stop: record the reason in STATE.md and summary.json,
exit 0; the finalizing launcher commits the tail and records with a limited
pathspec. A refused workspace admission exits nonzero without writes.
Quota exhaustion has its own halt class and never triggers another model
call. A terminal rule decision remains final if quota, cost or wall-clock
limits trip afterward; finalize it without further model work. An ITERATE
instruction remains nonterminal and is abandoned on interrupted recovery.

Unknown Codex usage is JSON `null` per call and in `codex_tokens` whenever
any Codex call lacks usage. Summaries and budget messages separately expose
`codex_tokens_observed`, `codex_calls_reported`, `codex_calls_total`,
`codex_usage_complete` and the per-call list. Observed zero is distinct from
unknown. This is usage accounting, not a claim of complete dollar spend.

## 13. Referee protocol (S4)

`orchestrator/rule.py` validates required shape, finite numbers, registered
criteria, iteration order and history consistency, then implements §8.
Missing or inconsistent inputs raise `RuleInputError`; no merits verdict
is emitted. `validate_decision` checks both rule and secondary output.
The decision is persisted and committed before the secondary call starts.
The secondary uses the existing pinned S4 configuration and a synchronous
bounded call. Its output is diagnostic only; all exceptions are recorded
as unavailable, and quota failures additionally halt further model work.

## 14. Pipeline stages

S1 HYPOTHESIZE (claude): reserve a hypothesis ID, then write and commit
   its card with pre-registered blocks before any evaluation.
S2 IMPLEMENT (codex): writes signal.py; the existing four contract checks
   run. Red tests get one repair attempt, then infrastructure failure.
S3 BACKTEST (engine, no LLM): reserve an evaluation ID and input identities,
   persist the launch boundary, invoke once with the ID, kind and writer
   lease, and preserve the complete result before S4.
S4 RULE (code): decide and bind authority to that completed result; the
   existing Codex referee configuration supplies a recorded secondary check.
S5 MEMO (claude): writes memo with result lineage. Its failure cannot erase
   a terminal decision. After a terminal decision, finalization records the
   ledger, queue where appropriate, lifecycle and state. Local stage commits
   never push; launcher finalization captures final logs and records.

R4 applies up to and including S4 where no valid rule decision exists.
After a terminal KILL or PROMOTE, the decision stands. Launch uncertainty
halts for human reconciliation; an interrupted ITERATE is not a merits kill.
PROMOTE remains a human-only, single-shot evaluation gate, never automatic.

## Changes from v1.0 (DRAFT)

- Count completed evaluations, including parameter variants, with frozen
  historical contributors; remove dependence on ledger row count.
- Reserve durable IDs and bind engine results to the evaluated bytes.
- Preserve full immutable results, record launch uncertainty and recover
  decisions without repeating evaluation or inventing verdicts.
- Use §8 as executable authority; retain the model check as secondary.
- Isolate fixture/synthetic/replay workspaces and enforce one writer.
- Report unknown usage as null with observed-subtotal completeness fields.
- Preserve all original research artifacts and label legacy imports and
  bookkeeping recounts. No scientific threshold, fold, cost or grid change.
