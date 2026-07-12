# BACKLOG.md — append-only suggestions. NEVER read during overnight runs.
# Human promotes items to SPEC.md manually.

- [2026-07-07 | setup] data_pipeline.py `_fetch_full_history` never
  terminates cleanly for actively-trading symbols: the pagination advances
  `since` past "now" and Coinbase Exchange raises
  `ExchangeError("Start cannot be in the future")` instead of returning an
  empty batch, so the symbol is logged FAILED and its data discarded
  (observed ok=2/394). data_pipeline.py is protected, so the fix ships as a
  wrapper (scripts/fetch_train_val_data.py) that returns [] for exactly that
  error. Suggested upstream fix: catch that error in the pagination loop, or
  cap the request window at now.
- [2026-07-07 | setup] data_pipeline.py marks 218 symbols "empty" that are
  really "listed after the first 300-day request window" — the loop does not
  advance the window past an empty batch. Harmless here (those symbols are
  universe-ineligible by the listed-within-30-days rule) but the report
  label is misleading.
- [2026-07-11 | model pinning] S2/S4 codex MODELS are now pinned in
  orchestrator/stages.py (gpt-5.6-sol xhigh; gpt-5.6-terra) per the human
  directive, but S4's REASONING EFFORT was not named in the directive and
  is deliberately left unpinned — it still inherits from the mounted
  ~/.codex config (currently "ultra"; live-probed OK with terra on
  2026-07-11). Same vendor-rewrite drift class as the model identity.
  Suggest the human name an S4 effort value to pin via an R5-gated change.
  S1/S5 (claude) are likewise unpinned by flag; summary.json records what
  the CLI reports it used each run.
- [2026-07-08 | setup | A6] The egress allowlist holds exactly the two
  VERIFIED domains (api.anthropic.com; chatgpt.com for codex ChatGPT-OAuth
  mode, observed live). If an overnight run halts because codex needs a
  token-refresh domain (suspected but UNVERIFIED: auth.openai.com), the
  failure will be visible in STATE.md/toolcalls; add the observed domain to
  docker/proxy/filter via an R5 review gate. Do not pre-add unverified
  domains.
- [2026-07-07 | setup | protocol interpretation] A1 review round 2 FAILed
  with three NEW findings (not repeats), all of which Claude agreed with
  and fixed. Section 8's "if still FAIL: STOP, await human arbitration"
  was read as targeting UNRESOLVED findings — a Claude-vs-Codex deadlock
  where "both positions" exist — not fresh, uncontested findings on new
  content. Claude therefore fixed and re-ran the gate once more instead of
  halting the session. If the human reads Section 8 stricter than this,
  say so and the loop's gate handler will be tightened to match.
  [RESOLVED 2026-07-11: interpretation RATIFIED by the human; codified as
  the canonical gate procedure in SETUP_NOTES.md.]
