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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .models import NodeConfig, WorkflowDefinition

ACTION_CATALOG: Dict[str, Dict[str, Any]] = {
    "joeos.notification": {
        "permission": "notification.publish",
        "side_effects": ("notification",),
        "risk": "low",
        "description": "Publish a notification.",
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


def default_handlers(*, event_sink=None, memory_proposer=None, agent_api=None, git_reader=None) -> Dict[str, Callable]:
    """Wire core action handlers to authoritative JoeOS services."""

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
        if event_sink:
            event_sink("info", "workflow", safe_message)
        return {"published": True, "message": safe_message}

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
        "joeos.memory.propose": _memory_propose,
        "joeos.git.status": _git_status,
        "joeos.delay": _delay,
        "joeos.transform": _transform,
        "joeos.audit_marker": _audit_marker,
        "joeos.subworkflow": _subworkflow,
    }