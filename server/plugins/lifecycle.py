"""Lifecycle management for the JoeOS Plugin Platform.

Discovery, validation, installation, activation, deactivation, enable,
disable, update, rollback, quarantine, safe mode, and uninstall are explicit,
idempotent where practical, and never execute plugin code during manifest
validation. Activation is lazy and event-driven; a plugin is never activated
before installation and permission review complete.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .compatibility import eval_manifest_compatibility
from .contributions import ContributionError, ContributionRegistry
from .dependency import PluginDependencyResolver
from .events import EventGateway
from .extension_data import ExtensionSettingsService, ExtensionStorageService
from .health import PluginHealthService
from .host import ExtensionHostManager, HostError
from .integrity import (
    IntegrityError,
    compute_inventory,
    verify_file_set,
    verify_inventory,
    write_inventory_file,
)
from .models import (
    ActivityEvent,
    CompatibilityResult,
    PluginLifecycleState,
    PluginManifest,
    PluginRecord,
)
from .permissions import PermissionError, PermissionManager
from .publishers import PublisherService
from .resources import ResourceGovernor, ResourceLimitError
from .secrets import ExtensionSecretBroker
from .signature import evaluate_signature

MANIFEST_FILE = "manifest.json"
INVENTORY_FILE = "inventory.json"

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_FILES = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PluginLifecycleError(RuntimeError):
    pass


def _normalize_json(value):
    """Recursively convert JSON arrays into tuples for strict tuple fields."""
    if isinstance(value, list):
        return tuple(_normalize_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_json(item) for key, item in value.items()}
    return value


def parse_manifest(payload: dict) -> PluginManifest:
    """Strictly validate a manifest without executing any package code."""
    if not isinstance(payload, dict):
        raise PluginLifecycleError("manifest must be a JSON object.")
    # JSON arrays are decoded as lists; the internal model uses immutable
    # tuples, so normalize every collection recursively to a tuple.
    payload = _normalize_json(payload)
    try:
        return PluginManifest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise PluginLifecycleError(
            "manifest validation failed: %s (%s)" % (first.get("msg"), ".".join(map(str, first.get("loc", []))))
        ) from exc


class SafeModeState:
    def __init__(self) -> None:
        self.active = False
        self._lock = threading.RLock()

    def enter(self) -> None:
        with self._lock:
            self.active = True

    def exit(self) -> None:
        with self._lock:
            self.active = False


class UpdateService:
    """Controlled update and rollback with rollback checkpoints."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def record(
        self, *, plugin_id: str, previous_version: str, target_version: str, outcome: str, reason: str = ""
    ) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_update_history (
                    plugin_id, previous_version, target_version, outcome, reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (plugin_id, previous_version, target_version, outcome, reason[:300], _now()),
            )

    def history(self, *, plugin_id: str) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_update_history WHERE plugin_id = ? ORDER BY id DESC LIMIT 20",
                (plugin_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)


class QuarantineService:
    """Quarantine that halts activation, cancels work, and preserves evidence."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def quarantine(
        self,
        *,
        plugin_id: str,
        reason: str,
        health: PluginHealthService,
        contributions: ContributionRegistry,
        host: ExtensionHostManager,
    ) -> None:
        now = _now()
        host.shutdown(plugin_id)
        contributions.set_plugin_state(plugin_id=plugin_id, state="removed")
        health.set_health(plugin_id=plugin_id, state="quarantined", message=reason, host_state="stopped")
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE plugin_records
                SET lifecycle_state = 'quarantined', enabled_state = 'quarantined',
                    quarantine_reason = ?, updated_at = ?
                WHERE plugin_id = ?
                """,
                (reason[:300], now, plugin_id),
            )

    def restore(
        self,
        *,
        plugin_id: str,
        lifecycle: "ExtensionLifecycleManager",
        previous_state: str = "disabled",
    ) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE plugin_records
                SET lifecycle_state = 'installed', enabled_state = ?,
                    quarantine_reason = '', updated_at = ?
                WHERE plugin_id = ?
                """,
                (previous_state, _now(), plugin_id),
            )


class DevelopmentHost:
    """Local development-directory linking with a clearly visible dev mode."""

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir
        self._linked: Dict[str, Path] = {}
        self._lock = threading.RLock()

    def link(self, *, plugin_id: str, source_dir: str) -> str:
        source = Path(source_dir).resolve()
        if not (source / MANIFEST_FILE).is_file():
            raise PluginLifecycleError("linked directory has no manifest.json.")
        with self._lock:
            self._linked[plugin_id] = source
        return str(source)

    def unlink(self, *, plugin_id: str) -> None:
        with self._lock:
            self._linked.pop(plugin_id, None)

    def install_path(self, *, plugin_id: str) -> Optional[str]:
        with self._lock:
            path = self._linked.get(plugin_id)
        return str(path) if path else None


class ExtensionLifecycleManager:
    """The authoritative lifecycle state machine for plugins."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        install_root: str,
        package_root: str,
        permissions: PermissionManager,
        publishers: PublisherService,
        contributions: ContributionRegistry,
        health: PluginHealthService,
        events: EventGateway,
        resources: ResourceGovernor,
        settings: ExtensionSettingsService,
        storage: ExtensionStorageService,
        secrets: ExtensionSecretBroker,
        host: ExtensionHostManager,
        dependency_resolver: PluginDependencyResolver,
        updates: UpdateService,
        quarantine: QuarantineService,
        first_party_publishers: Optional[Sequence[str]] = None,
        joeos_version: str = "2.0.0",
        signed_permission: Optional[str] = None,
        dev: Optional[DevelopmentHost] = None,
        safe_mode: Optional[SafeModeState] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._install_root = Path(install_root)
        self._package_root = Path(package_root)
        self._permissions = permissions
        self._publishers = publishers
        self._contributions = contributions
        self._health = health
        self._events = events
        self._resources = resources
        self._settings = settings
        self._storage = storage
        self._secrets = secrets
        self._host = host
        self._dependency_resolver = dependency_resolver
        self._updates = updates
        self._quarantine = quarantine
        self._first_party = set(first_party_publishers or ())
        self._joeos_version = joeos_version
        self._signed_permission = signed_permission
        self._dev = dev or DevelopmentHost(self._install_root)
        self._safe_mode = safe_mode or SafeModeState()
        self._lock = threading.RLock()
        self._install_root.mkdir(parents=True, exist_ok=True)
        self._package_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Discovery & records
    # ------------------------------------------------------------------

    def record_exists(self, plugin_id: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT plugin_id FROM plugin_records WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
        return row is not None

    def get_record(self, plugin_id: str) -> Optional[PluginRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_records WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_records(self) -> Tuple[PluginRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM plugin_records ORDER BY display_name").fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _row_to_record(self, row: sqlite3.Row) -> PluginRecord:
        manifest = json.loads(str(row["manifest"]))
        return PluginRecord(
            plugin_id=str(row["plugin_id"]),
            display_name=str(row["display_name"]),
            version=str(row["version"]),
            publisher_id=str(row["publisher_id"]),
            manifest=parse_manifest(manifest),
            source=str(row["source"]),
            integrity_state=str(row["integrity_state"]),
            signature_state=str(row["signature_state"]),
            package_hash=str(row["package_hash"]),
            signer_fingerprint=str(row["signer_fingerprint"]),
            lifecycle_state=str(row["lifecycle_state"]),
            health_state=str(row["health_state"]),
            enabled_state=str(row["enabled_state"]),
            enabled_scope=str(row["enabled_scope"]),
            quarantine_reason=str(row["quarantine_reason"]),
            crash_count=int(row["crash_count"]),
            install_path=str(row["install_path"]),
            package_path=str(row["package_path"]),
            installed_at=str(row["installed_at"]),
            updated_at=str(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def install_package(self, package_path: str, *, source: str = "local_package", approval: Optional[dict] = None) -> PluginRecord:
        """Install a validated plugin package archive (.zip) into managed storage."""
        staged = self._stage_package(package_path)
        return self._commit_install(
            manifest=staged["manifest"],
            temp_dir=staged["dir"],
            package=staged["package"],
            inventory_root=staged["inventory_root"],
            source=source,
            approval=approval,
        )

    def _stage_package(self, package_path: str) -> dict:
        """Extract and validate a package archive into a staging directory."""
        package = Path(package_path).resolve()
        if not package.is_file():
            raise PluginLifecycleError("package archive does not exist.")
        if package.stat().st_size > MAX_PACKAGE_BYTES:
            raise PluginLifecycleError("package exceeds the size limit.")
        with zipfile.ZipFile(str(package), "r") as archive:
            names = archive.namelist()
            if len(names) > MAX_UNPACKED_FILES:
                raise PluginLifecycleError("package contains too many files.")
            if any(name.startswith("/") or ".." in name for name in names):
                raise PluginLifecycleError("package contains an unsafe path.")
            temp_dir = Path(tempfile.mkdtemp(prefix="joeos-plugin-"))
            try:
                archive.extractall(str(temp_dir))
                manifest_payload = self._read_manifest(temp_dir)
                manifest = parse_manifest(manifest_payload)
                ok, reason = verify_file_set(str(temp_dir), manifest_payload)
                if not ok:
                    raise PluginLifecycleError(reason)
                inventory, inventory_root = compute_inventory(str(temp_dir))
                write_inventory_file(str(temp_dir))
                self._verify_no_conflicts(manifest, temp_dir)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
        return {
            "manifest": manifest,
            "dir": temp_dir,
            "package": package,
            "inventory_root": inventory_root,
        }

    def install_directory(self, directory: str, *, source: str = "local_development") -> PluginRecord:
        """Install from a local directory (development or first-party source)."""
        source_dir = Path(directory).resolve()
        manifest_payload = self._read_manifest(source_dir)
        manifest = parse_manifest(manifest_payload)
        ok, reason = verify_file_set(str(source_dir), manifest_payload)
        if not ok:
            raise PluginLifecycleError(reason)
        inventory, inventory_root = compute_inventory(str(source_dir))
        write_inventory_file(str(source_dir))
        self._verify_no_conflicts(manifest, source_dir)
        # Directory installs are the local-development path; the manifest must
        # be explicitly marked as development and no production approval is
        # required because the plugin is never treated as trusted production.
        if not manifest.development:
            raise PluginLifecycleError(
                "directory install requires a manifest with development: true."
            )
        return self._commit_install(
            manifest=manifest,
            temp_dir=source_dir,
            package=None,
            inventory_root=inventory_root,
            source=source,
            approval=None,
            copy_content=False,
        )

    def _read_manifest(self, root: Path) -> dict:
        manifest_path = root / MANIFEST_FILE
        if not manifest_path.is_file():
            raise PluginLifecycleError("manifest.json is missing.")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise PluginLifecycleError("manifest.json is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise PluginLifecycleError("manifest.json must be an object.")
        return payload

    def _verify_no_conflicts(self, manifest: PluginManifest, source_dir: Path) -> None:
        for conflict in manifest.conflicts:
            if self.record_exists(conflict):
                raise PluginLifecycleError("conflicts with installed plugin %s." % conflict)
        with self._connection_factory() as connection:
            owners = {
                str(row["contribution_id"]): str(row["plugin_id"])
                for row in connection.execute(
                    "SELECT contribution_id, plugin_id FROM plugin_contributions"
                ).fetchall()
            }
        for contribution in manifest.contributions:
            contribution_id = "%s.%s" % (manifest.id, contribution.id)
            existing = owners.get(contribution_id)
            if existing is not None and existing != manifest.id:
                raise PluginLifecycleError(
                    "contribution %s conflicts with plugin %s." % (contribution_id, existing)
                )

    def _commit_install(
        self,
        *,
        manifest: PluginManifest,
        temp_dir: Path,
        package: Optional[Path],
        inventory_root: str,
        source: str,
        approval: Optional[dict],
        copy_content: bool = True,
    ) -> PluginRecord:
        plugin_id = manifest.id
        with self._lock:
            if self.record_exists(plugin_id):
                raise PluginLifecycleError("plugin %s is already installed." % plugin_id)
            compatibility = self._compatibility_for(manifest)
            if compatibility.decision == "incompatible":
                raise PluginLifecycleError(
                    "plugin is incompatible: " + "; ".join(compatibility.reasons)
                )
            self._ensure_publisher(manifest)
            if not manifest.development and not approval:
                raise PluginLifecycleError(
                    "installation requires user approval for %s." % plugin_id
                )
            install_dir = self._install_root / plugin_id
            install_dir.mkdir(parents=True, exist_ok=True)
            if copy_content:
                for item in temp_dir.iterdir():
                    target = install_dir / item.name
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.move(str(item), str(target))
                install_path = str(install_dir)
            else:
                # Development install: keep content in place and record the
                # source directory as the install path.
                install_path = str(temp_dir)
            # Recompute inventory against the final installed location.
            final_inventory, final_root = compute_inventory(install_path)
            write_inventory_file(install_path)
            signature_state = evaluate_signature(
                inventory_root_hash=final_root,
                plugin_id=plugin_id,
                version=manifest.version,
                encoded_signature=approval.get("signature", "") if approval else "",
                signer_public_key=approval.get("public_key", "") if approval else "",
                trusted_fingerprints=tuple(self._publisher_trusted_fingerprints(manifest.publisher.id)),
                first_party_fingerprints=tuple(self._first_party_fingerprints(manifest.publisher.id)),
                local_modified=False,
            )
            now = _now()
            publisher = self._publishers.require(manifest.publisher.id)
            first_party = publisher.first_party
            with self._connection_factory() as connection:
                connection.execute(
                    """
                    INSERT INTO plugin_records (
                        plugin_id, display_name, version, publisher_id, manifest, source,
                        integrity_state, signature_state, package_hash, signer_fingerprint,
                        lifecycle_state, health_state, enabled_state, enabled_scope,
                        quarantine_reason, crash_count, install_path, package_path,
                        installed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'valid', ?, ?, ?, 'installed', 'inactive',
                              'disabled', 'global', '', 0, ?, ?, ?, ?)
                    """,
                    (
                        plugin_id,
                        manifest.name,
                        manifest.version,
                        manifest.publisher.id,
                        json.dumps(manifest.model_dump()),
                        source,
                        signature_state.state,
                        final_root,
                        signature_state.signer_fingerprint,
                        install_path,
                        str(package) if package else "",
                        now,
                        now,
                    ),
                )
                self._publishers.set_known_plugin_ids(
                    manifest.publisher.id,
                    tuple(self._plugin_ids_for_publisher(manifest.publisher.id)),
                )
            # Register contributions in a disabled state.
            for contribution in manifest.contributions:
                try:
                    self._contributions.register(
                        plugin_id=plugin_id,
                        contribution_id="%s.%s" % (plugin_id, contribution.id),
                        contribution_type=contribution.type,
                        title=contribution.title or contribution.id,
                        description=contribution.description,
                        commands=tuple(contribution.commands),
                        requires_permissions=tuple(contribution.requires_permissions),
                    )
                except ContributionError as exc:
                    raise PluginLifecycleError(str(exc)) from exc
            self._health.set_health(plugin_id=plugin_id, state="inactive", host_state="not_running", message="Installed; awaiting permissions and activation.")
            self._health.record_activity(plugin_id=plugin_id, kind="installed", message="Plugin installed (%s)." % source, level="success")
            return self.get_record(plugin_id)

    def _compatibility_for(self, manifest: PluginManifest) -> CompatibilityResult:
        installed = {record.plugin_id for record in self.list_records()}
        return eval_manifest_compatibility(
            manifest=manifest,
            joeos_version=self._joeos_version,
            platform_compliant=True,
            available_plugins=installed,
        )

    def _ensure_publisher(self, manifest: PluginManifest) -> None:
        if manifest.publisher.id in self._first_party:
            self._publishers.register_first_party(manifest.publisher.id, manifest.publisher.name)
            return
        existing = self._publishers.get(manifest.publisher.id)
        if existing is None:
            # Register an unverified publisher so it is inspectable and its
            # trust state can be reviewed; it is never treated as trusted.
            self._publishers.set_verification_state(manifest.publisher.id, "unknown")

    def _publisher_trusted_fingerprints(self, publisher_id: str) -> Sequence[str]:
        record = self._publishers.get(publisher_id)
        return record.signing_fingerprints if record else ()

    def _first_party_fingerprints(self, publisher_id: str) -> Sequence[str]:
        record = self._publishers.get(publisher_id)
        if record and record.first_party:
            return record.signing_fingerprints
        return ()

    def _plugin_ids_for_publisher(self, publisher_id: str) -> Sequence[str]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT plugin_id FROM plugin_records WHERE publisher_id = ?", (publisher_id,)
            ).fetchall()
        return tuple(str(row["plugin_id"]) for row in rows)

    # ------------------------------------------------------------------
    # Permissions & enable
    # ------------------------------------------------------------------

    def request_permissions(self, plugin_id: str) -> PluginRecord:
        record = self.require_record(plugin_id)
        if record.lifecycle_state == "quarantined":
            raise PluginLifecycleError("plugin is quarantined.")
        summary = self._permissions.summary(
            plugin_id=plugin_id,
            required=[decl.permission for decl in record.manifest.required_permissions],
            optional=[decl.permission for decl in record.manifest.optional_permissions],
        )
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_records SET lifecycle_state = 'pending_permissions', updated_at = ? WHERE plugin_id = ?",
                (_now(), plugin_id),
            )
        self._health.set_health(plugin_id=plugin_id, state="permission_blocked", message="Awaiting permission decisions.")
        return self.get_record(plugin_id)

    def grant_permission(
        self, *, plugin_id: str, permission: str, scope: str = "granted_global", scope_target: str = ""
    ) -> None:
        record = self.require_record(plugin_id)
        declared = {
            decl.permission for decl in record.manifest.required_permissions
        } | {decl.permission for decl in record.manifest.optional_permissions}
        if permission not in declared:
            raise PluginLifecycleError("permission %r is not declared by this plugin." % permission)
        self._permissions.grant(
            plugin_id=plugin_id,
            permission=permission,
            scope=scope,
            scope_target=scope_target,
            reason="user grant",
        )
        self._health.record_activity(plugin_id=plugin_id, kind="permission_changed", message="Granted %s." % permission, level="success")

    def enable(self, plugin_id: str, *, scope: str = "global", workspace: str = "", project: str = "") -> PluginRecord:
        if self._safe_mode.active and not self._is_first_party(plugin_id):
            raise PluginLifecycleError("Safe Mode is active; third-party plugins are disabled.")
        record = self.require_record(plugin_id)
        if record.lifecycle_state == "quarantined":
            raise PluginLifecycleError("plugin is quarantined; restore before enabling.")
        summary = self._permissions.summary(
            plugin_id=plugin_id,
            required=[decl.permission for decl in record.manifest.required_permissions],
            optional=[decl.permission for decl in record.manifest.optional_permissions],
        )
        # Required permissions that are not granted appear in summary.pending;
        # optional permissions that were never granted do not block enabling.
        required_declared = {
            decl.permission for decl in record.manifest.required_permissions
        }
        missing = [permission for permission in summary.pending if permission in required_declared]
        if missing:
            raise PluginLifecycleError("required permissions are not granted: %s" % ", ".join(missing))
        now = _now()
        with self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE plugin_records SET enabled_state = ?, enabled_scope = ?,
                    lifecycle_state = 'installed', updated_at = ?
                WHERE plugin_id = ?
                """,
                (self._enabled_state_for(scope, workspace, project), scope, now, plugin_id),
            )
        self._health.set_health(plugin_id=plugin_id, state="disabled", message="Enabled for %s." % scope)
        return self.get_record(plugin_id)

    @staticmethod
    def _enabled_state_for(scope: str, workspace: str, project: str) -> str:
        if scope == "workspace" and workspace:
            return "enabled_workspace"
        if scope == "project" and project:
            return "enabled_project"
        return "enabled"

    def disable(self, plugin_id: str) -> PluginRecord:
        record = self.require_record(plugin_id)
        if record.lifecycle_state == "activating" or record.lifecycle_state == "active":
            self.deactivate(plugin_id)
        self._host.shutdown(plugin_id)
        self._contributions.set_plugin_state(plugin_id=plugin_id, state="removed")
        self._resources._active_jobs.pop(plugin_id, None)
        now = _now()
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_records SET enabled_state = 'disabled', lifecycle_state = 'disabled', updated_at = ? WHERE plugin_id = ?",
                (now, plugin_id),
            )
        self._health.set_health(plugin_id=plugin_id, state="disabled", host_state="not_running", message="Disabled.")
        self._health.record_activity(plugin_id=plugin_id, kind="disabled", message="Plugin disabled.", level="info")
        return self.get_record(plugin_id)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate(self, plugin_id: str) -> PluginRecord:
        record = self.require_record(plugin_id)
        if record.enabled_state == "disabled" or record.enabled_state == "quarantined":
            raise PluginLifecycleError("plugin is not enabled.")
        if self._safe_mode.active and not self._is_first_party(plugin_id):
            raise PluginLifecycleError("Safe Mode is active; third-party plugins are disabled.")
        self._verify_integrity_before_activation(record)
        summary = self._permissions.summary(
            plugin_id=plugin_id,
            required=[decl.permission for decl in record.manifest.required_permissions],
            optional=[decl.permission for decl in record.manifest.optional_permissions],
        )
        required_declared = {
            decl.permission for decl in record.manifest.required_permissions
        }
        pending_required = [permission for permission in summary.pending if permission in required_declared]
        if pending_required:
            raise PluginLifecycleError("required permissions are pending: %s" % ", ".join(pending_required))
        blocks, _warnings = self._dependency_resolver.resolve(plugin_id)
        if blocks:
            raise PluginLifecycleError("dependencies not satisfiable: %s" % "; ".join(blocks))
        self._health.record_activation(plugin_id=plugin_id)
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_records SET lifecycle_state = 'activating', updated_at = ? WHERE plugin_id = ?",
                (_now(), plugin_id),
            )
        try:
            manifest = record.manifest
            install_dir = self._dev.install_path(plugin_id=plugin_id) or record.install_path
            self._host.invoke(
                plugin_id=plugin_id,
                plugin_dir=install_dir,
                manifest=manifest,
                method="lifecycle.activate",
                params={},
            )
        except HostError as exc:
            self._mark_crashed(plugin_id, str(exc))
            raise PluginLifecycleError("activation failed: %s" % exc) from exc
        self._contributions.set_plugin_state(plugin_id=plugin_id, state="active")
        self._health.record_success(plugin_id=plugin_id)
        self._health.set_health(plugin_id=plugin_id, state="healthy", host_state="running", message="Active.")
        self._health.record_activity(plugin_id=plugin_id, kind="activated", message="Plugin activated.", level="success")
        now = _now()
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_records SET lifecycle_state = 'active', health_state = 'healthy', updated_at = ? WHERE plugin_id = ?",
                (now, plugin_id),
            )
        return self.get_record(plugin_id)

    def deactivate(self, plugin_id: str) -> PluginRecord:
        record = self.require_record(plugin_id)
        try:
            install_dir = self._dev.install_path(plugin_id=plugin_id) or record.install_path
            self._host.invoke(
                plugin_id=plugin_id,
                plugin_dir=install_dir,
                manifest=record.manifest,
                method="lifecycle.deactivate",
                params={},
            )
        except HostError:
            pass
        self._host.shutdown(plugin_id)
        self._contributions.set_plugin_state(plugin_id=plugin_id, state="registered")
        self._health.set_health(plugin_id=plugin_id, state="inactive", host_state="not_running", message="Deactivated.")
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_records SET lifecycle_state = 'installed', updated_at = ? WHERE plugin_id = ?",
                (_now(), plugin_id),
            )
        self._health.record_activity(plugin_id=plugin_id, kind="deactivated", message="Plugin deactivated.", level="info")
        return self.get_record(plugin_id)

    def invoke_contribution(self, *, plugin_id: str, contribution_id: str, params: Optional[dict] = None) -> dict:
        record = self.require_record(plugin_id)
        if record.lifecycle_state != "active":
            raise PluginLifecycleError("plugin is not active.")
        contribution = self._contributions.get(contribution_id=contribution_id)
        if contribution is None or contribution.plugin_id != plugin_id:
            raise PluginLifecycleError("contribution not found.")
        install_dir = self._dev.install_path(plugin_id=plugin_id) or record.install_path
        return self._host.invoke(
            plugin_id=plugin_id,
            plugin_dir=install_dir,
            manifest=record.manifest,
            method="contribution.invoke",
            params={"contribution_id": contribution_id, "params": params or {}},
        )

    def _verify_integrity_before_activation(self, record: PluginRecord) -> None:
        install_dir = self._dev.install_path(plugin_id=record.plugin_id) or record.install_path
        try:
            verified = verify_inventory(str(install_dir), record.package_hash)
        except IntegrityError:
            verified = False
        if not verified:
            self._quarantine.quarantine(
                plugin_id=record.plugin_id,
                reason="package integrity changed since installation.",
                health=self._health,
                contributions=self._contributions,
                host=self._host,
            )
            raise PluginLifecycleError("package integrity changed; plugin quarantined.")

    def _mark_crashed(self, plugin_id: str, error: str) -> None:
        self._health.record_crash(plugin_id=plugin_id, error=error)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT crash_count FROM plugin_records WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            crashes = (int(row["crash_count"]) if row else 0) + 1
            connection.execute(
                "UPDATE plugin_records SET crash_count = ?, lifecycle_state = 'crashed', updated_at = ? WHERE plugin_id = ?",
                (crashes, _now(), plugin_id),
            )

    def _is_first_party(self, plugin_id: str) -> bool:
        record = self.get_record(plugin_id)
        if record is None:
            return False
        return record.publisher_id in self._first_party

    def require_record(self, plugin_id: str) -> PluginRecord:
        record = self.get_record(plugin_id)
        if record is None:
            raise PluginLifecycleError("plugin %s is not installed." % plugin_id)
        return record

    # ------------------------------------------------------------------
    # Update & rollback
    # ------------------------------------------------------------------

    def update(self, plugin_id: str, new_package_path: str, *, approval: Optional[dict] = None) -> PluginRecord:
        record = self.require_record(plugin_id)
        with self._lock:
            staged = self._stage_package(new_package_path)
            new_manifest = staged["manifest"]
            if new_manifest.id != plugin_id:
                shutil.rmtree(staged["dir"], ignore_errors=True)
                raise PluginLifecycleError("update package declares a different plugin id.")
            if new_manifest.version == record.version:
                shutil.rmtree(staged["dir"], ignore_errors=True)
                raise PluginLifecycleError("update package version is identical.")
            changed_permissions = self._permission_difference(record, new_manifest)
            self._host.shutdown(plugin_id)
            self._contributions.unregister_all(plugin_id=plugin_id)
            # Replace the installed content in place.
            install_dir = self._install_root / plugin_id
            backup_dir = install_dir.with_name(plugin_id + ".rollback-" + record.version)
            shutil.rmtree(backup_dir, ignore_errors=True)
            if install_dir.exists():
                shutil.move(str(install_dir), str(backup_dir))
            install_dir.mkdir(parents=True, exist_ok=True)
            try:
                for item in staged["dir"].iterdir():
                    shutil.move(str(item), str(install_dir))
            except OSError:
                shutil.rmtree(install_dir, ignore_errors=True)
                if backup_dir.exists():
                    shutil.move(str(backup_dir), str(install_dir))
                shutil.rmtree(staged["dir"], ignore_errors=True)
                raise PluginLifecycleError("update could not be staged; previous version restored.")
            shutil.rmtree(staged["dir"], ignore_errors=True)
            final_inventory, final_root = compute_inventory(str(install_dir))
            write_inventory_file(str(install_dir))
            signature_state = evaluate_signature(
                inventory_root_hash=final_root,
                plugin_id=plugin_id,
                version=new_manifest.version,
                encoded_signature=approval.get("signature", "") if approval else "",
                signer_public_key=approval.get("public_key", "") if approval else "",
                trusted_fingerprints=tuple(self._publisher_trusted_fingerprints(new_manifest.publisher.id)),
                first_party_fingerprints=tuple(self._first_party_fingerprints(new_manifest.publisher.id)),
                local_modified=False,
            )
            now = _now()
            with self._connection_factory() as connection:
                connection.execute(
                    """
                    UPDATE plugin_records
                    SET version = ?, manifest = ?, package_hash = ?,
                        signature_state = ?, signer_fingerprint = ?,
                        lifecycle_state = 'installed', health_state = 'inactive',
                        updated_at = ?
                    WHERE plugin_id = ?
                    """,
                    (
                        new_manifest.version,
                        json.dumps(new_manifest.model_dump()),
                        final_root,
                        signature_state.state,
                        signature_state.signer_fingerprint,
                        now,
                        plugin_id,
                    ),
                )
            self._publishers.set_known_plugin_ids(
                new_manifest.publisher.id,
                tuple(self._plugin_ids_for_publisher(new_manifest.publisher.id)),
            )
            for contribution in new_manifest.contributions:
                self._contributions.register(
                    plugin_id=plugin_id,
                    contribution_id="%s.%s" % (plugin_id, contribution.id),
                    contribution_type=contribution.type,
                    title=contribution.title or contribution.id,
                    description=contribution.description,
                    commands=tuple(contribution.commands),
                    requires_permissions=tuple(contribution.requires_permissions),
                )
            self._updates.record(
                plugin_id=plugin_id,
                previous_version=record.version,
                target_version=new_manifest.version,
                outcome="succeeded",
            )
            self._health.set_health(plugin_id=plugin_id, state="inactive", host_state="not_running", message="Updated to %s." % new_manifest.version)
            self._health.record_activity(plugin_id=plugin_id, kind="updated", message="Updated to %s." % new_manifest.version, level="success")
            if changed_permissions:
                with self._connection_factory() as connection:
                    connection.execute(
                        "UPDATE plugin_records SET lifecycle_state = 'pending_permissions', enabled_state = 'disabled', updated_at = ? WHERE plugin_id = ?",
                        (_now(), plugin_id),
                    )
                self._health.set_health(plugin_id=plugin_id, state="permission_blocked", message="New permissions require review.")
            return self.get_record(plugin_id)

    def rollback(self, plugin_id: str) -> Optional[PluginRecord]:
        record = self.require_record(plugin_id)
        history = self._updates.history(plugin_id=plugin_id)
        if not history:
            raise PluginLifecycleError("no update history to roll back.")
        latest = history[0]
        previous = str(latest["previous_version"])
        backup_dir = self._install_root / (plugin_id + ".rollback-" + previous)
        if not backup_dir.is_dir():
            raise PluginLifecycleError("previous package is not retained for rollback.")
        self.deactivate(plugin_id)
        with self._lock:
            self._host.shutdown(plugin_id)
            install_dir = self._install_root / plugin_id
            current_backup = install_dir.with_name(plugin_id + ".pre-rollback-" + record.version)
            shutil.rmtree(current_backup, ignore_errors=True)
            if install_dir.exists():
                shutil.move(str(install_dir), str(current_backup))
            shutil.move(str(backup_dir), str(install_dir))
            manifest_payload = self._read_manifest(install_dir)
            manifest = parse_manifest(manifest_payload)
            inventory, final_root = compute_inventory(str(install_dir))
            write_inventory_file(str(install_dir))
            now = _now()
            with self._connection_factory() as connection:
                connection.execute(
                    """
                    UPDATE plugin_records
                    SET version = ?, manifest = ?, package_hash = ?,
                        lifecycle_state = 'installed', health_state = 'inactive',
                        updated_at = ?
                    WHERE plugin_id = ?
                    """,
                    (manifest.version, json.dumps(manifest.model_dump()), final_root, now, plugin_id),
                )
            self._contributions.unregister_all(plugin_id=plugin_id)
            for contribution in manifest.contributions:
                self._contributions.register(
                    plugin_id=plugin_id,
                    contribution_id="%s.%s" % (plugin_id, contribution.id),
                    contribution_type=contribution.type,
                    title=contribution.title or contribution.id,
                    description=contribution.description,
                    commands=tuple(contribution.commands),
                    requires_permissions=tuple(contribution.requires_permissions),
                )
            self._updates.record(
                plugin_id=plugin_id,
                previous_version=record.version,
                target_version=manifest.version,
                outcome="rolled_back",
                reason="user-initiated rollback",
            )
            self._health.record_activity(plugin_id=plugin_id, kind="rolled_back", message="Rolled back to %s." % manifest.version, level="warn")
            return self.get_record(plugin_id)

    def _permission_difference(self, old: PluginRecord, new_manifest: PluginManifest) -> Tuple[str, ...]:
        old_set = {decl.permission for decl in old.manifest.required_permissions} | {
            decl.permission for decl in old.manifest.optional_permissions
        }
        new_set = {decl.permission for decl in new_manifest.required_permissions} | {
            decl.permission for decl in new_manifest.optional_permissions
        }
        return tuple(sorted(new_set - old_set))

    def _reapply_grants(self, plugin_id: str, staged: PluginRecord) -> None:
        # Keep existing grants that are still declared; they are re-applied at
        # activation time by the permission summary, so nothing to do here
        # beyond preserving the record.
        return

    # ------------------------------------------------------------------
    # Quarantine, safe mode, uninstall
    # ------------------------------------------------------------------

    def quarantine(self, plugin_id: str, reason: str) -> PluginRecord:
        self._quarantine.quarantine(
            plugin_id=plugin_id,
            reason=reason,
            health=self._health,
            contributions=self._contributions,
            host=self._host,
        )
        return self.get_record(plugin_id)

    def restore(self, plugin_id: str) -> PluginRecord:
        self._quarantine.restore(plugin_id=plugin_id, lifecycle=self)
        self._health.record_activity(plugin_id=plugin_id, kind="restored", message="Restored from quarantine.", level="success")
        return self.get_record(plugin_id)

    def uninstall(self, plugin_id: str, *, delete_data: bool = False) -> None:
        record = self.require_record(plugin_id)
        dependents = self._dependents_of(plugin_id)
        if dependents:
            raise PluginLifecycleError(
                "cannot uninstall: dependent plugins require it: %s" % ", ".join(dependents)
            )
        with self._lock:
            self.deactivate(plugin_id) if record.lifecycle_state in {"active", "activating", "idle"} else None
            self._host.shutdown(plugin_id)
            self._contributions.unregister_all(plugin_id=plugin_id)
            with self._connection_factory() as connection:
                connection.execute(
                    "UPDATE plugin_records SET lifecycle_state = 'uninstalling', updated_at = ? WHERE plugin_id = ?",
                    (_now(), plugin_id),
                )
                connection.execute("DELETE FROM plugin_permission_grants WHERE plugin_id = ?", (plugin_id,))
                connection.execute("DELETE FROM plugin_contributions WHERE plugin_id = ?", (plugin_id,))
                connection.execute("DELETE FROM plugin_settings WHERE plugin_id = ?", (plugin_id,))
                connection.execute("DELETE FROM plugin_health WHERE plugin_id = ?", (plugin_id,))
                if delete_data:
                    connection.execute("DELETE FROM plugin_storage WHERE plugin_id = ?", (plugin_id,))
                    connection.execute("DELETE FROM plugin_secret_refs WHERE plugin_id = ?", (plugin_id,))
                    connection.execute("DELETE FROM plugin_logs WHERE plugin_id = ?", (plugin_id,))
                    connection.execute("DELETE FROM plugin_events WHERE plugin_id = ?", (plugin_id,))
                connection.execute("DELETE FROM plugin_records WHERE plugin_id = ?", (plugin_id,))
            self._health.record_activity(plugin_id=plugin_id, kind="uninstalled", message="Plugin uninstalled.", level="success")

    def _dependents_of(self, plugin_id: str) -> Tuple[str, ...]:
        dependents: list = []
        for record in self.list_records():
            for dependency in record.manifest.dependencies:
                if dependency.plugin_id == plugin_id and not dependency.optional:
                    dependents.append(record.plugin_id)
        return tuple(sorted(set(dependents)))

    # ------------------------------------------------------------------
    # Safe mode
    # ------------------------------------------------------------------

    def enter_safe_mode(self) -> None:
        self._safe_mode.enter()
        for record in self.list_records():
            if not self._is_first_party(record.plugin_id):
                try:
                    self.deactivate(record.plugin_id)
                except PluginLifecycleError:
                    self._host.shutdown(record.plugin_id)

    def exit_safe_mode(self) -> None:
        self._safe_mode.exit()

    def safe_mode_active(self) -> bool:
        return self._safe_mode.active