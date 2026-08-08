"""Durable notifications for autonomous operations.

Bridges autonomous event outcomes to the authoritative NotificationCenter
(server.communications). Notifications are persisted server-side; realtime
toasts are a presentation layer only. Deep links point at the exact
AutomationRun result.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from .models import AutomationDefinition, AutomationRun

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_deep_link(run: AutomationRun) -> str:
    return "/os/automations/%s/runs/%s" % (run.automation_id, run.id)


class AutomationNotifier:
    def __init__(self, notification_service, *, event_sink: Optional[Callable[..., None]] = None) -> None:
        self._notifications = notification_service
        self._event_sink = event_sink

    def _emit(self, category: str, title: str, message: str,
              severity: str, related_entity: str, links: tuple, dedup_key: str) -> None:
        if self._notifications is None:
            return
        try:
            self._notifications.create_notification(
                source="automation",
                source_type="automation",
                category=category,
                title=title,
                message=message,
                severity=severity,
                deduplication_key=dedup_key,
                related_entity=related_entity,
                action_links=links,
            )
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("automation notification failed: %s", error)

    def completed(self, run: AutomationRun, definition: Optional[AutomationDefinition]) -> None:
        name = definition.name if definition else run.automation_id
        self._emit(
            category="AUTOMATION_COMPLETED",
            title="Automation completed: %s" % name,
            message=("The automation run %s finished successfully."
                     % run.id)[:500],
            severity="success",
            related_entity="automation:" + run.automation_id,
            links=(_run_deep_link(run),),
            dedup_key="automation.completed.%s" % run.id,
        )

    def failed(self, run: AutomationRun, definition: Optional[AutomationDefinition]) -> None:
        name = definition.name if definition else run.automation_id
        self._emit(
            category="AUTOMATION_FAILED",
            title="Automation failed: %s" % name,
            message=("The automation run %s failed (%s)."
                     % (run.id, run.error_category or "unknown"))[:500],
            severity="error",
            related_entity="automation:" + run.automation_id,
            links=(_run_deep_link(run),),
            dedup_key="automation.failed.%s" % run.id,
        )

    def blocked(self, run: AutomationRun, definition: Optional[AutomationDefinition]) -> None:
        name = definition.name if definition else run.automation_id
        self._emit(
            category="AUTOMATION_BLOCKED",
            title="Automation blocked: %s" % name,
            message=("The automation run %s is blocked and needs attention." % run.id)[:500],
            severity="warning",
            related_entity="automation:" + run.automation_id,
            links=(_run_deep_link(run),),
            dedup_key="automation.blocked.%s" % run.id,
        )

    def retrying(self, run: AutomationRun, definition: Optional[AutomationDefinition]) -> None:
        name = definition.name if definition else run.automation_id
        self._emit(
            category="AUTOMATION_RETRYING",
            title="Automation retrying: %s" % name,
            message=("Automation %s will retry (attempt %d, retry at %s)."
                     % (run.automation_id, run.attempt, run.next_retry_at))[:500],
            severity="warning",
            related_entity="automation:" + run.automation_id,
            links=(_run_deep_link(run),),
            dedup_key="automation.retry.%s.%d" % (run.id, run.attempt),
        )

    def approval_required(self, run: AutomationRun, definition: Optional[AutomationDefinition],
                          proposal_id: str, approval_id: str) -> None:
        name = definition.name if definition else run.automation_id
        self._emit(
            category="APPROVAL_REQUIRED",
            title="Approval required: %s" % name,
            message=("Automation %s requires approval (proposal %s)."
                     % (run.automation_id, proposal_id))[:500],
            severity="warning",
            related_entity="approval:" + approval_id,
            links=(_run_deep_link(run), "/os/automations/%s/runs/%s" % (run.automation_id, run.id)),
            dedup_key="automation.approval.%s.%s" % (run.id, approval_id),
        )

    def handle(self, payload: Dict) -> None:
        run = payload.get("run")
        definition = payload.get("definition")
        kind = payload.get("kind", "completed")
        if run is None:
            return
        if kind == "failure" or run.state == "failed":
            self.failed(run, definition)
        elif kind == "retry" or run.state == "retry_wait":
            self.retrying(run, definition)
        elif run.state == "succeeded":
            self.completed(run, definition)
