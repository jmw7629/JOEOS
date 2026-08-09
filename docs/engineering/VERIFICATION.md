# Verification

Every implementation is independently verified before it is committed and
integrated. Verification is a distinct role: the Verifier does not implement.

## Verifier role

The `engineering.verification` agent receives the WorkPackage, its acceptance
criteria, the resulting diff/evidence, and the test output. It returns a verdict
beginning with exactly:

- `VERIFIED` — acceptance criteria met, no regression risk
- `PARTIAL` — some criteria met, findings remain
- `REJECTED` — criteria not met or scope creep / regression risk

## Builder ↔ Verifier repair loop

1. Builder implements in an isolated worktree.
2. A bounded test battery runs (`python_compile`; `backend_tests` for
   medium/high/critical risk).
3. Verifier judges the result.
4. On `REJECTED`, the package is routed back to eligible for a bounded repair
   attempt (`max_attempts_per_package`, default 3).
5. When the budget is exhausted the package is blocked and a durable blocker is
   raised.

There is no infinite repair loop. After the budget, the package waits for a
human or is marked failed.

## Test matrix selection

Not every suite runs after every change. The Director selects targeted tests by
risk:

- `joeos.dev.python_compile` — always
- `joeos.dev.backend_tests` — medium/high/critical risk packages
- `joeos.dev.runner_tests`, `joeos.dev.frontend_contract`,
  `joeos.dev.mobile_web_*` — when the package touches those areas
- `joeos.apple.build` — iOS client packages (remote Mac)

Broader regression runs before final integration, not after every single change.

## Evidence

Every verdict, run id, provider, and model is recorded in the campaign
checkpoint stream and in the AgentRun audit trail. JoeOS never fabricates a pass
when a check was unmeasured.
