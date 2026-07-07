# SPEC.md — Frozen Research Specification
#
# STATUS: DRAFT (v0.1). Freezes at Gate H when the human approves it.
# All thresholds marked PROPOSED are awaiting human approval.
# After freeze: read every cycle, written by no one. Changes require a new
# versioned SPEC and a new run series.

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

Purpose: penalize the aggregate Sharpe for the number of hypotheses tried.

Inputs: daily net returns of the aggregate window (length `T`, per-period
sharpe `sr = mean(r)/std(r, ddof=1)`, skewness `g3`, raw kurtosis `g4`),
and the trial count `N`.

- `N` = (LEDGER.md line count at S3 time) + 1. The "+1" IS the current
  hypothesis: its ledger line is only appended at end-of-cycle, so it is
  never yet in the file when S3 runs — the engine adds it explicitly.
  Iterations of the same hypothesis do not increment N (they share one
  ledger line and one trial slot; a re-run at a new grid point overwrites
  the current trial's sharpe, it does not add a trial).
- Trial-Sharpe set for variance: the `sharpe=` values parsed from every
  LEDGER.md line, EXCLUDING `nan` entries (infra-kills that never produced
  a backtest), each de-annualized by dividing by sqrt(365), PLUS the
  current hypothesis's `sharpe_daily` from this run. `V` = variance
  (ddof=1) of that set. If the set has fewer than 2 elements or `V == 0`,
  set `SR0 = 0` — i.e. the Sharpe is NOT deflated in that case and `N` has
  no effect. Stated limitation, accepted: deflation only activates once at
  least two numeric trial Sharpes exist in the ledger; `nan` (infra-kill)
  trials count in `N` but contribute nothing to `V`.
- Expected max trial Sharpe under N trials:
  `SR0 = sqrt(V) * ((1 - g) * z(1 - 1/N) + g * z(1 - 1/(N*e)))`
  where `g = 0.5772156649` (Euler–Mascheroni), `z` = standard normal
  quantile (inverse CDF), `e` = Euler's number.
- `DSR = Phi( ((sr - SR0) * sqrt(T - 1)) / sqrt(1 - g3*sr + ((g4 - 1)/4) * sr^2) )`
  where `Phi` = standard normal CDF. If the denominator radicand is <= 0,
  DSR = 0.0 (pathological tails; fail-safe toward killing).
- Implementation: `engine/metrics.py::deflated_sharpe(returns, n_trials,
  trial_sharpes)` — plain math (no scipy; use `statistics.NormalDist`).

## 8. Kill criteria — template and PROPOSED defaults

Every hypothesis.md MUST pre-register concrete numbers for all four, before
any data is touched (R3). Defaults below are PROPOSED starting points; a
hypothesis may tune them only WITH WRITTEN JUSTIFICATION in hypothesis.md,
and never after S1.

| criterion | PROPOSED default | judged on |
|---|---|---|
| min annualized Sharpe (after costs) | >= 1.0 | aggregate window |
| max drawdown | <= 0.30 | aggregate window |
| min hit rate | >= 0.52 | aggregate window |
| stability | `(fold_sharpe > 0) == (aggregate_sharpe > 0)` for >= 3 of 4 folds | folds |

Sign convention (deterministic, incl. exact zero): a Sharpe is "positive"
iff strictly `> 0`; zero counts as non-positive. A fold is sign-consistent
iff `(fold_sharpe > 0) == (aggregate_sharpe > 0)`. A flat (all-zero) result
is thus sign-consistent everywhere but fails the min-Sharpe criterion —
fail-safe toward killing.

Plus one SPEC-global criterion (not tunable per hypothesis):

| criterion | PROPOSED default |
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
{"min_sharpe": 1.0, "max_drawdown": 0.30, "min_hit_rate": 0.52,
 "min_sign_consistent_folds": 3}
```

- S1 writes these blocks once; S2/S4 parse them; nothing rewrites them
  after S1 (R3). The orchestrator validates their presence and shape
  before S2 starts; malformed blocks → infra-kill.

## 9. results.json schema (written by S3; engine output is the only source)

```json
{
  "hypothesis_id": "H001",
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
    "n_trials": 1,
    "trial_sharpes_daily": [0.0],
    "deflated_sharpe": 0.0
  },
  "status": "ok"
}
```

All floats full precision (no rounding in the file). `folds` always has
exactly 4 entries. `status` is `"ok"` or `"error: <one line>"` (an error
status at S3 → infra-kill per R4).

## 10. decision.json schema

Exactly one writer per cycle, chosen by path:
- MERIT path (S3 produced `status: "ok"`): the S4 REFEREE writes it, with
  `kill_reason` ∈ {"merits", null}. The referee never writes
  "infrastructure".
- INFRASTRUCTURE path (any stage failed; the referee is never invoked):
  the ORCHESTRATOR writes it mechanically, with `decision: "KILL"`,
  `kill_reason: "infrastructure"`, `criteria: []`, `iterate_params: null`,
  `referee_model: "orchestrator"`, and the failure line as justification
  (R4).

```json
{
  "hypothesis_id": "H001",
  "iteration": 0,
  "decision": "KILL",
  "kill_reason": "merits",
  "criteria": [
    {"name": "min_sharpe", "required": ">= 1.0", "observed": 0.42, "pass": false},
    {"name": "max_drawdown", "required": "<= 0.30", "observed": 0.18, "pass": true},
    {"name": "min_hit_rate", "required": ">= 0.52", "observed": 0.51, "pass": false},
    {"name": "stability_3_of_4", "required": "3/4 folds sign-consistent", "observed": "2/4", "pass": false},
    {"name": "deflated_sharpe", "required": ">= 0.95", "observed": 0.31, "pass": false}
  ],
  "iterate_params": null,
  "justification": "One paragraph citing the pre-registered numbers above.",
  "referee_model": "codex <model id>",
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

- `#N`: 1-based hypothesis counter == LEDGER line number.
- `DECISION(reason)`: `KILLED(merits)`, `KILLED(infrastructure)`,
  `PROMOTED`, or `ITERATED->KILLED(...)` etc. — final state only, one line
  per hypothesis regardless of iterations.
- `sharpe` = aggregate annualized net Sharpe (4 decimals), `dsr` = deflated
  Sharpe (4 decimals), `dd` = max drawdown fraction (4 decimals), `hit` =
  hit rate (4 decimals). For infra-kills where no backtest ran, all four
  are `nan`.
- `tests=pass` iff the S2 test suite passed.

## 12. Hard stops for the overnight loop (enforced by orchestrator/loop.py)

| stop | PROPOSED value |
|---|---|
| max hypotheses per run | 30 |
| wall-clock cap | 6 hours (checked between stages; loop exits cleanly) |
| consecutive infrastructure kills | 3 → halt + diagnosis written to STATE.md |
| max iterations per hypothesis | 2 (also enforced by referee protocol) |
| per-call claude budget | `--max-budget-usd 2.00` |
| per-run LLM cost ceiling | $25.00 summed over claude-reported `total_cost_usd`; codex spend counted when its JSONL events expose token usage, otherwise logged as unknown |
| per-stage subprocess timeout | S1 600s, S2 1200s, S3 900s, S4 600s, S5 600s |

On any hard stop: write the reason to STATE.md, commit locally, exit 0.

## 13. Referee protocol (S4) — self-contained instructions

The referee model receives: this SPEC (sections 8, 8b, 10), hypothesis.md,
and results.json. It must:
1. Parse the pre-registered kill-criteria JSON block and the ordered
   iteration-grid JSON block from hypothesis.md (Section 8b shapes).
2. Compare each criterion against results.json values (results.json is
   ground truth; the referee does no arithmetic beyond comparisons — DSR
   is precomputed in `referee_inputs.deflated_sharpe`, and tried grid
   points are listed in `iteration_history`).
3. Apply Section 8's decision rule mechanically.
4. Emit decision.json (Section 10 schema) and NOTHING else. No fixes, no
   new ideas, no commentary outside the justification paragraph.

Acceptance test for this SPEC (A1): a different model, given only this
file plus a hypothesis.md and results.json, reaches the same decision as
the reference implementation.

## 14. Stage-by-stage cycle contract (operational summary)

S1 HYPOTHESIZE (claude): writes `hypotheses/H###/hypothesis.md` per the
   template; must read last 30 LEDGER lines and avoid duplicating killed
   ideas; declares iteration grid NOW (max 2 points beyond iteration 0) and
   max lookback <= 120 days.
S2 IMPLEMENT (codex): writes `hypotheses/H###/signal.py` implementing
   Section 2, plus passing the shared test harness (lookahead, NaN
   handling, index alignment, determinism). Red tests → ONE repair attempt
   → else infra-kill.
S3 BACKTEST (engine, no LLM): `uv run python -m engine.run_backtest
   --hypothesis H###` writes results.json (Section 9).
S4 REFEREE (codex): emits decision.json (Sections 8, 10, 13).
S5 MEMO (claude): writes `hypotheses/H###/memo.md` — one page, ALL
   outcomes, includes lineage (git commit, data manifest sha256, hypothesis
   card version).
Then: append LEDGER line, overwrite STATE.md, `git add -A && git commit`
(local only during overnight runs, R6).

PROMOTE → append hypothesis id to PROMOTED_QUEUE.md. Holdout is HUMAN-ONLY
(R2, Section 11 of PROJECT_BRIEF).
