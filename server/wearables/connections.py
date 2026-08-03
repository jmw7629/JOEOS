"""Device permissions, connection manager, and capability negotiation for the
JoeOS Wearable Platform.

Permissions are granular and capability-scoped; camera, microphone, location,
and private-content permissions are never granted by default. The Connection
Manager is authoritative with bounded backoff. Capabilities are negotiated and
never inferred from device type.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .devices import AdapterRegistry, DeviceRegistry
from .models import AdapterRecord, DeviceSession
from .permissions import DevicePermissionManager, PermissionError
from .security import SecureSessionService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectionManager:
    """Authoritative connection lifecycle with bounded reconnect backoff."""

    def __init__(
        self,
        *,
        sessions: SecureSessionService,
        devices: DeviceRegistry,
        adapters: AdapterRegistry,
        max_reconnect_attempts: int = 4,
        event_sink=None,
    ) -> None:
        self._sessions = sessions
        self._devices = devices
        self._adapters = adapters
        self._max_reconnect = max_reconnect_attempts
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._lock = threading.RLock()
        self._reconnect_counts: Dict[str, int] = {}

    def connect(
        self,
        *,
        device_id: str,
        adapter_id: str,
        authenticated_user: str = "user",
        transport: str = "",
        capabilities: Sequence[str] = (),
        permissions: Sequence[str] = (),
    ) -> DeviceSession:
        device = self._devices.get(device_id)
        if device is None:
            raise PermissionError("device not found.")
        if device.revocation_state == "revoked":
            raise PermissionError("device trust was revoked.")
        if device.paired_state != "paired":
            raise PermissionError("device is not paired.")
        if device.trusted_state in {"untrusted", "revoked"}:
            raise PermissionError("device is not trusted.")
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise PermissionError("adapter not found.")
        if adapter.state not in {"enabled", "registered"}:
            raise PermissionError("adapter is not available.")
        self._devices.update_state(device_id, connection_state="connecting")
        session = self._sessions.open(
            device_id=device_id,
            adapter_id=adapter_id,
            authenticated_user=authenticated_user,
            transport=transport or device.transport,
            capabilities=capabilities,
            permissions=permissions,
        )
        self._reconnect_counts[device_id] = 0
        self._event_sink("info", "wearables", "Device %s connected." % device_id)
        return session

    def disconnect(self, *, device_id: str, reason: str = "user action") -> None:
        self._sessions.terminate_for_device(device_id, reason=reason)
        self._devices.update_state(device_id, connection_state="disconnected", last_disconnected=_now())
        self._event_sink("info", "wearables", "Device %s disconnected." % device_id)

    def reconnect(self, *, device_id: str, adapter_id: str, capabilities: Sequence[str] = (), permissions: Sequence[str] = ()) -> DeviceSession:
        with self._lock:
            attempts = self._reconnect_counts.get(device_id, 0)
            if attempts >= self._max_reconnect:
                self._devices.update_state(device_id, connection_state="failed", health="degraded")
                raise PermissionError("reconnect limit exceeded for device %s." % device_id)
            delay = min(30, 2 ** attempts)
            time.sleep(delay)  # bounded backoff; replaced by async scheduler in production
            self._reconnect_counts[device_id] = attempts + 1
            self._devices.update_state(device_id, connection_state="reconnecting")
        return self.connect(
            device_id=device_id,
            adapter_id=adapter_id,
            capabilities=capabilities,
            permissions=permissions,
        )

    def reset_reconnect(self, device_id: str) -> None:
        self._reconnect_counts.pop(device_id, None)


class CapabilityNegotiation:
    """Negotiates and verifies actual device capabilities per session."""

    def __init__(self, devices: DeviceRegistry, adapters: AdapterRegistry) -> None:
        self._devices = devices
        self._adapters = adapters

    def negotiate(self, *, device_id: str, adapter_id: str, device_reported: Sequence[str]) -> Dict[str, str]:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise PermissionError("adapter not found.")
        device = self._devices.get(device_id)
        if device is None:
            raise PermissionError("device not found.")
        adapter_caps = set(adapter.supported_capabilities)
        reported = set(device_reported)
        disabled = set(device.disabled_capabilities)
        result: Dict[str, str] = {}
        for capability in sorted(adapter_caps | reported):
            if capability in disabled:
                result[capability] = "disabled"
                self._devices.set_capability(device_id=device_id, capability_id=capability, support_state="disabled")
                continue
            if capability not in adapter_caps:
                # The adapter is the authority for what the device can do.
                result[capability] = "unsupported"
                self._devices.set_capability(device_id=device_id, capability_id=capability, support_state="unsupported")
                continue
            if capability in adapter_caps and capability in reported:
                result[capability] = "available"
                self._devices.set_capability(device_id=device_id, capability_id=capability, support_state="available", verification_state="verified")
            else:
                result[capability] = "available_with_limits"
                self._devices.set_capability(device_id=device_id, capability_id=capability, support_state="available_with_limits")
        verified = [cap for cap, state in result.items() if state in {"available", "available_with_limits"}]
        self._devices.update_state(device_id, verified_capabilities=verified)
        return result