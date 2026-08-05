# Agents, Actions, and Approvals (Phase P3B)

The backend is authoritative for providers, models, agents, tools, action
proposals, policy, approvals, and council state. Clients and models may request
or suggest; they never grant authority. No privileged action executes in this
phase: approved privileged proposals stop at `approved_awaiting_executor`.

## Flow

```
Principal starts an authorized agent run (canonical conversation)
   -> backend selects an allowed provider + model (local-only honored)
   -> agent returns content or a structured tool request
   -> backend validates the request against the authoritative tool catalog
   -> state-changing request becomes an immutable ActionProposal
   -> PolicyEngine evaluates (risk tier, capabilities, separation of duties)
   -> deny  -> policy_denied
   -> allow_read_only -> approved_awaiting_executor (no executor)
   -> approval_required -> ApprovalRequest (+ one-time approval challenge
        for step-up) signed with the enrolled approval key
   -> approve -> approved_awaiting_executor; deny -> denied
```

## Key guarantees

- ActionProposal payloads are immutable; any parameter/target/tool-version
  change creates a new proposal with a new digest.
- Approvals bind to the exact proposal digest; replay, expiry, digest change,
  cross-workspace, and self-approval are rejected.
- The ordinary device-authentication key is never used for approvals; the
  separate enrolled approval key signs the `JOEOS-ACTION-APPROVAL-V1` domain
  message.
- A cancelled/denied run never appears successful; partial text is never
  presented as completion.
- Privileged capabilities are defined but never granted to the default owner
  role; privileged executors remain `unavailable`.

## Browser and Swift

The web and Swift clients render authoritative proposal/approval state and
require the enrolled native device for medium/high/critical approvals. There is
no browser or client approval bypass.
