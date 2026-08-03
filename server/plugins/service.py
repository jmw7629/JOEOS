"""PluginService facade: one authoritative entry point into the JoeOS Plugin
and Extension Platform.

Composes the Plugin Registry, Publisher Registry, Integrity and Signature
services, Permission Manager, Capability Broker, Contribution Registry,
Lifecycle Manager, Extension Host, storage, settings, secret broker, event
gateway, resource governor, health, update/rollback, quarantine, safe mode,
and development host. All services share one versioned SQLite database.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .compatibility import eval_manifest_compatibility, version_in_range
from .contributions import ContributionRegistry
from .dependency import PluginDependencyResolver
from .events import EventGateway
from .extension_data import ExtensionSettingsService, ExtensionStorageService
from .health import PluginHealthService
from .host import ExtensionHostManager, RestartPolicy
from .integrity import verify_inventory
from .lifecycle import (
    DevelopmentHost,
    ExtensionLifecycleManager,
    PluginLifecycleError,
    SafeModeState,
    UpdateService,
    QuarantineService,
    parse_manifest,
)
from .models import (
    ActivityEvent,
    ContributionRecord,
    HealthRecord,
    PermissionSummary,
    PluginManifest,
    PluginOverview,
    PluginRecord,
    PublisherRecord,
)
from .permissions import CapabilityBroker, PermissionManager
from .publishers import PublisherService
from .resources import ResourceGovernor
from .secrets import ExtensionSecretBroker
from .storage import PluginRegistryStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PluginService:
    def __init__(
        self,
        data_dir: str,
        *,
        master_key: bytes,
        joeos_version: str = "2.0.0",
        first_party_publishers: Optional[Sequence[str]] = None,
        development_mode: bool = False,
        python: Optional[str] = None,
        rpc_timeout: float = 30.0,
    ) -> None:
        self.storage = PluginRegistryStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._joeos_version = joeos_version
        self._first_party = set(first_party_publishers or ())

        self.permissions = PermissionManager(self._connection_factory)
        self.publishers = PublisherService(self._connection_factory)
        self.contributions = ContributionRegistry(self._connection_factory)
        self.health = PluginHealthService(self._connection_factory)
        self.events = EventGateway(self._connection_factory, self.permissions, self._lifecycle_probe)
        self.resources = ResourceGovernor()
        self.settings = ExtensionSettingsService(self._connection_factory)
        self.storage_service = ExtensionStorageService(self._connection_factory)
        self.secrets = ExtensionSecretBroker(
            self._connection_factory, master_key, self.permissions, self._lifecycle_probe
        )
        self.updates = UpdateService(self._connection_factory)
        self.quarantine = QuarantineService(self._connection_factory)
        self.hosts = ExtensionHostManager(python=python, rpc_timeout=rpc_timeout, crash_callback=self._on_crash)
        self.dependency_resolver = PluginDependencyResolver(
            self._records_map, self._state_of
        )
        self.capability = CapabilityBroker(self.permissions, self._lifecycle_probe)
        self.safe_mode = SafeModeState()
        self.dev = DevelopmentHost(self._data_dir / "dev")
        self.lifecycle = ExtensionLifecycleManager(
            connection_factory=self._connection_factory,
            install_root=str(self._data_dir / "installed"),
            package_root=str(self._data_dir / "packages"),
            permissions=self.permissions,
            publishers=self.publishers,
            contributions=self.contributions,
            health=self.health,
            events=self.events,
            resources=self.resources,
            settings=self.settings,
            storage=self.storage_service,
            secrets=self.secrets,
            host=self.hosts,
            dependency_resolver=self.dependency_resolver,
            updates=self.updates,
            quarantine=self.quarantine,
            first_party_publishers=self._first_party,
            joeos_version=self._joeos_version,
            dev=self.dev,
            safe_mode=self.safe_mode,
        )

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    def _lifecycle_probe(self, plugin_id: str) -> str:
        record = self.lifecycle.get_record(plugin_id)
        return record.lifecycle_state if record else "unknown"

    def _state_of(self, plugin_id: str) -> str:
        record = self.lifecycle.get_record(plugin_id)
        if record is None:
            return "unknown"
        if record.lifecycle_state == "quarantined":
            return "quarantined"
        if record.lifecycle_state == "crashed":
            return "disabled_after_crash"
        return record.lifecycle_state

    def _records_map(self) -> Dict[str, PluginRecord]:
        return {record.plugin_id: record for record in self.lifecycle.list_records()}

    def _on_crash(self, plugin_id: str, reason: str) -> None:
        self.health.record_crash(plugin_id=plugin_id, error=reason)
        record = self.lifecycle.get_record(plugin_id)
        if record is not None and record.crash_count + 1 > 3:
            try:
                self.lifecycle.quarantine(plugin_id, "repeated crashes.")
            except PluginLifecycleError:
                pass

    # ------------------------------------------------------------------
    # Overview & discovery
    # ------------------------------------------------------------------

    def overview(self) -> PluginOverview:
        records = self.lifecycle.list_records()
        return PluginOverview(
            installed=len(records),
            active=sum(1 for record in records if record.lifecycle_state == "active"),
            disabled=sum(1 for record in records if record.enabled_state == "disabled"),
            quarantined=sum(1 for record in records if record.lifecycle_state == "quarantined"),
            incompatible=sum(
                1
                for record in records
                if eval_manifest_compatibility(
                    manifest=record.manifest,
                    joeos_version=self._joeos_version,
                    platform_compliant=True,
                    available_plugins={other.plugin_id for other in records},
                ).decision
                in {"incompatible", "unsupported_platform", "unsupported_api", "missing_dependency"}
            ),
            pending_permissions=sum(
                1
                for record in records
                if self.permissions.summary(
                    plugin_id=record.plugin_id,
                    required=[d.permission for d in record.manifest.required_permissions],
                    optional=[d.permission for d in record.manifest.optional_permissions],
                ).pending
            ),
            update_available=0,
            unverified_publishers=sum(
                1
                for record in records
                if not self._publisher_verified(record.publisher_id)
            ),
            safe_mode=self.safe_mode.active,
            generated_at=_now(),
        )

    def _publisher_verified(self, publisher_id: str) -> bool:
        record = self.publishers.get(publisher_id)
        return bool(record and record.verification_state in {"first_party", "verified", "user_trusted"})

    # ------------------------------------------------------------------
    # Facade: lifecycle
    # ------------------------------------------------------------------

    def install_package(self, package_path: str, *, source: str = "local_package", approval: Optional[dict] = None) -> PluginRecord:
        return self.lifecycle.install_package(package_path, source=source, approval=approval)

    def install_directory(self, directory: str, *, source: str = "local_development") -> PluginRecord:
        return self.lifecycle.install_directory(directory, source=source)

    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        return self.lifecycle.get_record(plugin_id)

    def list(self) -> Tuple[PluginRecord, ...]:
        return self.lifecycle.list_records()

    def enable(self, plugin_id: str, *, scope: str = "global", workspace: str = "", project: str = "") -> PluginRecord:
        return self.lifecycle.enable(plugin_id, scope=scope, workspace=workspace, project=project)

    def disable(self, plugin_id: str) -> PluginRecord:
        return self.lifecycle.disable(plugin_id)

    def activate(self, plugin_id: str) -> PluginRecord:
        return self.lifecycle.activate(plugin_id)

    def deactivate(self, plugin_id: str) -> PluginRecord:
        return self.lifecycle.deactivate(plugin_id)

    def uninstall(self, plugin_id: str, *, delete_data: bool = False) -> None:
        self.lifecycle.uninstall(plugin_id, delete_data=delete_data)

    def update(self, plugin_id: str, new_package_path: str, *, approval: Optional[dict] = None) -> PluginRecord:
        return self.lifecycle.update(plugin_id, new_package_path, approval=approval)

    def rollback(self, plugin_id: str) -> Optional[PluginRecord]:
        return self.lifecycle.rollback(plugin_id)

    def quarantine_plugin(self, plugin_id: str, reason: str) -> PluginRecord:
        return self.lifecycle.quarantine(plugin_id, reason)

    def restore(self, plugin_id: str) -> PluginRecord:
        return self.lifecycle.restore(plugin_id)

    def enter_safe_mode(self) -> None:
        self.lifecycle.enter_safe_mode()

    def exit_safe_mode(self) -> None:
        self.lifecycle.exit_safe_mode()

    # ------------------------------------------------------------------
    # Facade: permissions
    # ------------------------------------------------------------------

    def permission_summary(self, plugin_id: str) -> PermissionSummary:
        record = self.lifecycle.require_record(plugin_id)
        return self.permissions.summary(
            plugin_id=plugin_id,
            required=[d.permission for d in record.manifest.required_permissions],
            optional=[d.permission for d in record.manifest.optional_permissions],
        )

    def grant_permission(self, plugin_id: str, permission: str, *, scope: str = "granted_global", scope_target: str = "") -> None:
        self.lifecycle.grant_permission(plugin_id=plugin_id, permission=permission, scope=scope, scope_target=scope_target)

    def revoke_permission(self, plugin_id: str, permission: str, *, scope_target: str = "") -> None:
        self.permissions.revoke(plugin_id=plugin_id, permission=permission, scope_target=scope_target)

    def permission_grants(self, plugin_id: str) -> Tuple:
        return self.permissions.active_grants(plugin_id=plugin_id)

    # ------------------------------------------------------------------
    # Facade: contributions / invocation
    # ------------------------------------------------------------------

    def contribution_list(self, plugin_id: str) -> Tuple[ContributionRecord, ...]:
        return self.contributions.list_for(plugin_id=plugin_id)

    def active_contributions(self) -> Tuple[ContributionRecord, ...]:
        return self.contributions.list_active()

    def invoke_contribution(self, plugin_id: str, contribution_id: str, params: Optional[dict] = None) -> dict:
        return self.lifecycle.invoke_contribution(plugin_id=plugin_id, contribution_id=contribution_id, params=params)

    # ------------------------------------------------------------------
    # Facade: storage / settings / secrets / events
    # ------------------------------------------------------------------

    def storage_stats(self, plugin_id: str) -> dict:
        return {
            "items": len(self.storage_service.list_for(plugin_id=plugin_id)),
            "size_bytes": self.storage_service.size_for(plugin_id=plugin_id),
            "quota_bytes": self.get(plugin_id).manifest.resource_limits.max_storage_bytes if self.get(plugin_id) else 0,
        }

    def settings_all(self, plugin_id: str) -> dict:
        return self.settings.all_for(plugin_id=plugin_id)

    def set_setting(self, plugin_id: str, key: str, value: object) -> object:
        record = self.lifecycle.require_record(plugin_id)
        return self.settings.set(
            plugin_id=plugin_id,
            declarations=record.manifest.settings,
            key=key,
            value=value,
        )

    def get_setting(self, plugin_id: str, key: str) -> object:
        record = self.lifecycle.require_record(plugin_id)
        return self.settings.get(
            plugin_id=plugin_id,
            declarations=record.manifest.settings,
            key=key,
        )

    def set_secret(self, plugin_id: str, name: str, value: str) -> dict:
        return self.secrets.set(plugin_id=plugin_id, name=name, value=value)

    def secret_references(self, plugin_id: str) -> tuple:
        return self.secrets.references_for(plugin_id=plugin_id)

    def revoke_secret(self, plugin_id: str, name: str) -> None:
        self.secrets.revoke(plugin_id=plugin_id, name=name)

    def event_recent(self, plugin_id: str, limit: int = 50) -> tuple:
        return self.events.recent(plugin_id=plugin_id, limit=limit)

    # ------------------------------------------------------------------
    # Facade: health / diagnostics / activity
    # ------------------------------------------------------------------

    def health_record(self, plugin_id: str) -> HealthRecord:
        return self.health.get(plugin_id=plugin_id)

    def health_records(self) -> Tuple[HealthRecord, ...]:
        return self.health.all()

    def logs(self, plugin_id: str, limit: int = 100) -> tuple:
        return self.health.logs(plugin_id=plugin_id, limit=limit)

    def export_logs(self, plugin_id: str) -> str:
        return self.health.export_logs(plugin_id=plugin_id)

    def activity(self, plugin_id: str, limit: int = 50) -> tuple:
        return self.health.activity(plugin_id=plugin_id, limit=limit)

    def resource_snapshot(self, plugin_id: str) -> dict:
        record = self.lifecycle.require_record(plugin_id)
        return self.resources.snapshot(plugin_id=plugin_id, limits=record.manifest.resource_limits)

    # ------------------------------------------------------------------
    # Facade: publishers / compatibility / integrity
    # ------------------------------------------------------------------

    def publisher(self, publisher_id: str) -> Optional[PublisherRecord]:
        return self.publishers.get(publisher_id)

    def publisher_list(self) -> Tuple[PublisherRecord, ...]:
        return self.publishers.list()

    def set_publisher_trust(self, publisher_id: str, trusted: bool) -> PublisherRecord:
        return self.publishers.set_trust(publisher_id, trusted)

    def compatibility(self, plugin_id: str) -> dict:
        record = self.lifecycle.require_record(plugin_id)
        result = eval_manifest_compatibility(
            manifest=record.manifest,
            joeos_version=self._joeos_version,
            platform_compliant=True,
            available_plugins={other.plugin_id for other in self.list()},
        )
        return result.model_dump()

    def verify_integrity(self, plugin_id: str) -> dict:
        record = self.lifecycle.require_record(plugin_id)
        try:
            ok = verify_inventory(str(record.install_path), record.package_hash)
        except Exception:
            ok = False
        return {"plugin_id": plugin_id, "valid": ok, "expected_hash": record.package_hash[:16] + "…"}

    def update_history(self, plugin_id: str) -> tuple:
        return self.updates.history(plugin_id=plugin_id)

    def development_link(self, plugin_id: str, source_dir: str) -> str:
        return self.dev.link(plugin_id=plugin_id, source_dir=source_dir)

    def development_unlink(self, plugin_id: str) -> None:
        self.dev.unlink(plugin_id=plugin_id)

    def storage_stats_global(self) -> dict:
        return {
            "path": self.storage.path(),
            "size_bytes": self.storage.size_bytes(),
            "version": 1,
        }

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(self._data_dir))

    def shutdown(self) -> None:
        self.hosts.shutdown_all()


class _BorrowedConnection:
    """Context manager wrapper that never closes the shared connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()