"""Engine constants fixed by SPEC.md. Not configurable by hypotheses."""

import pandas as pd

COST_PER_SIDE = 0.0025  # 25 bps per side on turnover (SPEC §4)

# All windows below are stated in RETURN DAYS (SPEC §3 terminology);
# signal dates run one day earlier.
BURN_IN_START = pd.Timestamp("2022-07-01", tz="UTC")
AGGREGATE_START = pd.Timestamp("2022-11-01", tz="UTC")
AGGREGATE_END = pd.Timestamp("2025-06-30", tz="UTC")

FOLDS = (
    (pd.Timestamp("2022-11-01", tz="UTC"), pd.Timestamp("2023-06-30", tz="UTC")),
    (pd.Timestamp("2023-07-01", tz="UTC"), pd.Timestamp("2024-02-29", tz="UTC")),
    (pd.Timestamp("2024-03-01", tz="UTC"), pd.Timestamp("2024-10-31", tz="UTC")),
    (pd.Timestamp("2024-11-01", tz="UTC"), pd.Timestamp("2025-06-30", tz="UTC")),
)

ANNUALIZATION_DAYS = 365  # crypto trades every calendar day (SPEC §6)
MIN_ASSETS_PER_LEG = 3    # q < 3 => no positions that date (SPEC §3)
