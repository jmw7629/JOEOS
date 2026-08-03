"""Action Registry for the JoeOS Automation Platform.

The single authoritative registry of workflow actions. Actions are typed,
declare their side effects and permissions, and are executed only through the
execution engine which enforces the workflow's granted permissions. Privileged
actions route through authoritative JoeOS services; a workflow never receives
raw shell or unrestricted filesystem access.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .models import NodeConfig, WorkflowDefinition

ACTION_CATALOG: Dict[str, Dict[str, Any]] = {
    "joeos.notification": {
        "permission": "notification.publish",
        "side_effects": ("notification",),
        "risk": "low",
        "description": "Publish a notification (through the Communications Platform when available).",
    },
    "joeos.comms.notification": {
        "permission": "notification.publish",
        "side_effects": ("notification",),
        "risk": "low",
        "description": "Create a notification through the authoritative Communications Platform.",
    },
    "joeos.comms.internal_message": {
        "permission": "notification.publish",
        "side_effects": ("message",),
        "risk": "low",
        "description": "Send an internal message through the Communications Platform.",
    },
    "joeos.comms.draft": {
        "permission": "notification.publish",
        "side_effects": ("draft",),
        "risk": "low",
        "description": "Create an external message draft; never sends automatically.",
    },
    "joeos.comms.digest": {
        "permission": "notification.publish",
        "side_effects": ("digest",),
        "risk": "low",
        "description": "Build a communications digest.",
    },
    "joeos.comms.request_send_approval": {
        "permission": "notification.publish",
        "side_effects": ("approval",),
        "risk": "medium",
        "description": "Request external-send approval for a draft.",
    },
    "joeos.memory.propose": {
        "permission": "memory.propose_memory",
        "side_effects": ("memory",),
        "risk": "low",
        "description": "Propose a memory record (never a direct high-authority write).",
    },
    "joeos.task.create": {
        "permission": "mission.create_task",
        "side_effects": ("task",),
        "risk": "medium",
        "description": "Create a task in a mission.",
    },
    "joeos.mission.create": {
        "permission": "mission.create_mission",
        "side_effects": ("mission",),
        "risk": "medium",
        "description": "Create a mission.",
    },
    "joeos.agent.request": {
        "permission": "agent.request_task",
        "side_effects": ("agent",),
        "risk": "medium",
        "description": "Request an agent task.",
    },
    "joeos.command.validate": {
        "permission": "command.validate",
        "side_effects": (),
        "risk": "low",
        "description": "Validate a registered command against policy (no execution).",
    },
    "joeos.git.status": {
        "permission": "git.read",
        "side_effects": (),
        "risk": "low",
        "description": "Inspect the Git working-tree state of an approved project.",
    },
    "joeos.delay": {
        "permission": "",
        "side_effects": (),
        "risk": "low",
        "description": "Wait for a fixed duration.",
    },
    "joeos.transform": {
        "permission": "",
        "side_effects": (),
        "risk": "low",
        "description": "Transform values through the constrained expression engine.",
    },
    "joeos.audit_marker": {
        "permission": "",
        "side_effects": (),
        "risk": "low",
        "description": "Write an audit marker to the run trace.",
    },
}


class ActionError(RuntimeError):
    pass


class ActionRegistry:
    """Registers and dispatches workflow actions."""

    def __init__(self, handlers: Optional[Dict[str, Callable]] = None) -> None:
        self._handlers: Dict[str, Callable] = dict(handlers or {})

    def register(self, action_id: str, handler: Callable, *, permission: str = "", side_effects: Tuple[str, ...] = ()) -> None:
        self._handlers[action_id] = handler
        ACTION_CATALOG.setdefault(
            action_id,
            {"permission": permission, "side_effects": side_effects, "risk": "low", "description": "Registered action."},
        )

    def exists(self, action_id: str) -> bool:
        return action_id in ACTION_CATALOG

    def permission_for(self, action_id: str) -> str:
        return ACTION_CATALOG.get(action_id, {}).get("permission", "")

    def side_effects(self, action_id: str) -> Tuple[str, ...]:
        return tuple(ACTION_CATALOG.get(action_id, {}).get("side_effects", ()))

    def risk(self, action_id: str) -> str:
        return ACTION_CATALOG.get(action_id, {}).get("risk", "low")

    def dispatch(
        self,
        *,
        action_id: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
        variables: Dict[str, Any],
        trace,
    ) -> Dict[str, Any]:
        handler = self._handlers.get(action_id)
        if handler is None:
            raise ActionError("action %s has no registered handler." % action_id)
        try:
            return handler(params=params, context=context, variables=variables, trace=trace)
        except ActionError:
            raise
        except Exception as exc:
            raise ActionError("action %s failed: %s" % (action_id, type(exc).__name__)) from exc

    def list_catalog(self) -> Tuple[dict, ...]:
        return tuple(
            {
                "action_id": action_id,
                "permission": info.get("permission", ""),
                "side_effects": info.get("side_effects", ()),
                "risk": info.get("risk", "low"),
                "description": info.get("description", ""),
            }
            for action_id, info in sorted(ACTION_CATALOG.items())
        )


def default_handlers(*, event_sink=None, memory_proposer=None, agent_api=None, git_reader=None, communications=None) -> Dict[str, Callable]:
    """Wire core action handlers to authoritative JoeOS services.

    ``communications`` is an optional facade into the Communications Platform
    (``server.communications``). When provided, workflows route notifications,
    internal messages, drafts, digests, and send approvals through the
    authoritative CommunicationsService instead of calling providers directly.
    """

    def _subworkflow(params, context, variables, trace):
        runner = (context or {}).get("_subworkflow_runner")
        if runner is None:
            return {"invoked": False, "reason": "subworkflow runner unavailable"}
        return runner(params, context, variables)

    def _notification(params, context, variables, trace):
        message = str(params.get("message") or "")
        if not message:
            raise ActionError("notification action requires a message.")
        safe_message = message[:500]
        if communications is not None:
            notification = communications.create_notification(
                source=str(params.get("source") or "automation"),
                source_type="workflow",
                category=str(params.get("category") or "workflow_notification"),
                title=str(params.get("title") or "Workflow notification"),
                message=safe_message,
                severity=str(params.get("severity") or "informational"),
                priority=str(params.get("priority") or "normal"),
                workflow=context.get("workflow_id") if isinstance(context, dict) else "",
            )
            return {"published": True, "message": safe_message, "notification_id": notification.notification_id}
        if event_sink:
            event_sink("info", "workflow", safe_message)
        return {"published": True, "message": safe_message}

    def _comms_internal_message(params, context, variables, trace):
        if communications is None:
            return {"sent": False, "reason": "communications platform unavailable"}
        from server.communications.models import Origin
        message = str(params.get("message") or "")
        if not message:
            raise ActionError("internal message action requires a message.")
        recipients = params.get("recipients") or ("identity.user",)
        record = communications.send_internal(
            communication_type=str(params.get("communication_type") or "internal_direct_message"),
            recipients=tuple(recipients),
            subject=str(params.get("subject") or "Workflow message"),
            body=message,
            origin=Origin(
                origin_type="workflow",
                label="Workflow",
                source_workflow=context.get("workflow_id") if isinstance(context, dict) else "",
            ),
            priority=str(params.get("priority") or "normal"),
        )
        return {"sent": True, "message_id": record.message_id}

    def _comms_draft(params, context, variables, trace):
        if communications is None:
            return {"drafted": False, "reason": "communications platform unavailable"}
        from server.communications.models import DraftRecord
        draft = communications.save_draft(
            DraftRecord(
                draft_id="draft_wf_" + uuid_hex(),
                author=str(params.get("author") or "user"),
                proposed_sender=str(params.get("proposed_sender") or "identity.user"),
                recipients=tuple(params.get("recipients") or ()),
                provider=str(params.get("provider") or "test.isolated"),
                account=str(params.get("account") or ""),
                subject=str(params.get("subject") or ""),
                body=str(params.get("body") or ""),
                source="workflow",
                source_workflow=context.get("workflow_id") if isinstance(context, dict) else "",
            )
        )
        # A draft is never sent automatically; it requires review/approval.
        return {"drafted": True, "draft_id": draft.draft_id, "sent": False}

    def _comms_digest(params, context, variables, trace):
        if communications is None:
            return {"built": False, "reason": "communications platform unavailable"}
        digest = communications.build_digest(window_hours=int(params.get("window_hours") or 24))
        return {"built": True, "digest_id": digest.digest_id}

    def _comms_request_send_approval(params, context, variables, trace):
        if communications is None:
            return {"requested": False, "reason": "communications platform unavailable"}
        draft = communications.get_draft(str(params.get("draft_id") or ""))
        if draft is None:
            return {"requested": False, "reason": "draft not found"}
        approval = communications.request_external_send(
            draft=draft,
            subject=str(params.get("subject") or draft.subject),
            body=str(params.get("body") or draft.body),
            recipients=tuple(params.get("recipients") or draft.recipients),
            provider=str(params.get("provider") or draft.provider or "test.isolated"),
            account=str(params.get("account") or draft.account),
            privacy=str(params.get("privacy") or draft.privacy or "private"),
        )
        return {"requested": True, "approval_id": approval["approval_id"]}

    def _memory_propose(params, context, variables, trace):
        if memory_proposer is None:
            return {"proposed": False, "reason": "memory service unavailable"}
        return memory_proposer(params, context, variables)

    def _git_status(params, context, variables, trace):
        if git_reader is None:
            return {"available": False, "reason": "git service unavailable"}
        return git_reader(params, context, variables)

    def _delay(params, context, variables, trace):
        seconds = max(0, int(params.get("seconds") or 0))
        if seconds > 86400:
            raise ActionError("delay exceeds the maximum duration.")
        import time
        time.sleep(seconds)
        return {"delayed_seconds": seconds}

    def _transform(params, context, variables, trace):
        from .expressions import evaluate_expression
        expression = str(params.get("expression") or "")
        if not expression:
            raise ActionError("transform action requires an expression.")
        return {"value": evaluate_expression(expression, variables)}

    def _audit_marker(params, context, variables, trace):
        marker = str(params.get("message") or "audit")
        if trace is not None:
            trace(marker)
        return {"marker": marker}

    return {
        "joeos.notification": _notification,
        "joeos.comms.notification": _notification,
        "joeos.comms.internal_message": _comms_internal_message,
        "joeos.comms.draft": _comms_draft,
        "joeos.comms.digest": _comms_digest,
        "joeos.comms.request_send_approval": _comms_request_send_approval,
        "joeos.memory.propose": _memory_propose,
        "joeos.git.status": _git_status,
        "joeos.delay": _delay,
        "joeos.transform": _transform,
        "joeos.audit_marker": _audit_marker,
        "joeos.subworkflow": _subworkflow,
    }


def uuid_hex() -> str:
    return uuid.uuid4().hex[:16]