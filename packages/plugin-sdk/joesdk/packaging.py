"""Packaging and integrity utilities for the JoeOS Plugin SDK.

Validates manifests, computes canonical package inventories, and builds
versioned plugin packages (.zip). Used by the CLI and the test harness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MANIFEST_FILE = "manifest.json"
INVENTORY_FILE = "inventory.json"

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}\.[a-z][a-z0-9_-]{0,39}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$")

# Mirrors the server permission catalog so the SDK can reject undeclared
# permissions before a package ever reaches the platform.
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


class ValidationError(RuntimeError):
    pass


def validate_manifest(data: Dict[str, Any]) -> None:
    """Strictly validate a manifest dictionary without executing code."""
    if not isinstance(data, dict):
        raise ValidationError("manifest must be an object.")
    plugin_id = data.get("id")
    if not isinstance(plugin_id, str) or _PLUGIN_ID.fullmatch(plugin_id) is None:
        raise ValidationError("invalid plugin id.")
    version = data.get("version")
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise ValidationError("version must be major.minor.patch.")
    publisher = data.get("publisher")
    if not isinstance(publisher, dict) or not publisher.get("id"):
        raise ValidationError("publisher.id is required.")
    if not plugin_id.startswith(str(publisher.get("id")) + "."):
        raise ValidationError("plugin id must start with its publisher id.")
    entry = data.get("entry_point")
    if not isinstance(entry, dict) or _MODULE.fullmatch(str(entry.get("module") or "")) is None:
        raise ValidationError("entry_point.module must be a safe dotted module name.")
    api_version = data.get("api_version", 1)
    if not isinstance(api_version, int) or api_version < 1 or api_version > 1:
        raise ValidationError("api_version is unsupported.")
    for decl in data.get("required_permissions", []) + data.get("optional_permissions", []):
        if decl.get("permission") not in _PERMISSIONS:
            raise ValidationError("undeclared permission: %r" % decl.get("permission"))
    contribution_ids = [c.get("id") for c in data.get("contributions", [])]
    if len(contribution_ids) != len(set(contribution_ids)):
        raise ValidationError("duplicate contribution ids.")
    command_ids = [
        command
        for c in data.get("contributions", [])
        if c.get("type") == "command"
        for command in c.get("commands", [])
    ]
    if len(command_ids) != len(set(command_ids)):
        raise ValidationError("duplicate command ids.")
    for dep in data.get("dependencies", []):
        if _PLUGIN_ID.fullmatch(str(dep.get("plugin_id") or "")) is None:
            raise ValidationError("invalid dependency plugin id.")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_integrity(plugin_dir: str) -> Dict[str, Any]:
    """Compute the canonical inventory for a plugin directory."""
    root = Path(plugin_dir).resolve()
    if not root.is_dir():
        raise ValidationError("plugin directory does not exist.")
    inventory: Dict[str, str] = {}
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", "__pycache__"}]
        for name in files:
            if name == INVENTORY_FILE:
                continue
            path = Path(current) / name
            if path.is_symlink():
                raise ValidationError("plugin directory contains a symbolic link.")
            relative = str(path.relative_to(root)).replace(os.sep, "/")
            inventory[relative] = _file_sha256(path)
    root_hash = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"algorithm": "sha256", "root_hash": root_hash, "files": inventory}


def validate_package(plugin_dir: str) -> Tuple[bool, str]:
    """Validate a plugin directory (manifest + entry point + safe file set)."""
    root = Path(plugin_dir).resolve()
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        return False, "manifest.json is missing."
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False, "manifest.json is not valid JSON."
    try:
        validate_manifest(data)
    except ValidationError as exc:
        return False, str(exc)
    module = str(data["entry_point"]["module"]).replace(".", "/") + ".py"
    if not (root / module).is_file():
        return False, "entry point module is missing."
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", "__pycache__"}]
        for name in files:
            path = Path(current) / name
            if path.is_symlink():
                return False, "package contains a symbolic link."
            if name.endswith((".pyc", ".pyo")):
                return False, "package contains a compiled artifact."
            try:
                path.resolve().relative_to(root)
            except ValueError:
                return False, "package contains a path outside its root."
    return True, "ok"


def package_plugin(plugin_dir: str, output_path: str, *, include_inventory: bool = True) -> str:
    """Build a versioned plugin package (.zip) with an integrity inventory."""
    ok, reason = validate_package(plugin_dir)
    if not ok:
        raise ValidationError(reason)
    root = Path(plugin_dir).resolve()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if include_inventory:
        inventory = calculate_integrity(str(root))
        inventory_path = root / INVENTORY_FILE
        inventory_path.write_text(json.dumps(inventory, sort_keys=True), encoding="utf-8")
    try:
        with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
            for current, directories, files in os.walk(root):
                directories[:] = [name for name in directories if name not in {".git", "__pycache__"}]
                for name in files:
                    path = Path(current) / name
                    relative = str(path.relative_to(root)).replace(os.sep, "/")
                    archive.write(str(path), relative)
    finally:
        if include_inventory:
            (root / INVENTORY_FILE).unlink(missing_ok=True)
    return str(output)