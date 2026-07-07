"""Guards added at the A2 review gate: unsorted panels and calendar gaps
must fail loudly, never silently distort the no-lookahead cut or metrics."""

import pandas as pd
import pytest

from engine.backtest import run_walkforward
from engine.errors import EngineError
from tests.test_known_answer import D1, D2, _panel, toy_signal


def test_unsorted_panel_refused():
    panel = _panel().iloc[::-1]  # reverse order — positional cut would break
    with pytest.raises(EngineError, match="not sorted"):
        run_walkforward(panel, toy_signal, start=D1, end=D2)


def test_interior_calendar_gap_refused():
    panel = _panel()
    dates = panel.index.get_level_values("date")
    gapped = panel[dates != D1]  # drop the middle day entirely
    # D1 missing: as a return day (window shrinks) and as D2's signal date
    with pytest.raises(EngineError, match="calendar"):
        run_walkforward(gapped, toy_signal, start=D1, end=D2)
    with pytest.raises(EngineError, match="calendar"):
        run_walkforward(gapped, toy_signal, start=D2, end=D2)


def test_terminal_calendar_gap_refused():
    panel = _panel()
    dates = panel.index.get_level_values("date")
    truncated = panel[dates != D2]  # requested end date absent from panel
    with pytest.raises(EngineError, match="calendar"):
        run_walkforward(truncated, toy_signal, start=D1, end=D2)
