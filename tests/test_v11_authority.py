import copy
import json
import sys
from unittest import mock

import pytest

from engine.workspace import writer_lock
from orchestrator import records, rule
from orchestrator.cli_runner import run_logged
from orchestrator.loop import Loop, QuotaHalt
from orchestrator.stages import LlmUsage, QuotaExhausted, StageFailure, parse_codex_usage
from tests.v11_support import BLOCKS, completed, init_workspace, start_hypothesis


@pytest.fixture
def owned(tmp_path):
    init_workspace(tmp_path)
    loop = Loop(tmp_path, "dry-run", "test", 3, 1, 25, 1)
    with writer_lock(tmp_path), mock.patch.object(loop, "_commit"):
        yield tmp_path, loop


def answer(decision, params=None, reason=None):
    return json.dumps({"hypothesis_id": "H001", "iteration": 0, "decision": decision,
                       "kill_reason": reason, "criteria": [], "iterate_params": params,
                       "justification": "fake secondary", "referee_model": "codex gpt-5.6-terra",
                       "timestamp": "test"})


@pytest.mark.parametrize("passing,text,expected,status", [
    (False, answer("PROMOTE"), "ITERATE", "disagree"),
    (True, answer("KILL", reason="merits"), "PROMOTE", "disagree"),
    (False, answer("ITERATE", {"lookback_days": 120}), "ITERATE", "invalid"),
    (False, "{bad json", "ITERATE", "invalid"),
    (False, answer("ITERATE", {"lookback_days": 60}), "ITERATE", "agree"),
    (True, StageFailure("fake timeout"), "PROMOTE", "unavailable"),
    (True, UnicodeDecodeError("utf8", b"\xff", 0, 1, "bad"), "PROMOTE", "unavailable"),
])
def test_rule_authority_over_fake_secondary(owned, passing, text, expected, status):
    root, loop = owned
    hid = start_hypothesis(root)
    eid, result = completed(root, hid, passing=passing)
    path = root / "hypotheses" / hid / "decision.json"
    before_secondary = []
    class Fake:
        def codex_text(self, *a, **kw):
            # The authority is already persisted before the secondary can run.
            before_secondary.append(path.read_bytes())
            assert kw["effort"] == "none" and kw["model"] == "gpt-5.6-terra"
            if isinstance(text, Exception): raise text
            return text
    loop.llm = Fake()
    decision = loop._s4_referee(hid, "card", BLOCKS, result, canned=False)
    assert decision["decision"] == expected
    assert path.read_bytes() == before_secondary[0]
    assert records.bound_decision(root, hid)[1]["evaluation_id"] == eid
    secondary = json.loads((path.parent / "referee_llm.json").read_text())
    assert secondary["status"] == status
    assert secondary["agrees"] is (None if status == "unavailable" else status == "agree")


def test_timeout_cannot_late_write_authoritative_files(owned):
    root, loop = owned
    hid = start_hypothesis(root)
    _, result = completed(root, hid, passing=True)
    path = root / "hypotheses" / hid / "decision.json"
    before = []
    class FakeTimeout:
        def codex_text(self, *a, **kw):
            before.append(path.read_bytes())
            # A local fake child, not a model; subprocess.run kills and waits on timeout.
            child = run_logged(loop.log, [sys.executable, "-c",
                               "import time; time.sleep(30); print('late answer')"],
                               cwd=root, timeout_s=.05, stage="fake-timeout")
            assert child.timed_out and child.stdout == ""
            raise StageFailure("secondary timeout")
    loop.llm = FakeTimeout()
    loop._s4_referee(hid, "card", BLOCKS, result, canned=False)
    assert path.read_bytes() == before[0]
    assert records.load_evaluations(root)[0]["results"] == result
    assert "#1 |" not in (root / "LEDGER.md").read_text()
    assert records._read_json(records.lifecycle_path(root, hid))["state"] == "decided"
    assert loop.usage.referee_unavailable == 1


def test_bad_rule_inputs_never_write_a_verdict(owned):
    root, loop = owned
    hid = start_hypothesis(root)
    _, r = completed(root, hid)
    r["aggregate"]["sharpe_annualized"] = float("inf")
    with pytest.raises(StageFailure, match="rule inputs invalid"):
        loop._s4_referee(hid, "card", BLOCKS, r, canned=True)
    assert not (root / "hypotheses" / hid / "decision.json").exists()


def test_infra_kill_refuses_a_bound_decision(owned):
    root, loop = owned
    hid = start_hypothesis(root)
    _, r = completed(root, hid, passing=True)
    loop._s4_referee(hid, "card", BLOCKS, r, canned=True)
    before = (root / "hypotheses" / hid / "decision.json").read_bytes()
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        loop._infra_kill(1, hid, "test", "memo failed", True)
    assert (root / "hypotheses" / hid / "decision.json").read_bytes() == before


@pytest.mark.parametrize("memo_error", [StageFailure("fake memo failure"), QuotaExhausted("quota exceeded")])
def test_s5_failure_keeps_verdict_and_quota_halts_without_next_cycle(tmp_path, memo_error):
    init_workspace(tmp_path)
    loop = Loop(tmp_path, "dry-run", "test", 4, 1, 25, 1)
    saved = []
    def s1(hid, canned):
        (tmp_path / "hypotheses" / hid / "signal.py").write_text("PARAMS = {'lookback_days':90}\n")
        return "card", copy.deepcopy(BLOCKS)
    def s3(hid):
        _, r = completed(tmp_path, hid, passing=True)
        return r
    def memo(*a):
        saved.append((tmp_path / "hypotheses/H001/decision.json").read_bytes())
        raise memo_error
    with mock.patch.object(loop, "preflight"), mock.patch.object(loop, "_commit"), \
         mock.patch.object(loop, "_s1_hypothesize", side_effect=s1), \
         mock.patch.object(loop, "_s2_implement", return_value=True), \
         mock.patch.object(loop, "_s3_backtest", side_effect=s3), \
         mock.patch.object(loop, "_s5_memo", side_effect=memo):
        assert loop.run() == 0
    assert (tmp_path / "hypotheses/H001/decision.json").read_bytes() == saved[0]
    assert "PROMOTED" in (tmp_path / "LEDGER.md").read_text()
    assert "memo_status: failed" in (tmp_path / "hypotheses/H001/memo.md").read_text()
    summary = json.loads((tmp_path / "runs/test/summary.json").read_text())
    assert summary["memo_failures"] == 1
    if isinstance(memo_error, QuotaExhausted): assert summary["halt_class"] == "quota-exhausted"
    assert len(list((tmp_path / "hypotheses").iterdir())) == 1
    assert records.load_evaluations(tmp_path)[0]["status"] == "completed"


def test_secondary_quota_preserves_decision_and_sets_halt(owned):
    root, loop = owned
    hid = start_hypothesis(root)
    _, r = completed(root, hid, passing=True)
    loop.llm = mock.Mock()
    loop.llm.codex_text.side_effect = QuotaExhausted("quota")
    decision = loop._s4_referee(hid, "card", BLOCKS, r, canned=False)
    assert decision["decision"] == "PROMOTE" and isinstance(loop._secondary_halt, QuotaHalt)
    assert records.bound_decision(root, hid) is not None


def test_usage_unknown_zero_and_observed_subtotals_are_distinct():
    u = LlmUsage()
    u.add_codex(None)
    assert u.codex_tokens_reported() is None
    u.add_codex(1200)
    report = u.usage_summary()
    assert report["codex_tokens"] is None
    assert report["codex_tokens_observed"] == 1200
    assert report["codex_calls_reported"] == 1 and report["codex_calls_total"] == 2
    assert report["codex_usage_complete"] is False
    assert report["codex_call_tokens"] == [None, 1200]
    zero = LlmUsage(); zero.add_codex(0)
    assert zero.codex_tokens_reported() == 0 and zero.usage_summary()["codex_usage_complete"]
    assert parse_codex_usage("", "tokens used: 4,321") == 4321
    assert parse_codex_usage("tokens used 10", "") == 10
    assert parse_codex_usage("no usage", "") is None


def test_hard_stop_usage_reports_completeness(owned):
    _, loop = owned
    loop.usage.add_codex(None); loop.usage.add_codex(7)
    loop.usage.add_claude(100)
    with pytest.raises(Exception, match='"codex_usage_complete": false') as error:
        loop._check_hard_stops()
    assert '"codex_tokens_observed": 7' in str(error.value)


@pytest.mark.parametrize('stop', ['secondary-quota', 'post-decision-budget'])
def test_terminal_stop_finalizes_without_any_later_model_or_engine_call(tmp_path, stop):
    init_workspace(tmp_path)
    loop = Loop(tmp_path, 'dry-run', 'terminal-stop', 4, 1, 25, 4)
    def s1(hid, canned):
        (tmp_path / 'hypotheses' / hid / 'signal.py').write_text("PARAMS = {'lookback_days':90}\n")
        return 'card', copy.deepcopy(BLOCKS)
    def s3(hid):
        return completed(tmp_path, hid, passing=True)[1]
    authoritative = loop._s4_referee
    saved = []
    def s4(hid, card, blocks, result, canned):
        decision = authoritative(hid, card, blocks, result, canned=stop != 'secondary-quota')
        if stop == 'post-decision-budget': loop.usage.claude_cost_usd = 26
        saved.append((tmp_path / 'hypotheses' / hid / 'decision.json').read_bytes())
        return decision
    loop.llm = mock.Mock()
    loop.llm.codex_text.side_effect = QuotaExhausted('quota at optional check')
    with mock.patch.object(loop, 'preflight'), mock.patch.object(loop, '_commit'), \
         mock.patch.object(loop, '_s1_hypothesize', side_effect=s1) as first, \
         mock.patch.object(loop, '_s2_implement', return_value=True), \
         mock.patch.object(loop, '_s3_backtest', side_effect=s3) as engine, \
         mock.patch.object(loop, '_s4_referee', side_effect=s4), \
         mock.patch.object(loop, '_s5_memo', side_effect=AssertionError('must not call S5')) as memo:
        assert loop.run() == 0
    assert first.call_count == engine.call_count == 1 and memo.call_count == 0
    assert loop.llm.codex_text.call_count == int(stop == 'secondary-quota')
    assert (tmp_path / 'hypotheses/H001/decision.json').read_bytes() == saved[0]
    assert records._read_json(records.lifecycle_path(tmp_path, 'H001'))['state'] == 'closed'
    assert (tmp_path / 'PROMOTED_QUEUE.md').read_text().count('H001 |') == 1
    summary = json.loads((tmp_path / 'runs/terminal-stop/summary.json').read_text())
    assert summary['cycles'] == 1 and summary['memo_failures'] == 1
    assert summary['halt_class'] == ('quota-exhausted' if stop == 'secondary-quota' else 'hard-stop')


def test_finalization_uses_referenced_evaluation_not_last_completed(owned):
    root, loop = owned
    hid = start_hypothesis(root)
    first, result = completed(root, hid, passing=True)
    loop._s4_referee(hid, 'card', BLOCKS, result, canned=True)
    # Simulate separate preserved evidence without replacing the authority.
    last, _ = completed(root, hid, passing=False)
    assert last != first
    records.finalize_terminal(root, hid, recovery=True)
    ledger = (root / 'LEDGER.md').read_text()
    assert 'PROMOTED' in ledger and 'sharpe=1.5000' in ledger
    assert records._read_json(records.lifecycle_path(root, hid))['evaluation_id'] == first


def test_cli_decoding_failure_is_an_unknown_usage_call(owned):
    from orchestrator.stages import LiveLlm, StageTimeouts
    root, loop = owned
    client = LiveLlm(loop.log, root, StageTimeouts(), loop.usage)
    failure = UnicodeDecodeError('utf8', b'\xff', 0, 1, 'bad')
    with mock.patch('orchestrator.stages.run_logged', side_effect=failure), pytest.raises(UnicodeDecodeError):
        client.codex_text('fake', 'S4-llm', 1, 'gpt-5.6-terra', 'none')
    assert loop.usage.codex_tokens_reported() is None
    assert loop.usage.codex_calls_total == 1 and loop.usage.codex_calls_reported == 0
    assert json.loads(loop.log.path.read_text().splitlines()[-1])['usage']['codex_tokens'] is None
