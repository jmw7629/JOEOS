# Automations architecture

JoeOS autonomous operations is a durable, agent-based automation layer over the
existing AgentFabric. It is not a second agent framework and not a second model
runtime.

## Flow

```
AutomationDefinition (durable intention)
  trigger (one_time / recurring / event / condition_watch / manual)
    -> AutonomousScheduler (backend singleton asyncio loop)
    -> claim occurrence (durable lease, deterministic occurrence key)
    -> AutomationRun created (deduplicated)
    -> AgentFabricAutomationExecutor
    -> AgentRun (control plane)
    -> AgentVersion -> ProviderRegistry -> ModelRegistry -> Ollama
    -> delegation / TaskGraph / ToolBroker
    -> result persisted
    -> next occurrence computed
    -> durable NotificationCenter notification (+ realtime toast if open)
```

## Components

- `server/autonomous/models.py` — AutomationDefinition, AutomationRun, triggers, policies.
- `server/autonomous/storage.py` — durable SQLite store with unique occurrence key.
- `server/autonomous/scheduling.py` — deterministic next-occurrence (reuses automation schedule service).
- `server/autonomous/service.py` — definition lifecycle + scheduler-facing operations.
- `server/autonomous/executor.py` — AgentFabric bridge (exact interactive path).
- `server/autonomous/scheduler.py` — long-lived asyncio loop, lease + retry + recovery.
- `server/autonomous/notifier.py` — durable notification bridge.
- `server/autonomous/router.py` — `/api/v1/automations/*`.

## Process ownership

The scheduler is a backend-managed singleton asyncio task (like the campaign
worker). It is started once per backend process and never coupled to uvicorn
request workers, so there is a single scheduler instance. The browser is never
the scheduler; disconnecting the browser does not stop background execution.
