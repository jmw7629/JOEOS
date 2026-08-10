"""ModuleManifest schema + validation.

This is the shared cross-platform contract for JoeOS modules. It is additive
and permissive on unknown fields (future capabilities must not break routing),
but strict on identity and safety fields so a malformed manifest can never
claim an invalid route, permission, or Joe-context source.

A manifest is data, not code. Clients render it through trusted native/web
component registries; unknown component types fail safely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Canonical component types a trusted renderer may interpret. Unknown types are
# rejected (never silently rendered).
ALLOWED_COMPONENTS = frozenset({
    "text", "rich_status", "metric", "list", "table", "card", "chart",
    "timeline", "activity_feed", "button", "form", "command_launcher",
    "search", "artifact_browser", "agent_panel", "task_panel",
    "approval_panel", "file_panel", "markdown", "image", "media",
    "web_safe", "custom_query", "inspector", "group", "tabs", "split_view",
    "stack", "grid", "navigation",
})

ALLOWED_JOE_CONTEXT_KINDS = frozenset({
    "module", "object", "selection", "route", "none",
})


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class WidgetManifest:
    id: str
    type: str
    title: str = ""
    size: Optional[Dict[str, Any]] = None
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleManifest:
    id: str
    type: str = "module"
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    icon: str = ""
    category: str = ""
    subcategory: str = ""
    route: str = ""
    supported_form_factors: List[str] = field(default_factory=lambda: ["phone", "tablet", "laptop", "desktop"])
    required_permissions: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    data_sources: List[str] = field(default_factory=list)
    joe_context: Dict[str, Any] = field(default_factory=dict)
    widgets: List[WidgetManifest] = field(default_factory=list)
    inspection: bool = False
    feature_flags: List[str] = field(default_factory=list)
    policy_requirements: List[str] = field(default_factory=list)
    min_client_version: str = ""
    visibility: str = "visible"
    ordering: int = 0
    pinned: bool = False
    user_customizable: bool = False
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "subcategory": self.subcategory,
            "route": self.route,
            "supported_form_factors": list(self.supported_form_factors),
            "required_permissions": list(self.required_permissions),
            "required_capabilities": list(self.required_capabilities),
            "commands": list(self.commands),
            "actions": list(self.actions),
            "data_sources": list(self.data_sources),
            "joe_context": dict(self.joe_context),
            "widgets": [w.__dict__ for w in self.widgets],
            "inspection": self.inspection,
            "feature_flags": list(self.feature_flags),
            "policy_requirements": list(self.policy_requirements),
            "min_client_version": self.min_client_version,
            "visibility": self.visibility,
            "ordering": self.ordering,
            "pinned": self.pinned,
            "user_customizable": self.user_customizable,
            "schema_version": self.schema_version,
        }


def _require_str(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError("manifest.%s must be a string" % field_name)
    if not allow_empty and not value.strip():
        raise ManifestValidationError("manifest.%s is required" % field_name)
    return value.strip()


def _validate_widget(raw: Any) -> WidgetManifest:
    if not isinstance(raw, dict):
        raise ManifestValidationError("manifest widget must be an object")
    widget_id = _require_str(raw.get("id"), "widget.id")
    widget_type = _require_str(raw.get("type"), "widget.type")
    if widget_type not in ALLOWED_COMPONENTS:
        raise ManifestValidationError(
            "manifest widget.type '%s' is not an allowed component" % widget_type
        )
    size = raw.get("size")
    if size is not None and not isinstance(size, dict):
        raise ManifestValidationError("manifest widget.size must be an object")
    config = raw.get("config")
    if config is not None and not isinstance(config, dict):
        raise ManifestValidationError("manifest widget.config must be an object")
    return WidgetManifest(
        id=widget_id,
        type=widget_type,
        title=str(raw.get("title") or ""),
        size=size,
        config=config or {},
    )


def validate_manifest(raw: Any) -> ModuleManifest:
    """Validate a raw manifest dict; raises ManifestValidationError on any
    unsafe or malformed field. Unknown fields are ignored (additive)."""
    if not isinstance(raw, dict):
        raise ManifestValidationError("manifest must be an object")
    module_id = _require_str(raw.get("id"), "id")
    _require_str(raw.get("type") or "module", "type", allow_empty=True)
    version = str(raw.get("version") or "1.0.0")
    display_name = str(raw.get("display_name") or module_id)
    route = _require_str(raw.get("route") or "/os/" + module_id, "route")
    if not route.startswith("/") or "\\" in route or "\x00" in route:
        raise ManifestValidationError("manifest.route must be a safe absolute path")

    joe_context = raw.get("joe_context") or {}
    if not isinstance(joe_context, dict):
        raise ManifestValidationError("manifest.joe_context must be an object")
    context_kind = str(joe_context.get("kind") or "none")
    if context_kind not in ALLOWED_JOE_CONTEXT_KINDS:
        raise ManifestValidationError(
            "manifest.joe_context.kind '%s' is not allowed" % context_kind
        )

    form_factors = raw.get("supported_form_factors") or ["phone", "tablet", "laptop", "desktop"]
    if not isinstance(form_factors, list) or not all(isinstance(f, str) for f in form_factors):
        raise ManifestValidationError("manifest.supported_form_factors must be a string list")

    widgets_raw = raw.get("widgets") or []
    if not isinstance(widgets_raw, list):
        raise ManifestValidationError("manifest.widgets must be a list")
    widgets = [_validate_widget(w) for w in widgets_raw]

    for list_field in ("required_permissions", "required_capabilities", "commands",
                       "actions", "data_sources", "feature_flags", "policy_requirements"):
        value = raw.get(list_field)
        if value is not None and (not isinstance(value, list) or not all(isinstance(i, str) for i in value)):
            raise ManifestValidationError("manifest.%s must be a string list" % list_field)

    visibility = str(raw.get("visibility") or "visible")
    if visibility not in ("visible", "hidden", "disabled"):
        raise ManifestValidationError("manifest.visibility must be visible|hidden|disabled")

    return ModuleManifest(
        id=module_id,
        type=str(raw.get("type") or "module"),
        version=version,
        display_name=display_name,
        description=str(raw.get("description") or ""),
        icon=str(raw.get("icon") or ""),
        category=str(raw.get("category") or ""),
        subcategory=str(raw.get("subcategory") or ""),
        route=route,
        supported_form_factors=form_factors,
        required_permissions=list(raw.get("required_permissions") or []),
        required_capabilities=list(raw.get("required_capabilities") or []),
        commands=list(raw.get("commands") or []),
        actions=list(raw.get("actions") or []),
        data_sources=list(raw.get("data_sources") or []),
        joe_context=joe_context,
        widgets=widgets,
        inspection=bool(raw.get("inspection")),
        feature_flags=list(raw.get("feature_flags") or []),
        policy_requirements=list(raw.get("policy_requirements") or []),
        min_client_version=str(raw.get("min_client_version") or ""),
        visibility=visibility,
        ordering=int(raw.get("ordering") or 0),
        pinned=bool(raw.get("pinned")),
        user_customizable=bool(raw.get("user_customizable")),
        schema_version=int(raw.get("schema_version") or 1),
    )


def module_manifest_from(payload: str) -> ModuleManifest:
    """Parse + validate a manifest from a JSON string (e.g. a stored module
    definition or an API body)."""
    try:
        raw = json.loads(payload)
    except (ValueError, TypeError) as error:
        raise ManifestValidationError("manifest is not valid JSON") from error
    return validate_manifest(raw)
