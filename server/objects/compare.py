"""JoeOS Object Comparison — type-aware comparison of compatible objects.

The comparison renderer understands the object type and shows meaningful
differences (not generic JSON): models compare capabilities/provider/health;
providers compare availability/privacy/streaming; agents compare role/
capabilities/availability. Only fields that exist and are comparable are
included. Data comes from authoritative domain services via the resolver's
wired adapters — never fabricated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from server.objects.core import ObjectRef, normalize_object_type, safety_for_capability
from server.objects.resolver import ObjectResolver

# Comparison fields per type: (key, label). Each value is resolved from the
# object summary or a type-specific extractor.
_MODEL_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("health", "Health"),
    ("capabilities", "Capabilities"),
    ("provider", "Provider"),
    ("description", "Description"),
]
_PROVIDER_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("health", "Health"),
    ("kind", "Kind"),
    ("available", "Available"),
    ("supports_streaming", "Streaming"),
    ("privacy_class", "Privacy"),
]
_AGENT_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("availability", "Availability"),
    ("role", "Role"),
    ("capabilities", "Capabilities"),
    ("version", "Config version"),
]
_EXECUTION_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("duration", "Duration"),
    ("agent", "Agent"),
    ("description", "Objective"),
]
_AUTOMATION_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("health", "Health"),
    ("version", "Version"),
    ("description", "Description"),
]
_MEMORY_FIELDS = [
    ("name", "Name"),
    ("status", "Status"),
    ("category", "Category"),
    ("scope", "Scope"),
    ("authority", "Authority"),
]

_TYPE_FIELDS: Dict[str, List[Any]] = {
    "model": _MODEL_FIELDS,
    "provider": _PROVIDER_FIELDS,
    "agent": _AGENT_FIELDS,
    "agent_run": _EXECUTION_FIELDS,
    "execution": _EXECUTION_FIELDS,
    "automation": _AUTOMATION_FIELDS,
    "schedule": _AUTOMATION_FIELDS,
    "memory": _MEMORY_FIELDS,
    "conversation": _MEMORY_FIELDS,
}


def comparison_fields(object_type: str) -> List[Any]:
    return _TYPE_FIELDS.get(object_type, [("name", "Name"), ("status", "Status"), ("description", "Description")])


def _display_value(field: str, summary: Optional[Dict[str, Any]], ref: ObjectRef) -> Any:
    if summary is None:
        return None
    if field == "name":
        return summary.get("name") or ref.object_id
    if field == "status" or field == "availability":
        return summary.get("semantic_status", {}).get("label") or summary.get("status")
    if field == "capabilities":
        caps = summary.get("capabilities") or []
        return ", ".join(caps[:8]) or "none"
    if field in summary:
        return summary[field]
    return None


def compare_objects(left_ref: ObjectRef, right_ref: ObjectRef, resolver: ObjectResolver, principal: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two compatible objects (same normalized type) with a type-aware
    renderer model."""
    left_type = normalize_object_type(left_ref.object_type)
    right_type = normalize_object_type(right_ref.object_type)
    if not left_type or not right_type:
        return {"error": "Unknown object type.", "comparable": False}
    if left_type != right_type:
        return {
            "error": "Cannot compare different object types (%s vs %s)." % (left_type, right_type),
            "comparable": False,
        }
    left_summary = resolver.resolve(ObjectRef(object_id=left_ref.object_id, object_type=left_type), principal)
    right_summary = resolver.resolve(ObjectRef(object_id=right_ref.object_id, object_type=right_type), principal)
    if left_summary is None or right_summary is None:
        return {"error": "One or both objects are not accessible.", "comparable": False}

    fields = comparison_fields(left_type)
    columns = [
        {"ref": {"object_type": left_type, "object_id": left_ref.object_id, "display_hint": left_ref.display_hint}, "summary": left_summary},
        {"ref": {"object_type": right_type, "object_id": right_ref.object_id, "display_hint": right_ref.display_hint}, "summary": right_summary},
    ]
    rows = []
    for key, label in fields:
        left_value = _display_value(key, left_summary, left_ref)
        right_value = _display_value(key, right_summary, right_ref)
        same = left_value == right_value
        rows.append({"field": key, "label": label, "left": left_value, "right": right_value, "same": same})
    return {
        "object_type": left_type,
        "comparable": True,
        "columns": columns,
        "rows": rows,
        "differences": sum(1 for row in rows if not row["same"]),
    }
