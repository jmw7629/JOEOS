# Human Handoff

JoeOS autonomously performs safe, bounded engineering work, but it never
pretends to make genuine human decisions, obtain credentials, perform physical
device actions, or grant privileged approvals.

## Handoff reasons

| Reason | Meaning | Example |
|---|---|---|
| `human_decision` | a product decision the roadmap does not settle | mobile navigation tabs vs gesture-only |
| `credential_required` | an external credential is needed | OAuth, API key, Apple account, billing |
| `device_action_required` | a physical/GUI action is needed | Xcode signing prompt, iPhone confirmation, Face ID |
| `approval_required` | a privileged approval is required | high-risk production deployment |
| `security_block` | Security blocked the work | trust-boundary change rejected |
| `verifier_reject` | verification failed beyond the repair budget | acceptance criteria never met |

## Behavior

- A handoff raises a **durable blocker** (open until resolved) and notifies Joe
  through the Notification Center (`BUILD_BLOCKED`,
  `HUMAN_DECISION_REQUIRED`, `CREDENTIAL_REQUIRED`, `DEVICE_ACTION_REQUIRED`,
  `APPROVAL_REQUIRED`, `SECURITY_BLOCK`).
- Only the **dependent package** waits. Independent packages continue.
- JoeOS never asks repeatedly for the same credential; the blocker is durable.
- Resolution requires `engineering.blocker.resolve` (critical capability).

## Asking Joe

From the Build Command Center (`/os/build`) or Ask Joe, Joe sees:
- current work package and stage
- the exact open blockers with their reasons
- controls: Pause, Stop after current, Resume, Continue building

## Defaults

- Provide the question, options, an Architect recommendation, tradeoffs, and a
  default recommendation for `human_decision`.
- Provide exact minimal steps for `device_action_required`.
