# Signed Private Runner Execution Plane (Phase P3C)

The backend is authoritative for runner enrollment, connection authentication,
execution-job creation, leasing, cancellation, secret leases, artifacts, and
terminal results. The runner is a dedicated unprivileged process on a trusted
machine that connects outbound to the backend over a private network.

## Trust and signing

- Runner enrollment: one-time short-lived challenge bound to installation,
  organization, workspace, and machine fingerprint; the runner proves key
  possession by signing `JOEOS-RUNNER-ENROLLMENT-V1`.
- Connection: the backend issues a `JOEOS-RUNNER-CONNECTION-V1` challenge; the
  runner signs the exact message with its P-256 runner key; a short-lived
  connection credential is issued, rotated, and revoked on runner revocation.
- Jobs: created only from approved proposals. The payload digest binds
  proposal/policy/approval digests, tool and executor versions, runner,
  parameters, target, limits, idempotency key, and org/workspace.
- Results: the runner signs `JOEOS-EXECUTION-RESULT-V1`; the backend verifies
  and records exactly one terminal state. Late output is rejected.

## Enforcement

- Deny by default for unknown runners, executors, tools, proposals, jobs,
  targets, paths, leases, and policy states.
- Every dispatch revalidates the proposal, policy, approvals, approvers,
  runner, executor, and scope.
- Executors use the safe process foundation: `shell=False`, allowlisted
  executables, typed arguments, minimal environment, process-group
  termination, bounded output, and timeouts.
- Emergency stop pauses dispatch and cancels queued jobs via the trusted local
  CLI; it never depends on a model or agent.

## Honest states

- Unknown, interrupted, timed-out, lease-expired, and result-rejected states
  remain honest. No fabricated success, output, or completion.
- Secret values never appear in proposals, jobs, logs, events, artifacts, or
  command lines; they are injected only through short-lived execution-bound
  leases and redacted from output.
