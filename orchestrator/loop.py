"""Overnight research loop (A5). Plain Python, no agent framework.

    uv run python -m orchestrator.loop --mode dry-run --max-cycles 2
    uv run python -m orchestrator.loop --mode live --run-id overnight_01

Sequences S1->S5 per PROJECT_BRIEF §4 / SPEC §14 with:
- hard stops (SPEC §12): max hypotheses, wall clock, 3 consecutive
  infra-kills, claude cost ceiling — all checked between stages;
- kill-and-continue (R4): any stage failure => KILLED(infrastructure),
  STATE.md reset, next hypothesis; one repair attempt max on red tests;
- per-stage local commits (R6: the loop NEVER pushes);
- R5 self-enforcement: refuses to commit if a protected path changed;
- full toolcall instrumentation to runs/<run_id>/toolcalls.jsonl (§7).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from orchestrator import dryrun, prompts, state
from orchestrator.cli_runner import ToolCallLog, run_logged
from orchestrator.stages import (
    LiveLlm,
    LlmUsage,
    StageFailure,
    StageTimeouts,
    patch_params_line,
    run_backtest,
    run_hypothesis_tests,
    validate_decision,
    validate_hypothesis_md,
    validate_signal_source,
)

# Paths the loop must never modify (R5 + frozen/human-only files).
PROTECTED_PREFIXES = ("engine/", "orchestrator/", "data/", "tests/", "templates/",
                      "scripts/", "docker-compose")
PROTECTED_FILES = ("Dockerfile", "pyproject.toml", "uv.lock", "SPEC.md",
                   "PROJECT_BRIEF.md", "BACKLOG.md", "SETUP_NOTES.md",
                   "data_pipeline.py", "conftest.py", ".gitignore")


class HardStop(Exception):
    """Clean halt: write diagnosis to STATE.md, commit, exit 0."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Loop:
    def __init__(self, repo_root: Path, mode: str, run_id: str,
                 max_hypotheses: int, wall_clock_hours: float,
                 cost_ceiling_usd: float, max_cycles: int | None):
        self.repo_root = repo_root
        self.mode = mode
        self.run_id = run_id
        self.max_hypotheses = max_hypotheses
        self.deadline = time.time() + wall_clock_hours * 3600
        self.cost_ceiling = cost_ceiling_usd
        self.max_cycles = max_cycles
        self.run_dir = repo_root / "runs" / run_id
        self.log = ToolCallLog(self.run_dir)
        self.usage = LlmUsage()
        self.timeouts = StageTimeouts()
        self.consecutive_infra = 0
        self.cycles_done = 0
        self.llm = LiveLlm(self.log, repo_root, self.timeouts, self.usage) \
            if mode == "live" else None
        self.phase = "overnight" if mode == "live" else "dry-run"

    # ---------- infrastructure ----------

    def _mock_log(self, stage: str, what: str) -> None:
        self.log.append({"ts": _now_iso(), "stage": stage, "command": ["MOCK", what],
                         "cwd": str(self.repo_root), "duration_s": 0.0,
                         "exit_code": 0, "timed_out": False, "mock": True})

    def _state(self, hid: str, stage: str, iteration: int | None = None,
               error: str = "") -> None:
        state.write_state(self.repo_root, self.phase, hid, stage, iteration,
                          error, self.consecutive_infra)

    def _guard_protected_paths(self) -> None:
        result = run_logged(self.log, ["git", "status", "--porcelain"],
                            cwd=self.repo_root, timeout_s=60, stage="git")
        if result.exit_code != 0:
            raise HardStop(f"git status failed: {result.stderr[:200]}")
        touched = []
        for line in result.stdout.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path.startswith(PROTECTED_PREFIXES) or path in PROTECTED_FILES:
                touched.append(path)
        if touched:
            raise HardStop(
                f"R5 violation: protected paths modified during run: {touched}. "
                "Halting for human review; nothing committed."
            )

    def _commit(self, message: str) -> None:
        self._guard_protected_paths()
        run_logged(self.log, ["git", "add", "-A"], cwd=self.repo_root,
                   timeout_s=120, stage="git")
        result = run_logged(self.log, ["git", "commit", "-m", message],
                            cwd=self.repo_root, timeout_s=120, stage="git")
        if result.exit_code != 0 and "nothing to commit" not in result.stdout:
            raise HardStop(f"git commit failed: {result.stderr[:300]}")
        # R6: never push. Morning human reviews and pushes.

    def _check_hard_stops(self) -> None:
        if time.time() > self.deadline:
            raise HardStop("wall-clock cap reached")
        if self.cycles_done >= self.max_hypotheses:
            raise HardStop(f"max hypotheses ({self.max_hypotheses}) reached")
        if self.max_cycles is not None and self.cycles_done >= self.max_cycles:
            raise HardStop(f"max cycles ({self.max_cycles}) reached")
        if self.consecutive_infra >= 3:
            raise HardStop(
                "3 consecutive infrastructure kills — halting for diagnosis. "
                "Check runs/ logs; the failure signature repeats."
            )
        if self.usage.claude_cost_usd > self.cost_ceiling:
            raise HardStop(
                f"LLM cost ceiling exceeded: ${self.usage.claude_cost_usd:.2f} "
                f"> ${self.cost_ceiling:.2f} (claude-reported; codex spend "
                f"tracked as tokens={self.usage.codex_tokens})"
            )

    def preflight(self) -> None:
        if (self.repo_root / "data" / "holdout").exists():
            print("FATAL: data/holdout exists inside the repo. R2 requires it "
                  "physically absent. STOPPING — alert the human.", file=sys.stderr)
            raise SystemExit(2)
        for required in ("SPEC.md", "LEDGER.md", "templates/hypothesis.md",
                         "templates/memo.md"):
            if not (self.repo_root / required).exists():
                raise SystemExit(f"preflight: missing {required}")
        if not (self.repo_root / "data" / "train_val").exists():
            raise SystemExit("preflight: data/train_val missing")
        if self.mode == "live":
            # Verify CLI flags at runtime (PROJECT_BRIEF §8 / A5): the exact
            # flags this loop uses must appear in --help output.
            claude_help = run_logged(self.log, ["claude", "--help"],
                                     cwd=self.repo_root, timeout_s=120,
                                     stage="preflight")
            # every flag LiveLlm.claude_text actually passes
            for flag in ("-p, --print", "--output-format", "--max-budget-usd",
                         "--no-session-persistence", "--tools"):
                if flag not in claude_help.stdout:
                    raise SystemExit(f"preflight: claude --help lacks {flag!r}")
            codex_help = run_logged(self.log, ["codex", "exec", "--help"],
                                    cwd=self.repo_root, timeout_s=120,
                                    stage="preflight")
            # every flag LiveLlm.codex_text actually passes
            for flag in ("--output-last-message", "--skip-git-repo-check",
                         "-s, --sandbox", "-C, --cd"):
                if flag not in codex_help.stdout:
                    raise SystemExit(f"preflight: codex exec --help lacks {flag!r}")

    # ---------- stages ----------

    def _s1_hypothesize(self, hid: str, canned: dict | None) -> tuple[str, dict]:
        self._state(hid, "S1")
        template = (self.repo_root / "templates" / "hypothesis.md").read_text()
        if canned:
            self._mock_log("S1", f"canned hypothesis {canned['name']}")
            text = dryrun.render(canned["hypothesis_md"], hid=hid, ts=_now_iso())
        else:
            prompt = prompts.s1_hypothesize(
                (self.repo_root / "SPEC.md").read_text(),
                (self.repo_root / "STATE.md").read_text(),
                state.ledger_tail(self.repo_root),
                template, hid,
            )
            text = self.llm.claude_text(prompt, "S1", self.timeouts.s1)
        hyp_dir = self.repo_root / "hypotheses" / hid
        hyp_dir.mkdir(parents=True, exist_ok=True)
        (hyp_dir / "hypothesis.md").write_text(text, encoding="utf-8")
        # R4: the single repair attempt exists for red S2 tests ONLY.
        # An invalid card is a mid-cycle failure => infra-kill, no retry.
        blocks = validate_hypothesis_md(text)
        self._commit(f"{hid}/S1: hypothesize")
        return text, blocks

    def _s2_implement(self, hid: str, hypothesis_md: str, blocks: dict,
                      canned: dict | None) -> bool:
        self._state(hid, "S2")
        hyp_dir = self.repo_root / "hypotheses" / hid
        if canned:
            self._mock_log("S2", f"canned signal {canned['name']}")
            source = canned["signal_py"]
        else:
            spec2 = (self.repo_root / "SPEC.md").read_text()
            source = self.llm.codex_text(
                prompts.s2_implement(spec2, hypothesis_md), "S2", self.timeouts.s2)
        signal_params = validate_signal_source(source)
        if signal_params != blocks["params"]:
            # R3: results must run exactly the pre-registered iteration-0
            # params; a divergent PARAMS line is a mid-cycle failure.
            raise StageFailure(
                f"S2 PARAMS {signal_params} != pre-registered iteration-0 "
                f"params {blocks['params']}")
        (hyp_dir / "signal.py").write_text(source, encoding="utf-8")
        (hyp_dir / "iterations.json").write_text(
            json.dumps([{"iteration": 0, "params": blocks["params"]}]) + "\n")

        tests = run_hypothesis_tests(self.log, self.repo_root, hid, self.timeouts.s2)
        if tests.exit_code != 0:
            if canned:
                raise StageFailure(f"S2 tests red in dry-run: {tests.stdout[-500:]}")
            # single repair attempt (R4)
            source = self.llm.codex_text(
                prompts.s2_implement(
                    (self.repo_root / "SPEC.md").read_text(), hypothesis_md,
                    repair_context=(tests.stdout + tests.stderr)[-3000:],
                ),
                "S2-repair", self.timeouts.s2,
            )
            repaired_params = validate_signal_source(source)
            if repaired_params != blocks["params"]:
                raise StageFailure(
                    f"S2 repair changed PARAMS to {repaired_params} != "
                    f"pre-registered {blocks['params']} (R3)")
            (hyp_dir / "signal.py").write_text(source, encoding="utf-8")
            tests = run_hypothesis_tests(self.log, self.repo_root, hid,
                                         self.timeouts.s2)
            if tests.exit_code != 0:
                raise StageFailure(
                    f"S2 tests red after the one repair attempt: "
                    f"{(tests.stdout + tests.stderr)[-500:]}"
                )
        self._commit(f"{hid}/S2: implement (tests=pass)")
        return True

    def _s3_backtest(self, hid: str) -> dict:
        self._state(hid, "S3")
        result = run_backtest(self.log, self.repo_root, hid, self.timeouts.s3)
        results_path = self.repo_root / "hypotheses" / hid / "results.json"
        if result.exit_code != 0 or not results_path.exists():
            raise StageFailure(
                f"S3 backtest failed (exit {result.exit_code}): "
                f"{(result.stderr or result.stdout)[-500:]}"
            )
        results = json.loads(results_path.read_text())
        if results.get("status") != "ok":
            raise StageFailure(f"S3 results status: {results.get('status')}")
        self._commit(f"{hid}/S3: backtest")
        return results

    def _s4_referee(self, hid: str, hypothesis_md: str, blocks: dict,
                    results: dict, canned: bool) -> dict:
        self._state(hid, "S4", iteration=results.get("iteration"))
        if canned:
            self._mock_log("S4", "mechanical referee")
            # same validation path as live output — nothing bypasses it
            decision = validate_decision(
                json.dumps(dryrun.mechanical_referee(blocks, results)),
                blocks["grid"], results["iteration_history"])
        else:
            results_text = json.dumps(results, indent=2)
            text = self.llm.codex_text(
                prompts.s4_referee((self.repo_root / "SPEC.md").read_text(),
                                   hypothesis_md, results_text),
                "S4", self.timeouts.s4)
            # R4: invalid referee output is a mid-cycle failure => infra-kill
            decision = validate_decision(text, blocks["grid"],
                                         results["iteration_history"])
        (self.repo_root / "hypotheses" / hid / "decision.json").write_text(
            json.dumps(decision, indent=2) + "\n", encoding="utf-8")
        self._commit(f"{hid}/S4: referee ({decision['decision']})")
        return decision

    def _s5_memo(self, hid: str, hypothesis_md: str, results: dict,
                 decision: dict, canned: bool) -> None:
        self._state(hid, "S5")
        hyp_dir = self.repo_root / "hypotheses" / hid
        if canned:
            self._mock_log("S5", "canned memo")
            memo = dryrun.canned_memo(hypothesis_md, results, decision)
        else:
            # R4: an S5 failure is a mid-cycle failure => infra-kill (the
            # infra-kill path writes its own memo, so every outcome still
            # gets one)
            memo = self.llm.claude_text(
                prompts.s5_memo(
                    (self.repo_root / "templates" / "memo.md").read_text(),
                    hypothesis_md, json.dumps(results, indent=2),
                    json.dumps(decision, indent=2)),
                "S5", self.timeouts.s5)
        (hyp_dir / "memo.md").write_text(memo, encoding="utf-8")
        self._commit(f"{hid}/S5: memo")

    # ---------- cycle ----------

    def _hypothesis_name(self, hypothesis_md: str, hid: str) -> str:
        match = re.search(rf"#\s*{hid}\s*—\s*(\S+)", hypothesis_md)
        return match.group(1) if match else "unnamed"

    def _close_cycle(self, number: int, hid: str, name: str, decision: dict,
                     results: dict | None, tests_pass: bool,
                     iterations_run: int) -> None:
        agg = (results or {}).get("aggregate", {})
        ref = (results or {}).get("referee_inputs", {})
        label = {"PROMOTE": "PROMOTED", "KILL": f"KILLED({decision.get('kill_reason')})"}[
            decision["decision"]]
        if iterations_run > 0:
            label = f"ITERATED->{label}"
        line = state.append_ledger(
            self.repo_root, number, name, label,
            agg.get("sharpe_annualized"), ref.get("deflated_sharpe"),
            agg.get("max_drawdown"), agg.get("hit_rate"), tests_pass,
        )
        if decision["decision"] == "PROMOTE":
            state.append_promoted(self.repo_root, hid, name)
        self._state(hid, "done", error="")
        self._commit(f"{hid}: ledger — {label}")
        print(f"  ledger: {line}")

    def _infra_kill(self, number: int, hid: str, name: str, error: str,
                    tests_pass: bool) -> None:
        hyp_dir = self.repo_root / "hypotheses" / hid
        hyp_dir.mkdir(parents=True, exist_ok=True)
        decision = {
            "hypothesis_id": hid, "iteration": None, "decision": "KILL",
            "kill_reason": "infrastructure", "criteria": [],
            "iterate_params": None, "justification": error[:500],
            "referee_model": "orchestrator", "timestamp": _now_iso(),
        }
        (hyp_dir / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")
        memo = (f"# Memo — {hid} {name}\n\ndecision: KILL(infrastructure)\n"
                f"date: {_now_iso()}\n\n## What broke\n\n{error[:1000]}\n\n"
                f"(orchestrator-written memo; no merit judgment was made — R4)\n")
        (hyp_dir / "memo.md").write_text(memo)
        state.append_ledger(self.repo_root, number, name,
                            "KILLED(infrastructure)", None, None, None, None,
                            tests_pass)
        self.consecutive_infra += 1
        self._state(hid, "infra-killed", error=error[:200])
        self._commit(f"{hid}: infra-kill — {error[:60]}")
        print(f"  INFRA-KILL {hid}: {error[:120]}")

    def run_cycle(self) -> None:
        number = state.next_hypothesis_number(self.repo_root)
        hid = f"H{number:03d}"
        canned = None
        if self.mode == "dry-run":
            canned = dryrun.CANNED[self.cycles_done % len(dryrun.CANNED)]
        name, tests_pass = "unknown", False
        print(f"[{_now_iso()}] cycle {self.cycles_done + 1}: {hid} "
              f"({'dry-run' if canned else 'live'})")
        try:
            hypothesis_md, blocks = self._s1_hypothesize(hid, canned)
            name = self._hypothesis_name(hypothesis_md, hid)
            self._check_hard_stops()
            tests_pass = self._s2_implement(hid, hypothesis_md, blocks, canned)
            self._check_hard_stops()

            iterations_run = 0
            while True:
                results = self._s3_backtest(hid)
                self._check_hard_stops()
                decision = self._s4_referee(hid, hypothesis_md, blocks, results,
                                            canned is not None)
                if decision["decision"] != "ITERATE":
                    break
                iterations_run += 1
                iteration_entry = {
                    "iteration": results["iteration"] + 1,
                    "params": decision["iterate_params"],
                }
                iterations_path = self.repo_root / "hypotheses" / hid / "iterations.json"
                history = json.loads(iterations_path.read_text())
                history.append(iteration_entry)
                iterations_path.write_text(json.dumps(history) + "\n")
                patch_params_line(
                    self.repo_root / "hypotheses" / hid / "signal.py",
                    decision["iterate_params"])
                # params changed => re-run the harness before re-backtesting
                tests = run_hypothesis_tests(self.log, self.repo_root, hid,
                                             self.timeouts.s2)
                if tests.exit_code != 0:
                    raise StageFailure(
                        f"harness red after iteration patch: "
                        f"{(tests.stdout + tests.stderr)[-400:]}")
                self._commit(f"{hid}/S4: iterate -> {decision['iterate_params']}")

            self._s5_memo(hid, hypothesis_md, results, decision,
                          canned is not None)
            self._close_cycle(number, hid, name, decision, results, tests_pass,
                              iterations_run)
            self.consecutive_infra = 0
        except StageFailure as exc:
            self._infra_kill(number, hid, name, str(exc), tests_pass)
        self.cycles_done += 1
        # SPEC §12: a hard stop tripped between stages propagates as
        # HardStop out of run_cycle — STATE.md records the reason, the run
        # commits and exits 0. The in-flight hypothesis gets NO ledger line
        # (it was not judged); its id is reused by the next run.

    def run(self) -> int:
        self.preflight()
        self._state("none", "starting")
        halt_reason = "completed"
        try:
            while True:
                self._check_hard_stops()
                self.run_cycle()
        except HardStop as stop:
            halt_reason = str(stop)
        summary = {
            "run_id": self.run_id, "mode": self.mode,
            "cycles": self.cycles_done, "halt_reason": halt_reason,
            "claude_cost_usd": round(self.usage.claude_cost_usd, 4),
            "codex_tokens": self.usage.codex_tokens,
            "llm_calls": self.usage.calls,
            "consecutive_infra_at_halt": self.consecutive_infra,
            "finished_at": _now_iso(),
        }
        (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        state.write_state(self.repo_root, "idle", "none", "halted", None,
                          halt_reason[:200], self.consecutive_infra)
        # NO run-end commit here — deliberately. The toolcall logger records
        # every subprocess call including the orchestrator's own git
        # commits, so a commit made here would be described by log lines
        # written AFTER it completes: the logger cannot capture its own
        # last act, and the orphaned lines would dirty the tree until the
        # next run's `git add -A` swept them into the wrong hypothesis's
        # first commit. The LAUNCHER (scripts/shakedown.sh, the compose
        # `lab` command) finalizes after this process exits:
        #   git add runs/ STATE.md && git commit -m "run <id>: toolcall log finalized"
        # That commit is unlogged by construction, so nothing escapes.
        # (SPEC §12's "commit locally" on hard stop is thereby satisfied at
        # the launcher level; STATE.md is included because the idle-state
        # write above also lands after the last in-cycle commit.)
        print(f"halt: {halt_reason} after {self.cycles_done} cycle(s)")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "dry-run"], required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-id",
                        default=time.strftime("%Y%m%d_%H%M%S", time.gmtime()))
    parser.add_argument("--max-hypotheses", type=int, default=30)
    parser.add_argument("--wall-clock-hours", type=float, default=6.0)
    parser.add_argument("--cost-ceiling-usd", type=float, default=25.0)
    parser.add_argument("--max-cycles", type=int, default=None,
                        help="dry-run acceptance uses 2")
    args = parser.parse_args(argv)
    loop = Loop(Path(args.repo_root).resolve(), args.mode, args.run_id,
                args.max_hypotheses, args.wall_clock_hours,
                args.cost_ceiling_usd, args.max_cycles)
    return loop.run()


if __name__ == "__main__":
    raise SystemExit(main())
