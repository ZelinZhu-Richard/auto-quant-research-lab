# SPEC v2 — Research validity amendment

Status: implementation proposed for human review; NOT a retroactive approval of
v1.0 and NOT permission to start a large autonomous run. `SPEC.md` remains the
unaltered historical contract. New engine output is `protocol=quantlab-v2`;
old results are readable as historical evidence but cannot authorize v2 actions.

## Decision authority and evidence

`engine/policy.py` computes every criterion and KILL/ITERATE/PROMOTE. A failure
uses the first unused grid point in declaration order, up to two iterations;
all criteria plus DSR >= .95 are required for PROMOTE. The existing four
pre-registered thresholds and sign convention remain. Invalid, missing,
nonfinite, inconsistent, stale or uncommitted evidence is an infrastructure
abstention, never a merit finding. Models may explain decisions in memos;
model identity is no longer authorization. S4 and terminal/queue writers verify
committed results, card, signal, engine/spec hashes, score artifacts, journal
snapshot and daily metric reconstruction. The card must match its first Git
commit. Only the PARAMS line may change after the first signal commit.

A PROMOTE means a research screen cleared under the stated assumptions, NOT a
valid trading discovery, probability of truth, executable strategy or automatic
holdout access. A human alone controls any later single-shot evaluation.

## Trial accounting

`trial_evidence/events/` is an ordered SHA-256 chain of immutable start/terminal
JSON events. A start is durably published before executing signal code or
scoring, including every S2 harness, its one repair, and iteration checks.
A `checked` terminal event means harness pass with no numeric score. Harness
and S3 attempts with identical code/params/data share one statistical slot;
repaired source is another configuration. Tested source snapshots are retained. Every new configuration (including changed source, dataset or protocol)
counts. Identity includes hypothesis, iteration, parameters, card/signal/source
and data hashes. Each attempt has a new UUID and timestamp. Exact replays have
the same identity, one statistical slot, and must reproduce identical score
artifacts; different output is infrastructure failure. Failed launches retain
identity and failure reason. A crash leaves an unresolved start: it blocks
promotion and run preflight until a human reconciles the evidence. No automatic
retry erases evidence. One kernel lock serializes evaluations; immutable files
are published atomically. Git commits anchor deletions/changes; a hash chain is
not protection against an attacker who controls both Git and the grader.

Report attempts, successfully scored attempts, unique scored configurations,
penalty count and unresolved attempts separately. Effective independent trial
count is **unknown (null)**. The penalty count is ALL distinct evaluated
configurations, including failed scoring attempts. Numeric scores use full
precision; markdown LEDGER rounding never supplies statistical inputs.
Historical inventory is separate, hash-bound to the original ledger prefix and
Git results. Pre-S3 infrastructure failures have no market score/trial slot.
Unlogged external searches cannot be reconstructed and must be disclosed.

DSR retains Bailey–López de Prado's expected-maximum and nonnormal-moment
formula, using the full configuration count as an independence **proxy**. For
new v2 decisions, trial-Sharpe variance is max(sample variance, 1/(T-1)); the
floor is an approximate iid null variance sensitivity guard, not an exact
calibration. This prevents identical scores or small samples from disabling
the multiple-search penalty. No estimated correlation discount is permitted.
Correlated settings, serially dependent returns, optional stopping, repeated
dataset reuse and adaptive LLM proposals invalidate an exact independent-trial
interpretation. Counting three correlated trials as independent does NOT
establish exact error control. A large search requires an independently
reviewed, pre-registered family/search budget and dependence-aware calibration
on public/synthetic development data. No private benchmark tasks are used.

Reference: [original DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
H001 retrospective diagnostics retain the unmodified formula (no variance
floor), explicitly separate from original scores and from v2 returns.

## Timing and accounting

`historical_close_v1` preserves the old close(t) signal / same-close fill,
target-to-target turnover and missing-price-zero conventions for regression
only. It is not admissible v2 promotion evidence.

`delayed_close_v2` is a daily-bar research fill model: observe the completed
UTC candle dated t; submit after it completes; simulate fill at the close of
the candle dated t+1; first earn the close(t+1) to close(t+2) interval. The harness receives defensive
copies and rejects mutation. Scoring uses a fresh module namespace for EACH
observation date, never the module that saw the full-panel harness. Thus a
return day d uses signal observations through d-2, trades at close(d-1), and
marks at close(d). There is a full candle between observation and assumed
fill. Orders are modeled as target-NAV instructions with sizing at fill.
Execution at the exact reported close is an assumption, not guaranteed
liquidity. No next-open executable-liquidity claim is made.

Carry signed asset notionals and cash, mark existing holdings to the fill
price, solve post-fee NAV = pre-fee NAV - .0025*sum(abs(target notionals -
current drifted notionals)), then trade to target weights times post-fee NAV.
Cash changes by minus net traded notional minus fees. Fee and turnover are
normalized by pre-trade NAV; daily net return includes post-fee exposure PnL.
Initial NAV is 1, cash is 1, holdings zero. Closing to cash incurs real costs.
Gross/net exposure 1/0 holds immediately after nondegenerate rebalances; it
may drift between them. Nonpositive NAV aborts. Initial wealth belongs in the
drawdown peak; v1 omitted first-day losses.

Missing/nonfinite/nonpositive marks for held or selected assets abort the
whole evaluation, including disappearance when the next target is zero. No
free delisting exit, forward fill or zero-return substitution. A selected
asset with no trading volume on the fill bar aborts. Zero volume is not proof
of liquidation value. Unchanged positive-volume closes can be real; daily
OHLCV alone cannot reliably identify stale vendor marks.

Cash interest, borrow availability/fees, short rebates, financing, market
impact, volume participation and forced liquidation are NOT evidenced by this
dataset. Cash/financing rates are zero research assumptions. Short positions
are hypothetical. A v2 research screen cannot claim investable performance
until these inputs and conservative capacity/cost sensitivity are reviewed.

## Universe and migration

The existing data files, universe/download audit JSONs and hashes are preserved.
The active-market download pool excludes past delistings. First-90-day ranking
is available only after its window, not at its stated sample-start `as_of`.
The current November scoring start is after that window, but the candidate
pool remains survivor-biased. A first observed bar does not prove a listing.

Optional point-in-time membership requires externally supplied listing and
exclusive delisting dates, observation/publication timestamps and source
references. Membership uses only information available on the decision date;
bar coverage is checked independently. The code supports validated membership
records; this PR supplies no invented historical listing/delisting dataset and
does not migrate the frozen market sample.

Migration: preserve old commits, H001 and prior runs; audit the historical
trial inventory; start new hypothesis IDs/run series on a reviewed commit.
Never overwrite an old result or compare v1 and v2 returns as if generated
under the same protocol. Use synthetic fixtures for engineering validation.
Rebuild and verify Docker isolation and the read-only grader, approve this
amendment, reconcile pending attempts and historical search coverage, and
complete a human-supervised shakedown before another large run. The holdout
remains outside all autonomous read/mount scopes; no migration reads it.
