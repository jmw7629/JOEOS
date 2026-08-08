# Verification

The Verifier agent performs independent verification; it does not simply agree
with the Builder. The control-plane delegation + task-graph architecture makes
real Builder -> Verifier loops possible:

```
Joe run
  task Implement (Builder)   -> child AgentRun (proposes implementation)
  task Verify    (Verifier)  -> child AgentRun (independently checks)
```

Because each task runs through a real child AgentRun on the assigned agent, the
Verifier's judgment is independent of the Builder's. Results are
VERIFIED / PARTIALLY_VERIFIED / FAILED_VERIFICATION with evidence.

## Current state

- The architectural path is real (separate agents, separate child runs, real
  task dependencies).
- Automatic retry loops (Builder fixes -> Verifier re-checks) are not yet
  automated end-to-end; they are driven by the graph/operator today.

## Acceptance checks the Verifier is instructed to use

- Inspect the implementation/diff (read tools).
- Run authorized tests/builds where available.
- Confirm acceptance criteria with evidence.
- Return VERIFIED only when evidence supports it; otherwise FAILED_VERIFICATION
  with specific findings.
