"""JoeOS Enterprise Object System — core contracts.

Everything meaningful inside JoeOS is an Enterprise Object: an agent, a model,
a provider, a file, a workflow, a schedule, a task, an approval, an execution,
a machine, a conversation, a memory, a module. They all participate in the same
universal object model with identity, type, state, relationships, capabilities,
permissions, lifecycle, history, actions, and context.

The system is implemented incrementally: existing domains keep their own
databases and services, and are surfaced through authorized adapters. No
destructive rewrite is ever performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------
# Canonical object references
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ObjectRef:
    """A lightweight, canonical, stable reference to an Enterprise Object.

    ``object_id`` and ``object_type`` are the only required fields. Optional
    ``organization_id``/``workspace_id`` scope the reference where relevant.

    ObjectRef is how Joe context, relationships, navigation, activity, search,
    approvals, executions, and files reference objects — without copying whole
    objects around.
    """

    object_id: str
    object_type: str
    organization_id: Optional[str] = None
    workspace_id: Optional[str] = None
    display_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"object_id": self.object_id, "object_type": self.object_type}
        if self.organization_id:
            payload["organization_id"] = self.organization_id
        if self.workspace_id:
            payload["workspace_id"] = self.workspace_id
        if self.display_hint:
            payload["display_hint"] = self.display_hint
        return payload

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> Optional["ObjectRef"]:
        if not isinstance(payload, dict):
            return None
        object_id = str(payload.get("object_id") or "").strip()
        object_type = str(payload.get("object_type") or "").strip()
        if not object_id or not object_type:
            return None
        return cls(
            object_id=object_id[:160],
            object_type=object_type[:64],
            organization_id=str(payload["organization_id"]).strip()[:64] if payload.get("organization_id") else None,
            workspace_id=str(payload["workspace_id"]).strip()[:64] if payload.get("workspace_id") else None,
            display_hint=str(payload["display_hint"]).strip()[:120] if payload.get("display_hint") else None,
        )

    def __str__(self) -> str:
        return f"{self.object_type}/{self.object_id}"


# --------------------------------------------------------------------------
# Object type registry
# --------------------------------------------------------------------------

# Canonical, typed object kinds. These are the *known* first-class types. New
# enterprise types can be registered declaratively without editing this list.
OBJECT_TYPES: Dict[str, str] = {
    "organization": "organization",
    "workspace": "workspace",
    "user": "user",
    "team": "team",
    "department": "department",
    "agent": "agent",
    "agent_run": "agent_run",
    "provider": "provider",
    "model": "model",
    "capability": "capability",
    "tool": "tool",
    "conversation": "conversation",
    "message": "message",
    "memory": "memory",
    "task": "task",
    "campaign": "campaign",
    "work_package": "work_package",
    "approval": "approval",
    "execution": "execution",
    "schedule": "schedule",
    "automation": "automation",
    "pipeline": "pipeline",
    "pipeline_stage": "pipeline_stage",
    "file": "file",
    "folder": "folder",
    "artifact": "artifact",
    "module": "module",
    "widget": "widget",
    "machine": "machine",
    "device": "device",
    "service": "service",
    "notification": "notification",
    "event": "event",
}

# Aliases accepted when resolving references (UI/module historical naming).
OBJECT_TYPE_ALIASES: Dict[str, str] = {
    "agent": "agent",
    "automation": "automation",
    "workflow": "automation",
    "bot": "agent",
    "model": "model",
    "provider": "provider",
    "file": "file",
    "project": "file",
    "approval": "approval",
    "execution": "execution",
    "mission": "agent_run",
    "run": "agent_run",
    "schedule": "schedule",
    "conversation": "conversation",
    "message": "message",
    "memory": "memory",
    "task": "task",
    "campaign": "campaign",
    "work_package": "work_package",
    "workpackage": "work_package",
    "device": "device",
    "machine": "machine",
    "module": "module",
    "widget": "widget",
    "user": "user",
    "team": "team",
    "organization": "organization",
}


def normalize_object_type(raw: Any) -> Optional[str]:
    """Return the canonical object type for a raw string, or None if unknown.

    Accepts canonical names and documented aliases. Unknown types resolve to
    None so callers can fail safely rather than fabricate meaning.
    """
    if not raw:
        return None
    value = str(raw).strip().lower().replace("-", "_")
    if value in OBJECT_TYPES:
        return value
    if value in OBJECT_TYPE_ALIASES:
        return OBJECT_TYPE_ALIASES[value]
    return None


def register_object_type(kind: str) -> None:
    """Declare a custom enterprise object type (validated at call site)."""
    kind = str(kind).strip().lower().replace("-", "_")
    if kind and kind not in OBJECT_TYPES:
        OBJECT_TYPES[kind] = kind
        OBJECT_TYPE_ALIASES[kind] = kind


# --------------------------------------------------------------------------
# Object capabilities
# --------------------------------------------------------------------------

# Capability flags that describe what may be done to/with an object.
CAP_VIEW = "view"
CAP_INSPECT = "inspect"
CAP_EDIT = "edit"
CAP_EXECUTE = "execute"
CAP_APPROVE = "approve"
CAP_REJECT = "reject"
CAP_ARCHIVE = "archive"
CAP_RESTORE = "restore"
CAP_DUPLICATE = "duplicate"
CAP_MOVE = "move"
CAP_LINK = "link"
CAP_COMMENT = "comment"
CAP_SHARE = "share"
CAP_EXPORT = "export"
CAP_COMPARE = "compare"
CAP_VERSION = "version"
CAP_ROLLBACK = "rollback"
CAP_AUTOMATE = "automate"
CAP_SCHEDULE = "schedule"
CAP_ATTACH = "attach"
CAP_SEARCH = "search"
CAP_ASK_JOE = "ask_joe"

# Capabilities an object always offers regardless of type (base contract).
BASE_CAPABILITIES = frozenset({CAP_VIEW, CAP_INSPECT, CAP_SEARCH, CAP_ASK_JOE})

# Type-specific capability maps. Objects in a domain may also disable
# capabilities based on state (e.g. an archived object is not executable).
_TYPE_CAPABILITIES: Dict[str, frozenset] = {
    "agent": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_AUTOMATE, CAP_SCHEDULE, CAP_ARCHIVE, CAP_RESTORE, CAP_DUPLICATE, CAP_COMMENT, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "agent_run": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EXPORT, CAP_COMMENT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "model": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EXECUTE, CAP_COMPARE, CAP_COMMENT, CAP_SEARCH, CAP_ASK_JOE}),
    "provider": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EXECUTE, CAP_EDIT, CAP_EXPORT, CAP_SEARCH, CAP_ASK_JOE}),
    "file": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_ARCHIVE, CAP_RESTORE, CAP_EXPORT, CAP_ATTACH, CAP_COMMENT, CAP_DUPLICATE, CAP_MOVE, CAP_LINK, CAP_VERSION, CAP_SEARCH, CAP_ASK_JOE}),
    "folder": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_ARCHIVE, CAP_RESTORE, CAP_EXPORT, CAP_ATTACH, CAP_MOVE, CAP_LINK, CAP_SEARCH, CAP_ASK_JOE}),
    "approval": frozenset({CAP_VIEW, CAP_INSPECT, CAP_APPROVE, CAP_REJECT, CAP_COMMENT, CAP_EXPORT, CAP_SEARCH, CAP_ASK_JOE}),
    "execution": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EXECUTE, CAP_APPROVE, CAP_REJECT, CAP_COMMENT, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "automation": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_AUTOMATE, CAP_SCHEDULE, CAP_ARCHIVE, CAP_RESTORE, CAP_VERSION, CAP_ROLLBACK, CAP_DUPLICATE, CAP_COMPARE, CAP_COMMENT, CAP_SEARCH, CAP_ASK_JOE}),
    "schedule": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_ARCHIVE, CAP_RESTORE, CAP_SEARCH, CAP_ASK_JOE}),
    "pipeline": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_AUTOMATE, CAP_VERSION, CAP_COMPARE, CAP_SEARCH, CAP_ASK_JOE}),
    "task": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_APPROVE, CAP_REJECT, CAP_ARCHIVE, CAP_RESTORE, CAP_COMMENT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "work_package": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_APPROVE, CAP_REJECT, CAP_ARCHIVE, CAP_RESTORE, CAP_DUPLICATE, CAP_MOVE, CAP_LINK, CAP_COMMENT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "campaign": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_APPROVE, CAP_ARCHIVE, CAP_RESTORE, CAP_DUPLICATE, CAP_LINK, CAP_COMMENT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "conversation": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_COMMENT, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "message": frozenset({CAP_VIEW, CAP_INSPECT, CAP_COMMENT, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "memory": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_COMMENT, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "module": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_ARCHIVE, CAP_RESTORE, CAP_VERSION, CAP_EXPORT, CAP_SEARCH, CAP_ASK_JOE}),
    "widget": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_MOVE, CAP_ARCHIVE, CAP_RESTORE, CAP_SEARCH, CAP_ASK_JOE}),
    "device": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_ARCHIVE, CAP_RESTORE, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "machine": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_EXECUTE, CAP_ARCHIVE, CAP_RESTORE, CAP_EXPORT, CAP_ATTACH, CAP_SEARCH, CAP_ASK_JOE}),
    "user": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_COMMENT, CAP_EXPORT, CAP_SEARCH, CAP_ASK_JOE}),
    "team": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_MOVE, CAP_COMMENT, CAP_SEARCH, CAP_ASK_JOE}),
    "organization": frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_ADMINISTER, CAP_SEARCH, CAP_ASK_JOE}) if False else frozenset({CAP_VIEW, CAP_INSPECT, CAP_EDIT, CAP_SEARCH, CAP_ASK_JOE}),
}


def capabilities_for(object_type: str) -> frozenset:
    """Base capabilities for an object type (union of the base contract and any
    type-specific capabilities)."""
    return frozenset(BASE_CAPABILITIES) | _TYPE_CAPABILITIES.get(object_type, frozenset())


def _is_state_disabled(type_capability: str, lifecycle_state: Optional[str]) -> bool:
    """Return True when an object's current lifecycle state disables a capability."""
    state = (lifecycle_state or "").lower()
    if state in ("archived", "deleted", "purged"):
        return type_capability in (CAP_EXECUTE, CAP_EDIT, CAP_AUTOMATE, CAP_SCHEDULE, CAP_APPROVE, CAP_MOVE, CAP_LINK)
    if state == "failed":
        return type_capability == CAP_EXECUTE
    return False


def effective_capabilities(
    object_type: str, lifecycle_state: Optional[str] = None, *, extra: Optional[Dict[str, bool]] = None
) -> List[str]:
    """Return the effective capability list for an object given its state.

    ``extra`` may disable capabilities explicitly (e.g. permission/policy
    decisions). Only capabilities that are both declared and not disabled by
    state or policy are returned.
    """
    declared = capabilities_for(object_type)
    denied = extra or {}
    result = []
    for cap in sorted(declared):
        if cap in CAP_VIEW and lifecycle_state in ("deleted", "purged"):
            continue
        if _is_state_disabled(cap, lifecycle_state):
            continue
        if denied.get(cap) is False:
            continue
        result.append(cap)
    return result
