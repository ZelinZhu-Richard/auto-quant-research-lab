"""Prompt builders for the four LLM stages. Per-cycle context budget is
fixed by PROJECT_BRIEF §5: SPEC.md + STATE.md + last 30 LEDGER lines +
current hypothesis directory. Nothing else."""

import re
from pathlib import Path


def _spec_sections(spec_text: str, wanted: tuple[str, ...]) -> str:
    """Extract '## N.' sections from SPEC.md by number (e.g. '8', '8b')."""
    parts = re.split(r"(?m)^## ", spec_text)
    keep = [p for p in parts if any(p.startswith(f"{w}.") or p.startswith(f"{w} ") for w in wanted)]
    return "\n\n".join("## " + p.strip() for p in keep)


def s1_hypothesize(spec: str, state: str, ledger_tail: str, template: str,
                   hypothesis_id: str) -> str:
    return f"""You are the HYPOTHESIZE stage (S1) of an autonomous quant research loop.

Write a complete hypothesis card for {hypothesis_id} following the template
EXACTLY (keep every heading; replace every {{placeholder}}). Rules:
- The three fenced JSON blocks (Parameters, Iteration grid, Kill criteria)
  are machine-parsed. They must be valid JSON in exactly the template's shape.
- Kill criteria are PRE-REGISTERED and frozen once written (R3). Defaults:
  min_sharpe 1.0, max_drawdown 0.30, min_hit_rate 0.50,
  min_sign_consistent_folds 3. Tune only with one written sentence of
  justification.
- Signals may use only open/high/low/close/volume of the 50-coin panel,
  max lookback 120 calendar days, cross-sectional long-short (see SPEC).
- Read the ledger tail below and do NOT duplicate a killed idea.
- Economic rationale MUST name who is on the other side of the trade.

Output ONLY the completed hypothesis.md content. No preamble, no fences.

=== SPEC.md ===
{spec}

=== STATE.md ===
{state}

=== last 30 LEDGER lines ===
{ledger_tail}

=== template ===
{template}
"""


def s2_implement(spec: str, hypothesis_md: str, repair_context: str = "") -> str:
    repair = ""
    if repair_context:
        repair = f"""
A previous attempt FAILED its tests. This is the single permitted repair
attempt (R4). Failure output:

{repair_context}

Fix the failure. Output the complete corrected file.
"""
    return f"""You are the IMPLEMENT stage (S2) of an autonomous quant research loop.

Write signal.py implementing the hypothesis below against this exact contract
(SPEC §2):

  def compute_signal(panel: pd.DataFrame) -> pd.Series

- panel: MultiIndex (date, symbol) DataFrame, lexsorted, columns
  open/high/low/close/volume, containing only rows up to the evaluation date.
- Return: pd.Series indexed by (date, symbol); higher = more attractive;
  NaN = no opinion. Returning full history is fine.
- PURITY: no I/O of any kind, no randomness without a fixed literal seed,
  no state between calls, no mutation of the input. File reads at compute
  time are detected and fail the tests.
- No lookahead: the value at date t may use data up to and including t only.
  Tests recompute on truncated data and compare.
- The file MUST contain exactly one module-level line starting with
  `PARAMS = ` holding a JSON-compatible dict literal (the orchestrator
  rewrites this line during pre-registered iterations). compute_signal must
  read its parameters from PARAMS, and PARAMS must equal the hypothesis
  card's "Parameters (iteration 0)" JSON block EXACTLY — any divergence
  fails the stage.
- Allowed imports: pandas, numpy, math only.

Output ONLY the complete Python source of signal.py. No preamble, no fences.
{repair}
=== hypothesis.md ===
{hypothesis_md}
"""


def s4_referee(spec_text: str, hypothesis_md: str, results_json: str) -> str:
    sections = _spec_sections(spec_text, ("8", "8b", "10", "13"))
    return f"""You are the REFEREE stage (S4) of an autonomous quant research loop.
Follow SPEC §13 exactly: parse the pre-registered blocks from hypothesis.md,
compare against results.json (it is ground truth; DSR is precomputed in
referee_inputs.deflated_sharpe), apply §8's decision rule MECHANICALLY, and
output decision.json per §10. You never propose fixes or new ideas. Your
justification must cite the pre-registered numbers.

Output ONLY the decision.json content as valid JSON. No preamble, no fences.

=== SPEC sections ===
{sections}

=== hypothesis.md ===
{hypothesis_md}

=== results.json ===
{results_json}
"""


def s5_memo(template: str, hypothesis_md: str, results_json: str,
            decision_json: str) -> str:
    return f"""You are the MEMO stage (S5) of an autonomous quant research loop.
Write the one-page research memo for this hypothesis using the template.
Memos are written for ALL outcomes including kills — a documented kill is a
success of the process. Fill the lineage section from results.json
(engine.git_commit, engine.data_manifest_sha256) and hypothesis.md
(card_version). Keep it to one page.

Output ONLY the completed memo.md content. No preamble, no fences.

=== template ===
{template}

=== hypothesis.md ===
{hypothesis_md}

=== results.json ===
{results_json}

=== decision.json ===
{decision_json}
"""
