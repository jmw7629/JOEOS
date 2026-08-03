"""Workflow templates for the JoeOS Automation Platform.

Safe, least-privilege templates. Templates never force push, expose ports,
disable security, upload private files, delete repositories, broadly access
secrets, or execute downloaded scripts.
"""

from __future__ import annotations

from typing import Tuple

from .models import (
    EdgeConfig,
    NodeConfig,
    Recurrence,
    ResourcePolicy,
    TriggerConfig,
    WorkflowDefinition,
)

TEMPLATES = {
    "project_health_check": {
        "name": "Project Health Check",
        "description": "Checks the working-tree state of an approved project and notifies the operator.",
        "risk": "low",
        "required_permissions": ("git.read", "notification.publish"),
    },
    "nightly_backup": {
        "name": "Nightly Local Backup",
        "description": "Runs a nightly maintenance workflow that records a backup marker.",
        "risk": "medium",
        "required_permissions": ("notification.publish",),
    },
    "failed_build_notify": {
        "name": "Failed-Build Notification",
        "description": "Publishes a notification when a build failure event arrives.",
        "risk": "low",
        "required_permissions": ("notification.publish",),
    },
    "model_runtime_health": {
        "name": "Model Runtime Health Check",
        "description": "Checks model runtime health and notifies on degradation.",
        "risk": "low",
        "required_permissions": ("notification.publish",),
    },
    "memory_review_reminder": {
        "name": "Memory Review Reminder",
        "description": "Proposes a memory reminder when triggered.",
        "risk": "low",
        "required_permissions": ("memory.propose_memory", "notification.publish"),
    },
}


def _template_definition(template_id: str, workflow_id: str, *, trigger: Optional[TriggerConfig] = None) -> WorkflowDefinition:
    info = TEMPLATES[template_id]
    nodes = [
        NodeConfig(id="start", type="start", title="Start"),
        NodeConfig(
            id="notify",
            type="notification",
            title="Notify",
            params={"message": "${message}"},
        ),
        NodeConfig(id="end", type="end", title="End"),
    ]
    edges = [
        EdgeConfig(source="start", target="notify"),
        EdgeConfig(source="notify", target="end"),
    ]
    variables = (
        {"name": "message", "type": "string", "default": "%s completed." % info["name"], "required": False, "scope": "run", "privacy": "plain", "max_size": 4096},
    )
    return WorkflowDefinition(
        workflow_id=workflow_id,
        name=info["name"],
        description=info["description"],
        owner="user",
        creator="user",
        source="template",
        risk=info["risk"],
        version="1.0.0",
        triggers=(trigger,) if trigger else (TriggerConfig(trigger_id="manual", type="manual"),),
        nodes=tuple(nodes),
        edges=tuple(edges),
        variables=tuple(variables),
        required_permissions=info["required_permissions"],
        resource=ResourcePolicy(max_active_runs=2, max_parallel_branches=1, max_loop_iterations=10, max_duration_seconds=3600, max_model_calls=0, max_tool_calls=10),
        template_source=template_id,
    )


def template_definition(template_id: str, workflow_id: str, *, scheduled: bool = False, timezone: str = "UTC", at_time: str = "09:00") -> WorkflowDefinition:
    trigger: Optional[TriggerConfig] = None
    if scheduled:
        trigger = TriggerConfig(
            trigger_id="scheduled",
            type="scheduled",
            schedule=Recurrence(kind="daily", at_time=at_time, timezone=timezone),
        )
    return _template_definition(template_id, workflow_id, trigger=trigger)


def list_templates() -> Tuple[dict, ...]:
    return tuple(
        {
            "template_id": template_id,
            "name": info["name"],
            "description": info["description"],
            "risk": info["risk"],
            "required_permissions": info["required_permissions"],
        }
        for template_id, info in sorted(TEMPLATES.items())
    )