"""Manifest helpers for the JoeOS Plugin SDK.

Provides a thin, friendly builder layer over the strict manifest schema and
compatibility checks, so plugin authors never hand-write fragile JSON. The
schema mirrors the server's ``server.plugins.models`` contracts.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

API_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}\.[a-z][a-z0-9_-]{0,39}$")
_PERMISSIONS = frozenset(
    {
        "filesystem.read_selected_file",
        "filesystem.read_project_files",
        "filesystem.propose_project_edit",
        "filesystem.create_project_files",
        "filesystem.delete_project_files",
        "filesystem.access_outside_projects",
        "terminal.request_registered_command",
        "terminal.request_execution",
        "terminal.start_managed_process",
        "terminal.stop_owned_process",
        "network.access_declared_domains",
        "network.access_local_network",
        "network.access_internet",
        "network.host_local_listener",
        "ai.use_local_runtime",
        "ai.use_approved_cloud_provider",
        "ai.register_provider",
        "ai.register_runtime",
        "ai.submit_embeddings",
        "ai.use_vision",
        "ai.use_speech",
        "memory.read_task_memory",
        "memory.read_project_memory",
        "memory.read_user_memory",
        "memory.propose_memory",
        "memory.write_approved_extension_memory",
        "intelligence.search_project",
        "intelligence.inspect_symbols",
        "intelligence.inspect_dependencies",
        "intelligence.inspect_git_history",
        "intelligence.contribute_analyzer_results",
        "secrets.request_named_extension_secret",
        "secrets.request_project_credential_reference",
        "secrets.request_provider_token",
        "ui.register_panel",
        "ui.register_view",
        "ui.register_menu",
        "ui.register_status_item",
        "ui.register_editor",
        "ui.register_theme",
        "hardware.microphone",
        "hardware.camera",
        "hardware.display",
        "hardware.smart_glasses",
        "hardware.serial_device",
        "hardware.bluetooth_device",
        "hardware.usb_device",
        "notification.publish",
        "events.subscribe",
        "automation.register_action",
        "automation.register_trigger",
        "storage.extension_data",
        "settings.contribute",
    }
)


def permission(name: str, purpose: str = "") -> Dict[str, str]:
    if name not in _PERMISSIONS:
        raise ValueError("unknown permission: %r" % name)
    return {"permission": name, "purpose": purpose}


def contribution(
    type: str,
    id: str,
    *,
    title: str = "",
    description: str = "",
    commands: Tuple[str, ...] = (),
    requires_permissions: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    return {
        "type": type,
        "id": id,
        "title": title,
        "description": description,
        "commands": list(commands),
        "requires_permissions": list(requires_permissions),
    }


def dependency(plugin_id: str, version_range: str = "*", optional: bool = False) -> Dict[str, Any]:
    return {"plugin_id": plugin_id, "version_range": version_range, "optional": optional}


def setting(
    key: str,
    type: str = "string",
    *,
    title: str = "",
    description: str = "",
    default: Any = None,
    scope: str = "global",
    sensitive: bool = False,
    choices: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "key": key,
        "title": title,
        "description": description,
        "type": type,
        "default": default,
        "scope": scope,
        "sensitive": sensitive,
    }
    if choices is not None:
        item["validation"] = {"choices": choices}
    return item


def manifest(
    *,
    plugin_id: str,
    name: str,
    version: str,
    publisher_id: str,
    publisher_name: str,
    module: str,
    function: str = "handle",
    description: str = "",
    api_version: int = API_VERSION,
    min_joeos_version: str = "0.0.0",
    activation_events: Tuple[str, ...] = (),
    contributions: Tuple[Dict[str, Any], ...] = (),
    required_permissions: Tuple[Dict[str, Any], ...] = (),
    optional_permissions: Tuple[Dict[str, Any], ...] = (),
    dependencies: Tuple[Dict[str, Any], ...] = (),
    conflicts: Tuple[str, ...] = (),
    settings: Tuple[Dict[str, Any], ...] = (),
    development: bool = False,
) -> Dict[str, Any]:
    if not _PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError("plugin_id must look like publisher.name.")
    if not plugin_id.startswith(publisher_id + "."):
        raise ValueError("plugin_id must start with publisher_id.")
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
        "publisher": {"id": publisher_id, "name": publisher_name},
        "api_version": api_version,
        "min_joeos_version": min_joeos_version,
        "entry_point": {"runtime": "python", "module": module, "function": function},
        "activation_events": list(activation_events),
        "contributions": list(contributions),
        "required_permissions": list(required_permissions),
        "optional_permissions": list(optional_permissions),
        "dependencies": list(dependencies),
        "conflicts": list(conflicts),
        "settings": list(settings),
        "development": development,
    }


class PluginManifest:
    """A small validated wrapper around a manifest dictionary."""

    def __init__(self, data: Dict[str, Any]) -> None:
        validate_manifest(data)
        self.data = data

    @property
    def plugin_id(self) -> str:
        return str(self.data["id"])

    @property
    def version(self) -> str:
        return str(self.data["version"])

    def dumps(self) -> str:
        return json.dumps(self.data, indent=2, sort_keys=True)