# Notifications

Automation outcomes emit **durable** notifications through the authoritative
NotificationCenter (`/api/v1/communications/notifications`). Realtime toasts are
a presentation layer only; persistence is server-side so a closed browser never
loses an important notification.

Notification types: AUTOMATION_COMPLETED, AUTOMATION_FAILED,
AUTOMATION_BLOCKED, AUTOMATION_RETRYING, APPROVAL_REQUIRED,
AGENT_FAILED, EXECUTION_FAILED, ARTIFACT_READY, PROVIDER_UNAVAILABLE,
RUNNER_UNAVAILABLE, CONDITION_MET.

Each notification carries: id, principal/workspace scope, category, title,
bounded body, source type + id, deep link, severity, created/read state. No
secrets. Routine low-value successful checks are not spammed by default
(`on_success=false`); failures and approval-required notifications are on by
default.

Deep links point at the exact AutomationRun (`/os/automations/<id>/runs/<run>`),
so "Automation completed" opens the result.
