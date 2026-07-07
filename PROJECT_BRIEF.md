# PROJECT_BRIEF.md — Autonomous Quant Research Lab
# SINGLE SOURCE OF TRUTH. Replaces all prior briefs. Read fully before any action.
# Where instinct conflicts with this file, the file wins; log objections in BACKLOG.md.
 
## 0. MISSION AND DEFINITION OF DONE
 
A multi-agent system that generates trading hypotheses, implements them, backtests
them against a FROZEN engine, kills them against PRE-REGISTERED criteria, and writes
audit-trailed research memos. The product is idea-killing discipline, not alpha.
Headline artifact: "N hypotheses tested, M killed, full audit trail on every one."
 
The project is PERFECTLY FINISHED when ALL of the following are true:
 
  [ ] Frozen engine passes: unit tests, a known-answer test (toy signal with
      hand-computed PnL), and the leaky-signal test (a deliberately lookahead-
      contaminated signal is caught automatically).
  [ ] At least 3 clean unattended overnight runs completed; >= 100 total
      hypotheses in LEDGER.md; infrastructure-kill rate < 10% on the last run.
  [ ] At least 1 PROMOTE has gone through the single-shot holdout protocol,
      with the result documented in a memo WHETHER OR NOT it survived.
      (A documented holdout failure is a success of the process.)
  [ ] Reproducibility: fresh clone + `uv sync` + one documented command reruns
      a full single-hypothesis cycle end to end.
  [ ] README.md contains: architecture diagram, the two-zone rationale,
      ledger statistics table, stated limitations, and a 5-minute demo script.
  [ ] Instrumented tool-call log exists for at least one overnight run
      (raw material for the Scutum companion project).
  [ ] The repo history shows one commit per stage — the audit spine is legible.
 
## 1. NON-NEGOTIABLE RULES (memorize these)
 
R1. TWO ZONES. `engine/` (backtester, data loader, cost model, metrics) is the
    frozen zone: built during setup, then never edited by the overnight loop,
    mounted read-only in Docker. `hypotheses/<id>/` is the agent zone: the ONLY
    place the loop writes code.
R2. HOLDOUT IS PHYSICALLY ABSENT. Only `data/train_val/` exists inside the
    container. Never write code that references, globs, or reconstructs holdout.
R3. PRE-REGISTRATION. Kill criteria are written in hypothesis.md BEFORE any data
    is touched. The referee judges ONLY against those criteria plus the deflated
    Sharpe ratio over the running hypothesis count.
R4. KILL-AND-CONTINUE. Any mid-cycle failure => KILLED(reason=infrastructure),
    logged distinctly from KILLED(merits), STATE.md reset, loop continues.
    One repair attempt max on red tests. Never retry-loop a broken cycle.
R5. REVIEW GATES ARE STRUCTURAL. Diffs touching `orchestrator/`, `engine/`,
    `Dockerfile`, `docker-compose*`, `pyproject.toml`, or `uv.lock` require a
    Codex review pass before commit (see Section 8). During overnight runs the
    loop must not touch these paths at all.
R6. NO CREDENTIALS IN THE CONTAINER. The overnight container has no GitHub
    push access, no exchange keys, nothing. It commits locally; the human
    pushes after morning review.
R7. HUMAN GATE H. The first unattended overnight run may not start until the
    human has (a) approved SPEC.md — which freezes at that moment, not at
    drafting — and (b) personally executed the shakedown script (Section 9,
    step A7) with a clean result. Claude Code must stop and say exactly:
    "GATE H: awaiting human shakedown and SPEC approval" when setup is complete.
 
## 2. CURRENT REPO STATE (what already exists — do not rebuild)
 
- data_pipeline.py (v2): Coinbase Exchange public API; universe = top-50 by
  MEDIAN daily dollar volume over the first 90 days of the sample (point-in-time,
  anti-survivorship); must be listed within 30 days of start; stablecoins/
  wrapped/staked excluded. Stated limitations live in data/universe.json.
- data/train_val/{SYMBOL}.parquet — DatetimeIndex "date" (UTC, daily),
  columns: open, high, low, close, volume (float64). INSPECT ONE REAL FILE
  before writing the loader; build against reality, not this description.
- data/universe.json, data/download_report.json — audit artifacts, committed.
- data/holdout has been MOVED OUTSIDE the repo by the human. If you find a
  holdout directory inside the repo, STOP and alert the human.
- Environment: uv, Python 3.12, pandas 3.0.x (major version — flag any
  compatibility concern out loud rather than silently working around it),
  pyarrow, ccxt, pytest. No new dependencies without a review-gate pass.
## 3. ARCHITECTURE SPEC (concrete contracts)
 
### 3.1 Signal interface (the one contract everything shares)
    def compute_signal(panel: pd.DataFrame) -> pd.Series
- `panel`: MultiIndex (date, symbol) DataFrame with columns
  open/high/low/close/volume, containing ONLY data up to and including the
  evaluation date (the engine enforces this by construction).
- Returns: pd.Series indexed by (date, symbol), higher = more attractive.
  NaN = "no position opinion for this asset on this date."
- Purity requirement: no I/O, no randomness without a fixed seed, no state
  between calls.
### 3.2 Portfolio construction (fixed in the engine, not chosen by agents)
- Each date: cross-sectional rank of the signal; long top quintile, short
  bottom quintile, equal weight within each leg, dollar-neutral.
- EXECUTION LAG: signal computed on close(t) earns close(t)->close(t+1)
  returns starting at t+1. This one-day lag is the engine's core
  no-lookahead guarantee; it is not configurable.
- Assets with NaN signal on a date are excluded from that date's ranking.
### 3.3 Costs and metrics (fixed in the engine)
- 25 bps per side, charged on turnover, every rebalance. Not optional.
- Metrics per run: annualized Sharpe (after costs), max drawdown, hit rate,
  annualized turnover, per-fold Sharpe for stability, and DEFLATED SHARPE
  RATIO per Bailey & Lopez de Prado, using the running hypothesis count N
  read from LEDGER.md at referee time.
### 3.4 Walk-forward
- train_val period only. 4 expanding-window folds. Report per-fold and
  aggregate. "Stability" in kill criteria means: sign-consistent Sharpe
  across at least 3 of 4 folds (default; SPEC.md may tune before freeze).
## 4. FIVE-STAGE CYCLE (per hypothesis, directory hypotheses/H###/)
 
S1 HYPOTHESIZE (Claude): writes hypothesis.md —
   economic rationale incl. who is on the other side of the trade; testable
   prediction; features required; PRE-REGISTERED kill criteria (min annualized
   Sharpe after costs, max drawdown, min hit rate, stability rule); declared
   iteration grid (max 2 iterations, parameters listed NOW or never).
   Must read the last 30 LEDGER lines and avoid duplicating killed ideas.
S2 IMPLEMENT (Codex): writes signal.py implementing compute_signal per 3.1,
   plus tests via the shared harness: lookahead test (recompute on data
   truncated at t; assert equality at t for >=10 sampled t), NaN handling,
   index alignment, determinism. Red tests => one repair attempt => infra-kill.
S3 BACKTEST (engine, no LLM): runs the frozen engine, writes results.json
   (schema defined in SPEC.md).
S4 REFEREE (Codex — must be a different model family than S1): reads
   hypothesis.md + results.json + LEDGER count. Outputs decision.json with
   exactly one of KILL / ITERATE / PROMOTE and one-paragraph justification
   citing the pre-registered numbers. Referee NEVER proposes fixes or new
   ideas. ITERATE only within the declared grid, max 2.
S5 MEMO (Claude): memo.md, one page, written for ALL outcomes including
   kills: rationale, method, results, decision, lineage (git commit hash,
   sha256 of the data files used, hypothesis card version).
Then: append one LEDGER line, overwrite STATE.md, `git add -A && git commit`.
 
PROMOTE => hypothesis id appended to PROMOTED_QUEUE.md. Holdout execution is
HUMAN-ONLY, once per idea, ever. The loop never touches holdout (R2).
 
## 5. FOUR-FILE CONTEXT PROTOCOL
 
- SPEC.md    — frozen at Gate H. Read every cycle; written by no one after.
- LEDGER.md  — append-only. Format:
               `#N | name | DECISION(reason) | sharpe=X dsr=Y dd=Z hit=W | tests=pass`
- STATE.md   — overwritten at every stage transition; max 50 lines; contains
               current hypothesis id, stage, last error.
- BACKLOG.md — append-only suggestions. NEVER read during overnight runs.
               Human promotes items to SPEC.md manually (which, post-freeze,
               means a new versioned SPEC and a new run series).
Per-cycle context = SPEC.md + STATE.md + last 30 LEDGER lines + current
hypothesis directory. Nothing else. This is the context-window budget.
## 6. GIT AND GITHUB DISCIPLINE
 
- Branch: main only for the pilot.
- INTERACTIVE phases (setup): commit after every completed build-order step,
  message format `setup(A3): lookahead harness — catches planted leak`.
  Push to origin after EVERY committed step. Push is part of "done".
- OVERNIGHT runs: commit after every stage (message `H042/S3: backtest`),
  LOCAL ONLY (R6). Morning: human reads `git log --oneline` and the diffs
  BEFORE running anything, then pushes.
- Never force-push. Never rewrite history. History is the audit spine.
- data/all, data/raw, data/train_val, data/holdout stay gitignored;
  universe.json and download_report.json are committed.
## 7. SAFETY / DOCKER SPEC (build in A6, used from Gate H onward)
 
- Base: python:3.12-slim + uv. Non-root user.
- Mounts: repo read-write EXCEPT engine/ and data/ mounted read-only.
- Network: egress allowlist = api.anthropic.com + the OpenAI API endpoint
  ONLY (verify the exact Codex endpoint domain during A6; do not guess).
  No package-registry egress at runtime — deps are baked into the image.
- Resource limits: --memory 4g --cpus 2. Wall-clock cap enforced by the
  orchestrator, not just Docker.
- Instrument: every CLI invocation the orchestrator makes is logged to
  runs/<timestamp>/toolcalls.jsonl (command, cwd, duration, exit code).
  This log is a first-class deliverable (Scutum raw material).
## 8. CODEX REVIEW GATES (interactive phase)
 
After steps A1, A3, A5, A6 (and any diff matching R5 paths), Claude Code runs
Codex in non-interactive mode against the latest commit. VERIFY the current
CLI syntax with `codex --help` / official docs at execution time — do not rely
on memorized flags. The review prompt template:
 
  "Read PROJECT_BRIEF.md sections 1, 3, 4. Review the diff introduced by the
   latest commit against build-order step <X> acceptance criteria. Your role:
   reviewer only. Flag violations of the non-negotiable rules, correctness
   bugs, and lookahead risks. Do NOT propose alternative architectures.
   Output: PASS or FAIL with a numbered findings list."
 
Resolution loop: Claude fixes legitimate findings, re-runs review ONCE. If
still FAIL, or if Claude believes a finding is wrong: STOP, write both
positions to STATE.md, await human arbitration. Never argue past one round.
 
## 9. BUILD ORDER (each step = code + tests + commit + push; do not skip ahead)
 
A1. SPEC.md draft. Concretize every contract in Section 3 with real numbers;
    kill-criteria template with PROPOSED defaults (mark them PROPOSED:
    e.g. min Sharpe 1.0 after costs, max DD 30%, min hit 52%, 3-of-4 fold
    sign consistency — tune only with justification); results.json and
    decision.json schemas; hard-stop numbers for the loop.
    Acceptance: a different model could referee from SPEC.md alone.
    => Codex review gate.
A2. engine/: loader (against the REAL parquet schema), cost model,
    walk-forward backtester per 3.2-3.4, metrics incl. deflated Sharpe.
    Acceptance: unit tests + known-answer test (hardcoded toy signal on a
    tiny synthetic panel with hand-computed expected PnL, exact to 1e-9).
A3. Lookahead harness as a reusable pytest fixture + the planted-leak test:
    a signal deliberately using close(t+1) must be CAUGHT automatically.
    Acceptance: the leaky signal fails, a clean momentum signal passes.
    => Codex review gate.
A4. Four-file protocol + hypotheses/ scaffolding + templates
    (hypothesis.md, memo.md, results.json, decision.json).
A5. orchestrator/loop.py: plain Python, no agent framework. Sequence per
    Section 4 via headless `claude` and `codex` CLI calls — VERIFY flags via
    --help at runtime. Hard stops: max hypotheses (default 30), wall-clock
    cap (default 6h), 3 consecutive infra-kills => halt + diagnosis to
    STATE.md, cost ceiling if the CLIs expose usage. Every subprocess call
    logged per Section 7. Acceptance: a DRY-RUN mode using a mocked LLM
    (canned hypothesis + canned signal) completes 2 full cycles, producing
    valid ledger lines, memos, and commits.
    => Codex review gate.
A6. Dockerfile + compose per Section 7. Acceptance: dry-run mode passes
    INSIDE the container; attempting to write engine/ from inside fails;
    attempting to reach pypi.org from inside fails.
    => Codex review gate.
A7. shakedown.sh: runs exactly ONE real hypothesis cycle in the foreground
    with live CLIs, verbose. Built by Claude Code, EXECUTED BY THE HUMAN.
Then output: "GATE H: awaiting human shakedown and SPEC approval" and STOP.
 
## 10. OVERNIGHT RUN PROTOCOL (post-Gate H; for reference during setup)
 
Start: human launches container with run id. Loop: S1->S5 per hypothesis,
commit per stage, hard stops per A5. Morning ritual (human): read git log and
LEDGER before executing anything; review BACKLOG.md; push; decide next run's
SPEC version if changes are needed (changes => new run series, never mid-run).
 
## 11. HUMAN-ONLY ACTIONS (never automate, never simulate)
 
- SPEC.md approval and freeze (Gate H)
- Executing shakedown.sh
- All holdout runs (single-shot, once per promoted idea, ever)
- Morning reviews and all GitHub pushes of overnight work
- Any change to data/ or to this file