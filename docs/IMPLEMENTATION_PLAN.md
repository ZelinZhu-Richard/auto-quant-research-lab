# Research validity implementation plan

Base: local `059e095`, preserving its unpublished safety commits above public
`b494a06`. No historical artifact, frozen SPEC, or market dataset is edited.

1. **A / P0:** protected Python policy, strict metrics and registration checks,
   committed evidence verification at S4 and promotion. Accept only the computed
   outcome and first untried grid point; LLM output has no authorization role.
   Acceptance: adversarial PROMOTE, forged criteria, malformed metrics, changed
   evidence and inconsistent iteration all fail closed.
2. **B / P0:** append-only attempt journal with content identities, immutable
   score artifacts, replay rules, and explicit statistical limitations. Recover
   H001's three original scores from Git, independently reproduce the DSR
   sensitivity, and keep original results untouched. Acceptance: crash/retry,
   duplicate replay, tampering, and three distinct settings are tested.
3. **C:** preserve labeled v1 research baseline, add delayed-close accounting
   with actual holdings, cash and post-cost target sizing. Acceptance: hand
   calculations for drift, fees, observation/fill timing, missing marks and
   insolvency. Do not invent liquidity or borrow evidence.
4. **D:** strict bar and symbol coverage checks; optional externally sourced
   point-in-time membership input with provenance. Explicitly retain the
   existing dataset's active-market survivorship limitation.
5. **E:** deterministic toy positive, null, overfit and leakage cases; report
   counts and reasons without population error-control claims.
6. **F:** actual test logs, historical hashes, behavior/migration report and
   independent read-only review. Commit, push the requested branch and create
   the requested PR; never merge. Container checks only if Docker is available.
