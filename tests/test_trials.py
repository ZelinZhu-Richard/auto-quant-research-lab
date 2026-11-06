import pytest

from engine.errors import EngineError
from engine.trials import TrialJournal


def identity(params=90):
    return {
        "hypothesis_id": "H001",
        "params": {"lookback_days": params},
        "code": "a",
        "data": "b",
    }


def score(value=0.1):
    return {"aggregate": {"sharpe_daily": value}, "daily": [value]}


def test_all_configurations_and_exact_replay(tmp_path):
    journal = TrialJournal(tmp_path)
    with journal.writer():
        for setting in [90, 60, 120, 120]:
            journal.finish(
                journal.start(identity(setting), "commit"), score(setting / 1000)
            )
    a = journal.accounting()
    assert (
        a["attempted"],
        a["successfully_scored_attempts"],
        a["unique_scored"],
        a["n_trials"],
    ) == (4, 4, 3, 3)
    assert a["effective_independent_trials"] is None
    assert a["pending_attempts"] == 0


def test_failure_crash_and_retry_are_retained(tmp_path):
    j = TrialJournal(tmp_path)
    with j.writer():
        j.fail(j.start(identity(), "a"), "missing price")
        j.start(identity(60), "a")  # abrupt death: no terminal receipt
        j.finish(j.start(identity(), "a"), score())
    a = j.accounting()
    assert a["attempted"] == 3 and a["n_trials"] == 2 and a["pending_attempts"] == 1
    assert a["unique_scored"] == 1


def test_replay_different_score_cannot_overwrite(tmp_path):
    j = TrialJournal(tmp_path)
    with j.writer():
        j.finish(j.start(identity(), "a"), score())
        with pytest.raises(EngineError, match="immutable"):
            j.finish(j.start(identity(), "b"), score(0.2))
    assert j.accounting()["pending_attempts"] == 1


def test_corruption_and_middle_deletion_detected(tmp_path):
    j = TrialJournal(tmp_path)
    with j.writer():
        j.finish(j.start(identity(), "a"), score())
    p = tmp_path / "trial_evidence/events/00000001.json"
    original = p.read_bytes()
    p.write_text(original.decode().replace("90", "80"))
    with pytest.raises(EngineError, match="hash"):
        j.accounting()
    p.write_bytes(original)
    p.unlink()
    with pytest.raises(EngineError, match="discontinuity"):
        j.accounting()


def test_concurrent_writer_refused(tmp_path):
    with TrialJournal(tmp_path).writer():
        with pytest.raises(EngineError, match="another evaluation"):
            with TrialJournal(tmp_path).writer():
                pass


def test_legacy_ledger_cannot_be_ignored(tmp_path):
    (tmp_path / "LEDGER.md").write_text("#1 | old | KILLED | sharpe=1\n")
    with pytest.raises(EngineError, match="inventory"):
        TrialJournal(tmp_path).accounting()


def test_reconciled_crash_can_resume_without_double_counting(tmp_path):
    j = TrialJournal(tmp_path)
    with j.writer():
        start = j.start(identity(), 'a')
    assert j.accounting()['pending_attempts'] == 1
    with j.writer():
        j.fail(start, 'human reconciliation: crash before usable score')
        j.finish(j.start(identity(), 'a'), score())
    a = j.accounting()
    assert a['attempted'] == 2 and a['n_trials'] == 1
    assert a['pending_attempts'] == 0 and a['unique_scored'] == 1
