# SETUP_NOTES.md

Written before any project code, per the session's standing rules. Source of
truth is PROJECT_BRIEF.md; this file records my reading of it, the verified
data schema, and every assumption I resolved on my own.

## (a) The seven non-negotiable rules, restated in my own words

- **R1 — Two zones.** `engine/` (loader, cost model, backtester, metrics) is
  built once during setup and then frozen: the overnight loop never edits it,
  and Docker mounts it read-only so it *cannot*. The only directory the loop
  is allowed to write code into is `hypotheses/<id>/`.
- **R2 — Holdout is physically absent.** The container's world contains only
  `data/train_val/`. Holdout lives outside the repo entirely. No code may
  reference, glob, download, or otherwise reconstruct holdout data — the
  protection is physical absence, not a polite instruction.
- **R3 — Pre-registration.** Kill criteria are written into hypothesis.md
  *before* any data is touched, and the referee's judgment is confined to
  exactly those criteria plus the deflated Sharpe ratio computed at the
  current LEDGER hypothesis count. No post-hoc goalpost moving.
- **R4 — Kill-and-continue.** Any mid-cycle failure becomes
  KILLED(reason=infrastructure) — logged distinctly from KILLED(merits) —
  STATE.md is reset, and the loop moves to the next hypothesis. Red tests get
  exactly one repair attempt. A broken cycle is never retry-looped.
- **R5 — Review gates are structural.** Any diff touching `orchestrator/`,
  `engine/`, `Dockerfile`, `docker-compose*`, `pyproject.toml`, or `uv.lock`
  requires a Codex review pass before commit. During overnight runs the loop
  must not touch those paths at all.
- **R6 — No credentials in the container.** The overnight container carries
  no GitHub push access, no exchange keys, nothing. It commits locally only;
  the human reviews in the morning and pushes.
- **R7 — Human Gate H.** The first unattended overnight run cannot start
  until the human has (a) approved SPEC.md — it freezes at approval time, not
  drafting time — and (b) personally executed shakedown.sh with a clean
  result. When setup is complete I stop and say exactly:
  "GATE H: awaiting human shakedown and SPEC approval".

## (b) Parquet schema — read from the ACTUAL file `data/train_val/BTC.parquet`

pyarrow schema:

    open: double
    high: double
    low: double
    close: double
    volume: double
    date: timestamp[ms, tz=UTC]     <- index

pandas view (pandas 3.0.3):

- Index: `DatetimeIndex`, name `"date"`, dtype **`datetime64[ms, UTC]`** —
  note **millisecond** resolution, not the ns default. pandas 3.x preserves
  the parquet unit. The engine loader must not assume ns; all date logic will
  be resolution-agnostic (tz-aware comparisons, no `.value` arithmetic).
- Columns: exactly `open, high, low, close, volume`, all `float64`.
- Timestamps are daily UTC midnights, monotonic increasing, unique.
- Audited all 50 files: every symbol spans 2022-07-01 → 2025-06-30
  (1096 rows), zero calendar gaps, zero NaNs, zero non-positive closes.
- `volume` is in **base-currency units** (e.g. BTC), not dollars. Dollar
  volume requires `close * volume`.

## (c) Ambiguities resolved by assumption (stated explicitly)

1. **The data did not exist at session start.** The brief's Section 2 lists
   `data/train_val/` as already built; the repo had no `data/` at all. The
   human confirmed mid-session: fetch it via ccxt using the existing
   `data_pipeline.py`. I used the pipeline's own documented commands and
   defaults: `download-all --start 2022-07-01`, `universe --start
   2022-07-01`, `split --cutoff 2025-07-01`.
2. **data_pipeline.py has a pagination-termination bug** (it is a protected
   file; I did not modify it). Its `_fetch_full_history` advances `since`
   past "now", and Coinbase Exchange raises
   `ExchangeError("Start cannot be in the future")` instead of returning an
   empty batch — so every actively-trading symbol was discarded as FAILED
   (first run: ok=2 of 394). Verified empirically. Workaround (transparent,
   pipeline logic untouched): `scripts/fetch_train_val_data.py` subclasses
   the ccxt exchange so `fetch_ohlcv` returns `[]` for exactly that error,
   which the pipeline's own `if not batch: break` handles. Re-run:
   ok=176, failed=0. Bug logged in BACKLOG.md for the human to fix.
3. **218 symbols report "empty" in download_report.json.** These are pairs
   listed after ~Apr 2023: the pipeline's first request window
   [start, start+300d] contains no candles for them and it does not advance
   the window. Harmless for this project — universe membership requires
   listing within 30 days of 2022-07-01 — but noted in BACKLOG.md.
4. **I moved `data/holdout` out of the repo** to
   `~/quantlab_holdout_DO_NOT_MOUNT`, exactly as data_pipeline.py's split
   step instructs. Section 11 makes data/ changes human-only; I read the
   human's instruction to build the dataset as delegating the whole
   pipeline including this move. The repo now contains no holdout directory
   (R2 verified).
5. **Repo root is `quantlab/`** (the git repo with origin
   `ZelinZhu-Richard/auto-quant-research-lab`). `engine/`, `orchestrator/`,
   `hypotheses/` etc. are created at this root.
6. **`.gitignore` was missing `data/all/`** while Section 6 requires it
   ignored; I added it (gitignore is not a protected file, and the change
   *implements* the brief rather than deviating from it).
7. **Train_val window** is 2022-07-01 → 2025-06-30 inclusive (1096 daily
   bars); holdout is 2025-07-01 onward and lives outside the repo. Walk-
   forward folds (4, expanding) will be defined over the train_val window in
   SPEC.md with exact dates.
8. **Model families**: S1 HYPOTHESIZE and S5 MEMO run on `claude`
   (Anthropic); S2 IMPLEMENT and S4 REFEREE run on `codex` (OpenAI). This
   satisfies "referee must be a different model family than S1".
9. **CLI syntax verified at execution time** (per Section 8):
   `codex-cli 0.139.0` — non-interactive form is
   `codex exec [PROMPT]` with `-s read-only|workspace-write`,
   `--output-last-message <file>`, `-C <dir>`, `--skip-git-repo-check`.
   `claude 2.1.201` — headless form is `claude -p "<prompt>"` with
   `--output-format text|json`, `--max-budget-usd <amount>`,
   `--allowedTools`, `--permission-mode`. Re-verified before each first use.
10. **pandas 3.0.x watch items** flagged out loud, per Section 2: (i) the
    ms-resolution index above; (ii) Copy-on-Write is always on — engine code
    avoids chained assignment; (iii) `numpy` nullable-string default dtype
    changes do not affect this all-float dataset. Any further 3.0
    incompatibility found in the engine will be flagged in BACKLOG.md, not
    silently worked around.
11. **`main.py`** is uv-init boilerplate; left untouched and committed as
    found.

## Test inventory (canonical counts, reconciled 2026-07-11)

- `uv run pytest tests/` collects and passes exactly **54** tests — the
  frozen-engine, harness, and orchestrator-validator suites.
- `tests/hypothesis_harness.py` holds **4** per-hypothesis contract checks
  (index alignment, determinism, NaN handling, no-lookahead). By design it
  does not match pytest's `test_*.py` discovery pattern; S2 runs it as its
  own SEPARATE invocation which executes exactly those 4 tests:
  `QUANTLAB_HYPOTHESIS=H### uv run pytest tests/hypothesis_harness.py`.
- 58 is therefore the COMBINED inventory across the two invocations (54 in
  the default suite + 4 in the per-hypothesis harness), never the count of
  any single pytest run.
- The "56-test suite" phrase in the Gate H session summary was a prose
  error; no repo artifact carried a wrong count (commit-message counts
  were accurate at their respective commits).

## Canonical review-gate procedure (Section 8 interpretation — RATIFIED)

Ratified by the human 2026-07-11, resolving the interpretation question
logged in BACKLOG.md during A1. This is the canonical procedure for every
Codex review gate (after build-order steps per PROJECT_BRIEF §8/§9 and any
diff touching an R5 path).

1. **Run the gate against the latest commit**, non-interactive
   (`codex exec -s read-only`), using the §8 reviewer-only prompt template;
   verify the CLI flags via `--help` before first use after any CLI or
   config change. Gates are BLOCKING: no other work proceeds while a
   review is in flight.
2. **On FAIL where every finding is NEW and, in Claude's judgment,
   legitimate:** fix, commit the fixes as their own commit with the
   findings enumerated in the message (audit spine), and re-run the gate
   against that commit. This may repeat across rounds; each round's
   findings must be new.
3. **Stop and await human arbitration** — write BOTH positions to
   STATE.md, commit, halt — when either deadlock condition appears:
   (a) the SAME finding recurs after its fix round (the fix is disputed),
   or (b) Claude believes a finding is WRONG. Never argue past one round
   on any single finding.
4. **Rationale (the ratified reading):** §8's "if still FAIL: STOP, write
   both positions, await arbitration" targets UNRESOLVED findings — a
   Claude-vs-Codex deadlock in which two positions exist. Fresh,
   uncontested findings on new content are resolvable, not deadlocked;
   halting the build to arbitrate a dispute that does not exist serves
   no one. The arbitration clause's own wording ("both positions")
   presupposes disagreement.
5. **Empirical record from setup** (all converged, every FAIL round
   consisted of new legitimate findings): A1 3 rounds, A2 3, A3 7, A5 6,
   A6 3, Gate-H amendment 2, launcher-finalize fix 3.
