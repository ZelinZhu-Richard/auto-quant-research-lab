"""Stage implementations S1-S5 (PROJECT_BRIEF §4, SPEC §14).

Design rule: LLMs AUTHOR content; the orchestrator does every file write.
S1/S5 run `claude -p --output-format json`; S2/S4 run
`codex exec -s read-only --output-last-message`. No LLM process is given
write access to the tree. Iterations never call an LLM: the pre-registered
grid point is patched into signal.py's single PARAMS line mechanically.
"""

import ast
import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator import prompts
from orchestrator.cli_runner import CliResult, ToolCallLog, run_logged


class StageFailure(Exception):
    """Mid-cycle failure => KILLED(reason=infrastructure), loop continues (R4)."""


@dataclass
class LlmUsage:
    claude_cost_usd: float = 0.0
    codex_tokens: int = 0
    codex_cost_known: bool = False
    calls: int = 0

    def add_claude(self, cost: float | None) -> None:
        self.calls += 1
        if cost:
            self.claude_cost_usd += float(cost)

    def add_codex(self, tokens: int | None) -> None:
        self.calls += 1
        if tokens:
            self.codex_tokens += int(tokens)


@dataclass
class StageTimeouts:
    s1: int = 600
    s2: int = 1200
    s3: int = 900
    s4: int = 600
    s5: int = 600


FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(os|sys|subprocess|pathlib|socket|urllib|requests|"
    r"http|shutil|ctypes|pickle|importlib|builtins|io)\b",
    re.M,
)
PARAMS_LINE_RE = re.compile(r"(?m)^PARAMS\s*=\s*(.+)$")


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", text, re.S)
    return match.group(1).strip() if match else text


class LiveLlm:
    """Real CLI calls. Flags were verified against `claude --help` /
    `codex --help` at setup time and are re-verified by loop preflight."""

    def __init__(self, log: ToolCallLog, repo_root: Path, timeouts: StageTimeouts,
                 usage: LlmUsage, claude_budget_usd: float = 2.00):
        self.log = log
        self.repo_root = repo_root
        self.timeouts = timeouts
        self.usage = usage
        self.claude_budget = claude_budget_usd

    def claude_text(self, prompt: str, stage: str, timeout_s: int) -> str:
        result = run_logged(
            self.log,
            [
                "claude", "-p",
                "--output-format", "json",
                "--max-budget-usd", f"{self.claude_budget:.2f}",
                "--no-session-persistence",
                "--tools", "",
            ],
            cwd=self.repo_root,
            timeout_s=timeout_s,
            stage=stage,
            stdin_text=prompt,
        )
        if result.timed_out or result.exit_code != 0:
            raise StageFailure(
                f"{stage}: claude exited {result.exit_code}"
                f"{' (timeout)' if result.timed_out else ''}: {result.stderr[:300]}"
            )
        try:
            payload = json.loads(result.stdout)
            text = payload["result"]
            self.usage.add_claude(payload.get("total_cost_usd"))
        except (json.JSONDecodeError, KeyError) as exc:
            raise StageFailure(f"{stage}: unparseable claude output: {exc}") from exc
        return _strip_fences(text)

    def codex_text(self, prompt: str, stage: str, timeout_s: int) -> str:
        out_file = Path(tempfile.gettempdir()) / f"codex_{stage}_{uuid.uuid4().hex}.md"
        result = run_logged(
            self.log,
            [
                "codex", "exec",
                "-s", "read-only",
                "--skip-git-repo-check",
                "-C", str(self.repo_root),
                "--output-last-message", str(out_file),
                "-",
            ],
            cwd=self.repo_root,
            timeout_s=timeout_s,
            stage=stage,
            stdin_text=prompt,
        )
        tokens = None
        match = re.search(r"tokens used[\s:]*([\d,]+)", result.stdout, re.I)
        if match:
            tokens = int(match.group(1).replace(",", ""))
        self.usage.add_codex(tokens)
        if result.timed_out or result.exit_code != 0:
            raise StageFailure(
                f"{stage}: codex exited {result.exit_code}"
                f"{' (timeout)' if result.timed_out else ''}: {result.stderr[:300]}"
            )
        if not out_file.exists():
            raise StageFailure(f"{stage}: codex produced no output file")
        text = out_file.read_text(encoding="utf-8")
        out_file.unlink(missing_ok=True)
        return _strip_fences(text)


def validate_hypothesis_md(text: str) -> dict:
    """SPEC §8b: the three fenced JSON blocks must parse and be shaped
    correctly. Returns {'params', 'grid', 'criteria'}."""
    blocks = re.findall(r"```json\n(.*?)```", text, re.S)
    if len(blocks) < 3:
        raise StageFailure(f"hypothesis.md has {len(blocks)} JSON blocks, need 3")
    try:
        params, grid, criteria = (json.loads(b) for b in blocks[:3])
    except json.JSONDecodeError as exc:
        raise StageFailure(f"hypothesis.md JSON block unparseable: {exc}") from exc
    if not isinstance(params, dict) or not params:
        raise StageFailure("Parameters block must be a non-empty dict")
    if not isinstance(grid, list) or len(grid) > 2 or not all(
        isinstance(g, dict) and set(g) == set(params) for g in grid
    ):
        raise StageFailure("Iteration grid must be a list of <=2 dicts with the same keys as Parameters")
    required = {"min_sharpe", "max_drawdown", "min_hit_rate", "min_sign_consistent_folds"}
    if not isinstance(criteria, dict) or set(criteria) != required:
        raise StageFailure(f"Kill criteria keys must be exactly {sorted(required)}")
    return {"params": params, "grid": grid, "criteria": criteria}


def validate_signal_source(source: str) -> dict:
    """Static checks before the file touches disk. Returns PARAMS dict."""
    if "def compute_signal" not in source:
        raise StageFailure("signal.py lacks compute_signal")
    banned = FORBIDDEN_IMPORT_RE.search(source)
    if banned:
        raise StageFailure(f"signal.py imports forbidden module: {banned.group(1)}")
    matches = PARAMS_LINE_RE.findall(source)
    if len(matches) != 1:
        raise StageFailure(f"signal.py must contain exactly one PARAMS line, found {len(matches)}")
    try:
        params = ast.literal_eval(matches[0].strip())
    except (ValueError, SyntaxError) as exc:
        raise StageFailure(f"PARAMS line is not a literal dict: {exc}") from exc
    if not isinstance(params, dict):
        raise StageFailure("PARAMS must be a dict literal")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise StageFailure(f"signal.py has a syntax error: {exc}") from exc
    return params


def patch_params_line(signal_path: Path, new_params: dict) -> None:
    """Deterministic iteration step: rewrite the single PARAMS line to the
    next pre-registered grid point. No LLM involved."""
    source = signal_path.read_text(encoding="utf-8")
    if len(PARAMS_LINE_RE.findall(source)) != 1:
        raise StageFailure("cannot patch PARAMS: line missing or duplicated")
    patched = PARAMS_LINE_RE.sub(f"PARAMS = {json.dumps(new_params)}", source, count=1)
    signal_path.write_text(patched, encoding="utf-8")


def validate_decision(text: str, declared_grid: list[dict],
                      iteration_history: list[dict]) -> dict:
    try:
        decision = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StageFailure(f"decision.json unparseable: {exc}") from exc
    if decision.get("decision") not in {"KILL", "ITERATE", "PROMOTE"}:
        raise StageFailure(f"invalid decision {decision.get('decision')!r}")
    # SPEC §10: the referee NEVER writes "infrastructure" — that value is
    # reserved for the orchestrator path when a stage fails.
    if decision.get("kill_reason") not in {"merits", None}:
        raise StageFailure(f"invalid referee kill_reason {decision.get('kill_reason')!r}")
    if not isinstance(decision.get("criteria"), list):
        raise StageFailure("decision.criteria must be a list")
    if not str(decision.get("justification", "")).strip():
        raise StageFailure("decision.justification is empty")
    if decision["decision"] == "ITERATE":
        tried = [h["params"] for h in iteration_history]
        untried = [g for g in declared_grid if g not in tried]
        if not untried:
            raise StageFailure("ITERATE with an exhausted grid")
        if decision.get("iterate_params") != untried[0]:
            raise StageFailure(
                f"ITERATE must name the FIRST untried grid point {untried[0]}, "
                f"got {decision.get('iterate_params')}"
            )
    return decision


def run_hypothesis_tests(log: ToolCallLog, repo_root: Path, hypothesis_id: str,
                         timeout_s: int) -> CliResult:
    return run_logged(
        log,
        ["env", f"QUANTLAB_HYPOTHESIS={hypothesis_id}",
         "uv", "run", "pytest", "tests/hypothesis_harness.py", "-q",
         "--no-header", "-x"],
        cwd=repo_root,
        timeout_s=timeout_s,
        stage="S2-tests",
    )


def run_backtest(log: ToolCallLog, repo_root: Path, hypothesis_id: str,
                 timeout_s: int) -> CliResult:
    return run_logged(
        log,
        ["uv", "run", "python", "-m", "engine.run_backtest",
         "--hypothesis", hypothesis_id, "--repo-root", str(repo_root)],
        cwd=repo_root,
        timeout_s=timeout_s,
        stage="S3-backtest",
    )
