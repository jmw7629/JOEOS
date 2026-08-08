# Approvals

Automations gain **zero extra authority**. They run with the exact owner
principal, ToolBroker, policy, approval, and execution boundaries as an
interactive agent run.

Privileged operation flow:

```
Automation -> AgentRun -> Tool Request -> ActionProposal
  -> PolicyDecision -> Approval -> Execution
```

If approval is required, the run surfaces `waiting_for_approval` and no
privileged execution occurs. When a valid approval arrives, the correct
blocked AgentRun/AutomationRun resumes without creating a duplicate occurrence
and without re-executing earlier successful tasks. Denied/expired/revoked/
superseded approvals update automation state honestly.

## Self-replication

An agent cannot silently create unlimited recurring automations. Creating an
automation from an agent requires a typed automation proposal -> policy ->
explicit user confirmation/approval -> AutomationDefinition. No recursive
automation-creation loop without approval.
