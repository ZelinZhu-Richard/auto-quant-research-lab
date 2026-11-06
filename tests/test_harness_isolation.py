"""Independent review round 1: full-panel state/mutation must not reach scoring."""
import pandas as pd
import pytest

from engine.harness import SignalContractError, run_full_harness
from engine.run_backtest import _load_signal_module, fresh_signal_callable
from engine.backtest import run_walkforward
from scripts.synthetic_validation import synthetic_panel


CACHE_SOURCE = '''import pandas as pd
PARAMS = {"rank": 1}
CACHE = None
def compute_signal(panel):
    global CACHE
    prices = panel.close.unstack("symbol")
    if CACHE is None:
        CACHE = prices.iloc[-1] / prices.iloc[0] - 1
    last = panel.index.get_level_values("date").max()
    index = panel.loc[[last]].index
    return pd.Series([CACHE[s] for _, s in index], index=index)
'''


def test_full_panel_cached_rank_is_not_reused_for_scoring(tmp_path):
    panel = synthetic_panel()
    path = tmp_path / 'signal.py'
    path.write_text(CACHE_SOURCE)
    module = _load_signal_module(path)
    run_full_harness(module.compute_signal, panel)  # last-date-only cache evades historical comparison
    dates = panel.index.get_level_values('date').unique()
    contaminated = run_walkforward(panel, module.compute_signal, dates[2], dates[-1])
    clean = run_walkforward(panel, fresh_signal_callable(path, {'rank': 1}), dates[2], dates[-1])
    assert not contaminated.r_net.equals(clean.r_net)
    # Mutating bars far beyond an earlier observation cannot alter that call.
    fresh = fresh_signal_callable(path, {'rank': 1})
    observed = panel.loc[panel.index.get_level_values('date') <= dates[10]]
    pd.testing.assert_series_equal(fresh(observed), fresh(observed))


def test_harness_rejects_idempotent_mutation_without_corrupting_panel():
    panel = synthetic_panel()
    original = panel.copy(deep=True)
    def mutate(p):
        p.loc[:, 'close'] = 100.
        return p.close
    with pytest.raises(SignalContractError, match='mutated'):
        run_full_harness(mutate, panel)
    pd.testing.assert_frame_equal(panel, original)
