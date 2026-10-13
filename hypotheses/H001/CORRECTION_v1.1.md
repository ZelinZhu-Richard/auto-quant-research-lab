# H001 — SPEC v1.1 bookkeeping correction (DRAFT)

Original card, signal, iterations, results, decision and memo are unchanged from
local main `059e0952a367f5e0b4ded60cafd7fd027fa835c5`.
The three preserved S3 results are evidence of three parameter evaluations of
one pre-registered hypothesis; they are correlated, not independent trials.

`E0001`–`E0003` are identifiers assigned during this legacy import. They did not
exist at launch. Each record labels `source: backfill`, the source commit,
`original_evaluation_id: null`, and `binding_status: legacy-unverifiable-at-launch`.
Existing result hashes preserve the historical evidence; they do not establish
v1.1-style input binding at the original launch.

| Import ID | Source commit | Original N | Original DSR | Counterfactual N | Counterfactual DSR |
|---|---|---|---|---|---|
| E0001 | `7528fb96257b3f84056563a966e1a959c5191e16` | 1 | 0.01073640 | 1 | 0.01073640 |
| E0002 | `e3e8a3237aefed2389c507c0786a56b29c4c0162` | 1 | 0.01177214 | 2 | 0.01133941 |
| E0003 | `cf20ab799e15d9a201f4702bbd4a083c9c96b6af` | 1 | 0.07815577 | 3 | 0.03298780 |

The separate counterfactual calculation uses the recorded sufficient statistics
and the chronological set of daily Sharpes. No backtest, model call or calibration
study was run. The old DSR fields and old decision are preserved, not recomputed
in place. This corrected bookkeeping is not evidence of calibrated error control.

Coverage beyond H001 is documented in `MIGRATION.md`. Software acceptance and
historical-migration readiness are separate; research cutover remains blocked.
