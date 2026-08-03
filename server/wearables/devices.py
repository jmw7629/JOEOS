"""Device Registry, Adapter Registry, and Discovery for the JoeOS Wearable
Platform.

Devices have stable canonical identity; device type never implies capability.
Adapters are provider-neutral and plugin-based. Discovery requires explicit
user action or a bounded discovery window and never runs continuously.
Discovery metadata is treated as untrusted.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    AdapterRecord,
    CapabilityID,
    CapabilityRecord,
    DeviceRecord,
    DeviceType,
    DeviceTypeInfo,
    Transport,
)

DEVICE_TYPES: Dict[DeviceType, DeviceTypeInfo] = {
    "display_glasses": DeviceTypeInfo(device_type="display_glasses", display_name="Display Glasses", typical_transports=("ble", "wifi")),
    "audio_glasses": DeviceTypeInfo(device_type="audio_glasses", display_name="Audio Glasses", typical_transports=("ble",)),
    "camera_glasses": DeviceTypeInfo(device_type="camera_glasses", display_name="Camera Glasses", typical_transports=("wifi", "usb")),
    "display_audio_glasses": DeviceTypeInfo(device_type="display_audio_glasses", display_name="Display + Audio Glasses", typical_transports=("ble", "wifi")),
    "camera_display_glasses": DeviceTypeInfo(device_type="camera_display_glasses", display_name="Camera + Display Glasses", typical_transports=("wifi", "usb")),
    "monocular_hud": DeviceTypeInfo(device_type="monocular_hud", display_name="Monocular HUD", typical_transports=("usb", "wifi")),
    "binocular_hud": DeviceTypeInfo(device_type="binocular_hud", display_name="Binocular HUD", typical_transports=("usb", "wifi")),
    "mixed_reality_headset": DeviceTypeInfo(device_type="mixed_reality_headset", display_name="Mixed-Reality Headset", typical_transports=("wifi", "usb")),
    "industrial_headset": DeviceTypeInfo(device_type="industrial_headset", display_name="Industrial Headset", typical_transports=("wifi", "usb")),
    "wearable_display": DeviceTypeInfo(device_type="wearable_display", display_name="Wearable Display", typical_transports=("ble",)),
    "accessibility_display": DeviceTypeInfo(device_type="accessibility_display", display_name="Accessibility Display", typical_transports=("ble",)),
    "wearable_microphone": DeviceTypeInfo(device_type="wearable_microphone", display_name="Wearable Microphone", typical_transports=("ble",)),
    "wearable_speaker": DeviceTypeInfo(device_type="wearable_speaker", display_name="Wearable Speaker", typical_transports=("ble",)),
    "mobile_relay_device": DeviceTypeInfo(device_type="mobile_relay_device", display_name="Mobile Relay Device", typical_transports=("local_network", "companion_relay")),
    "simulated_development_device": DeviceTypeInfo(device_type="simulated_development_device", display_name="Simulated Development Device", typical_transports=("simulator",)),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WearableError(RuntimeError):
    pass


class DeviceRegistry:
    """Authoritative registry of wearable devices."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def device_types(self) -> Tuple[DeviceTypeInfo, ...]:
        return tuple(DEVICE_TYPES.values())

    def register(
        self,
        *,
        device_id: str,
        device_type: DeviceType,
        display_name: str,
        adapter_id: str = "",
        plugin_id: str = "",
        transport: Transport = "local_network",
        manufacturer: str = "",
        model: str = "",
        firmware_version: str = "",
        connection_address_reference: str = "",
    ) -> DeviceRecord:
        if device_type not in DEVICE_TYPES:
            raise WearableError("unknown device type %r." % device_type)
        existing = self.get(device_id)
        if existing is not None and existing.deletion_state == "active":
            raise WearableError("device %s already exists." % device_id)
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_records (
                    device_id, device_type, display_name, manufacturer, model, firmware_version,
                    adapter_id, plugin_id, transport, connection_address_reference, paired_state,
                    trusted_state, authentication_state, connection_state, battery_state,
                    charging_state, thermal_state, network_state, bandwidth_class, health,
                    privacy_mode, last_firmware_check, created_at, revocation_state, deletion_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaired', 'untrusted', 'unauthenticated',
                          'discovered', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown',
                          'normal', ?, ?, 'active', 'active')
                """,
                (
                    device_id,
                    device_type,
                    display_name,
                    manufacturer,
                    model,
                    firmware_version,
                    adapter_id,
                    plugin_id,
                    transport,
                    connection_address_reference,
                    now,
                    now,
                ),
            )
        return self.get(device_id)

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM device_records WHERE device_id = ?", (device_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, include_removed: bool = False) -> Tuple[DeviceRecord, ...]:
        clause = "" if include_removed else " WHERE deletion_state = 'active'"
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM device_records" + clause + " ORDER BY display_name"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def update_state(self, device_id: str, **fields) -> DeviceRecord:
        allowed = {
            "paired_state", "trusted_state", "authentication_state", "connection_state",
            "battery_state", "charging_state", "thermal_state", "network_state",
            "latency_ms", "bandwidth_class", "health", "privacy_mode",
            "mic_active", "camera_active", "last_connected", "last_disconnected",
            "key_reference", "verified_capabilities", "disabled_capabilities",
            "revocation_state", "firmware_version",
        }
        setters = [field for field in fields if field in allowed]
        if not setters:
            return self.get(device_id)
        assignments = ", ".join("%s = ?" % field for field in setters)
        values: List[object] = []
        for field in setters:
            value = fields[field]
            if isinstance(value, (list, tuple)):
                value = "\n".join(value)
            elif isinstance(value, bool):
                value = 1 if value else 0
            values.append(value)
        values.append(device_id)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE device_records SET %s WHERE device_id = ?" % assignments, values
            )
        return self.get(device_id)

    def mark_removed(self, device_id: str, *, revoke: bool = False) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE device_records SET deletion_state = 'removed', revocation_state = ? WHERE device_id = ?",
                ("revoked" if revoke else "active", device_id),
            )

    def capabilities(self, device_id: str) -> Tuple[CapabilityRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM device_capabilities WHERE device_id = ? ORDER BY capability_id",
                (device_id,),
            ).fetchall()
        return tuple(self._cap_row(row) for row in rows)

    def set_capability(
        self,
        *,
        device_id: str,
        capability_id: str,
        support_state: str = "available",
        verification_state: str = "negotiated",
        permission_requirement: str = "",
    ) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_capabilities (
                    capability_id, device_id, support_state, verification_state,
                    permission_requirement, privacy_classification, resource_cost, health
                ) VALUES (?, ?, ?, ?, ?, 'private', 'low', 'healthy')
                ON CONFLICT(capability_id) DO UPDATE SET
                    support_state = excluded.support_state,
                    verification_state = excluded.verification_state,
                    permission_requirement = excluded.permission_requirement
                """,
                (
                    "%s:%s" % (device_id, capability_id),
                    device_id,
                    support_state,
                    verification_state,
                    permission_requirement,
                ),
            )

    def capability_state(self, device_id: str, capability_id: str) -> str:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT support_state FROM device_capabilities WHERE device_id = ? AND capability_id = ?",
                (device_id, "%s:%s" % (device_id, capability_id)),
            ).fetchone()
        return str(row["support_state"]) if row else "unknown"

    def capability_available(self, device_id: str, capability_id: str) -> bool:
        state = self.capability_state(device_id, capability_id)
        return state in {"available", "available_with_limits"}

    @staticmethod
    def _row(row: sqlite3.Row) -> DeviceRecord:
        return DeviceRecord(
            device_id=str(row["device_id"]),
            device_type=str(row["device_type"]),
            display_name=str(row["display_name"]),
            manufacturer=str(row["manufacturer"]),
            model=str(row["model"]),
            hardware_revision=str(row["hardware_revision"]),
            firmware_version=str(row["firmware_version"]),
            adapter_id=str(row["adapter_id"]),
            plugin_id=str(row["plugin_id"]),
            transport=str(row["transport"]),
            connection_address_reference=str(row["connection_address_reference"]),
            paired_state=str(row["paired_state"]),
            trusted_state=str(row["trusted_state"]),
            authentication_state=str(row["authentication_state"]),
            key_reference=str(row["key_reference"]),
            user_owned=bool(row["user_owned"]),
            verified_capabilities=tuple(p for p in str(row["verified_capabilities"]).split("\n") if p),
            disabled_capabilities=tuple(p for p in str(row["disabled_capabilities"]).split("\n") if p),
            connection_state=str(row["connection_state"]),
            battery_state=str(row["battery_state"]),
            charging_state=str(row["charging_state"]),
            thermal_state=str(row["thermal_state"]),
            network_state=str(row["network_state"]),
            latency_ms=row["latency_ms"],
            bandwidth_class=str(row["bandwidth_class"]),
            health=str(row["health"]),
            privacy_mode=str(row["privacy_mode"]),
            mic_active=bool(row["mic_active"]),
            camera_active=bool(row["camera_active"]),
            last_connected=str(row["last_connected"]),
            last_disconnected=str(row["last_disconnected"]),
            last_firmware_check=str(row["last_firmware_check"]),
            created_at=str(row["created_at"]),
            revocation_state=str(row["revocation_state"]),
            deletion_state=str(row["deletion_state"]),
        )

    @staticmethod
    def _cap_row(row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            capability_id=str(row["capability_id"]).split(":", 1)[1] if ":" in str(row["capability_id"]) else str(row["capability_id"]),
            device_id=str(row["device_id"]),
            adapter_id=str(row["adapter_id"]),
            support_state=str(row["support_state"]),
            verification_state=str(row["verification_state"]),
            permission_requirement=str(row["permission_requirement"]),
            privacy_classification=str(row["privacy_classification"]),
            resource_cost=str(row["resource_cost"]),
            limitations=str(row["limitations"]),
            health=str(row["health"]),
        )


class AdapterRegistry:
    """Provider-neutral wearable adapters contributed through the Plugin Platform."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(
        self,
        *,
        adapter_id: str,
        display_name: str,
        plugin_id: str = "",
        supported_manufacturers: Sequence[str] = (),
        supported_transports: Sequence[str] = (),
        supports_discovery: bool = False,
        supports_pairing: bool = True,
        supported_capabilities: Sequence[CapabilityID] = (),
        version: str = "",
        is_simulator: bool = False,
    ) -> AdapterRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO adapter_records (
                    adapter_id, plugin_id, display_name, supported_manufacturers,
                    supported_transports, supports_discovery, supports_pairing,
                    supported_capabilities, state, version, platform, health,
                    is_simulator, known_limitations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enabled', ?, '', 'healthy', ?, '')
                ON CONFLICT(adapter_id) DO UPDATE SET
                    display_name = excluded.display_name, version = excluded.version,
                    supported_capabilities = excluded.supported_capabilities
                """,
                (
                    adapter_id,
                    plugin_id,
                    display_name,
                    "\n".join(supported_manufacturers),
                    "\n".join(supported_transports),
                    1 if supports_discovery else 0,
                    1 if supports_pairing else 0,
                    "\n".join(supported_capabilities),
                    version,
                    1 if is_simulator else 0,
                ),
            )
        return self.get(adapter_id)

    def get(self, adapter_id: str) -> Optional[AdapterRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM adapter_records WHERE adapter_id = ?", (adapter_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self) -> Tuple[AdapterRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM adapter_records ORDER BY display_name").fetchall()
        return tuple(self._row(row) for row in rows)

    def set_state(self, adapter_id: str, state: str, *, health: Optional[str] = None) -> None:
        with self._lock, self._connection_factory() as connection:
            if health:
                connection.execute(
                    "UPDATE adapter_records SET state = ?, health = ? WHERE adapter_id = ?",
                    (state, health, adapter_id),
                )
            else:
                connection.execute(
                    "UPDATE adapter_records SET state = ? WHERE adapter_id = ?", (state, adapter_id)
                )

    def supports_capability(self, adapter_id: str, capability_id: str) -> bool:
        adapter = self.get(adapter_id)
        if adapter is None or adapter.state not in {"enabled", "registered"}:
            return False
        return capability_id in adapter.supported_capabilities

    @staticmethod
    def _row(row: sqlite3.Row) -> AdapterRecord:
        return AdapterRecord(
            adapter_id=str(row["adapter_id"]),
            plugin_id=str(row["plugin_id"]),
            display_name=str(row["display_name"]),
            supported_manufacturers=tuple(p for p in str(row["supported_manufacturers"]).split("\n") if p),
            supported_transports=tuple(p for p in str(row["supported_transports"]).split("\n") if p),
            supports_discovery=bool(row["supports_discovery"]),
            supports_pairing=bool(row["supports_pairing"]),
            supported_capabilities=tuple(p for p in str(row["supported_capabilities"]).split("\n") if p),
            state=str(row["state"]),
            version=str(row["version"]),
            platform=str(row["platform"]),
            health=str(row["health"]),
            is_simulator=bool(row["is_simulator"]),
            known_limitations=str(row["known_limitations"]),
        )


class DiscoveryService:
    """Controlled, explicit discovery of wearable devices (never continuous)."""

    def __init__(self, device_registry: DeviceRegistry, adapter_registry: AdapterRegistry) -> None:
        self._devices = device_registry
        self._adapters = adapter_registry

    def discover(self, *, adapter_id: str, window_seconds: int = 30, discovered: Sequence[dict] = ()) -> Tuple[dict, ...]:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise WearableError("adapter not found.")
        if adapter.state not in {"enabled", "registered"}:
            raise WearableError("adapter is not enabled.")
        if not adapter.supports_discovery:
            raise WearableError("adapter does not support discovery.")
        results = []
        for item in discovered:
            device_id = str(item.get("device_id") or ("dev_" + uuid.uuid4().hex[:14]))
            results.append(
                {
                    "device_id": device_id,
                    "display_name": str(item.get("display_name") or "Unknown device"),
                    "device_type": str(item.get("device_type") or "display_glasses"),
                    "manufacturer": str(item.get("manufacturer") or ""),
                    "model": str(item.get("model") or ""),
                    "transport": str(item.get("transport") or "local_network"),
                    "adapter_id": adapter_id,
                    "pairing_state": "unpaired",
                    "verification_confidence": "low",
                    "privacy_warning": "Discovery metadata is untrusted; pairing verifies identity.",
                    "supported_pairing_method": "one_time_code",
                }
            )
        return tuple(results)