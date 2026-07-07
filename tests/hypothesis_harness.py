"""S2 test suite for a hypothesis signal, driven by the shared harness.

The orchestrator (and shakedown.sh) runs:

    QUANTLAB_HYPOTHESIS=H001 uv run pytest tests/hypothesis_harness.py -q

against the REAL train_val panel. Without the env var the module skips
(normal `pytest tests/` runs are unaffected). Red result here => one repair
attempt => infra-kill (R4).
"""

import importlib.util
import os
from pathlib import Path

import pytest

from engine.harness import (
    assert_deterministic,
    assert_index_alignment,
    assert_nan_handling,
    assert_no_lookahead,
)
from engine.loader import load_panel

REPO_ROOT = Path(__file__).parent.parent
HYPOTHESIS_ID = os.environ.get("QUANTLAB_HYPOTHESIS", "")

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_ID, reason="set QUANTLAB_HYPOTHESIS=H### to run"
)


@pytest.fixture(scope="module")
def compute_signal():
    signal_path = REPO_ROOT / "hypotheses" / HYPOTHESIS_ID / "signal.py"
    assert signal_path.exists(), f"{signal_path} not found"
    spec = importlib.util.spec_from_file_location("hypothesis_signal", signal_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "compute_signal"), "signal.py lacks compute_signal"
    return module.compute_signal


@pytest.fixture(scope="module")
def panel():
    return load_panel(REPO_ROOT / "data" / "train_val")


def test_index_alignment(compute_signal, panel):
    assert_index_alignment(compute_signal, panel)


def test_determinism(compute_signal, panel):
    assert_deterministic(compute_signal, panel)


def test_nan_handling(compute_signal, panel):
    assert_nan_handling(compute_signal, panel)


def test_no_lookahead(compute_signal, panel):
    assert_no_lookahead(compute_signal, panel)
