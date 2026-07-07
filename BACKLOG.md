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
