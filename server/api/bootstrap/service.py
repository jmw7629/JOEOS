from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from .models import (
    BootstrapDocument,
    CapabilityDescriptor,
    DeviceEnrollmentProfile,
    RouteDescriptor,
    SecurityPosture,
    ServerIdentity,
)
from .repository import SQLiteServerIdentityRepository

DEFAULT_SERVER_VERSION = "2.0.0"


class BootstrapService:
    """Composes the strict, non-secret bootstrap discovery document."""

    def __init__(
        self,
        repository: SQLiteServerIdentityRepository,
        *,
        server_version: str = DEFAULT_SERVER_VERSION,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._repository = repository
        self._server_version = server_version
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

    def prepare(self) -> None:
        self._repository.prepare()

    def discover(self) -> BootstrapDocument:
        server_id = self._repository.get_or_create_server_id()
        now = self._now().astimezone(timezone.utc)
        return BootstrapDocument(
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            server=ServerIdentity(
                server_id=server_id,
                server_version=self._server_version,
            ),
            security=self._security_posture(),
            device_enrollment=DeviceEnrollmentProfile(),
            capabilities=self._capabilities(),
            routes=self._routes(),
        )

    def _security_posture(self) -> SecurityPosture:
        return SecurityPosture(
            warning=(
                "JoeOS is operator-managed and not public-internet ready: no browser-facing "
                "authentication or role-based access is enabled, and no secrets are returned. "
                "Expose only over a private trusted network."
            )
        )

    def _routes(self) -> list[RouteDescriptor]:
        return [
            RouteDescriptor(
                id="bootstrap.discovery",
                path="/api/v1/bootstrap",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Non-secret identity and capability discovery.",
            ),
            RouteDescriptor(
                id="workspace.retrieve",
                path="/api/v1/workspace",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Read the persistent workspace configuration.",
            ),
            RouteDescriptor(
                id="workspace.configure",
                path="/api/v1/workspace",
                protocol="http",
                methods=("PUT", "PATCH"),
                access="configuration",
                description="Persist workspace and layout configuration with revision checks.",
            ),
            RouteDescriptor(
                id="workspace.guide",
                path="/api/v1/workspace/guide",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Workspace configuration guide.",
            ),
            RouteDescriptor(
                id="events.stream",
                path="/ws/events",
                protocol="websocket",
                methods=("WEBSOCKET",),
                access="stream",
                description="Resumable audit-event and telemetry stream.",
            ),
            RouteDescriptor(
                id="telemetry.sample",
                path="/api/v1/telemetry",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Bounded host telemetry sample.",
            ),
            RouteDescriptor(
                id="command_center.overview",
                path="/api/v1/command-center/overview",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Executive overview aggregated from live subsystem health.",
            ),
            RouteDescriptor(
                id="command_center.services",
                path="/api/v1/command-center/services",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Typed service-health contract for registered JoeOS services.",
            ),
            RouteDescriptor(
                id="command_center.activity",
                path="/api/v1/command-center/activity",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Filterable, paginated activity timeline.",
            ),
            RouteDescriptor(
                id="health.liveness",
                path="/healthz",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Readiness probe without authentication.",
            ),
            RouteDescriptor(
                id="assistant.local_analysis",
                path="/api/chat",
                protocol="http",
                methods=("POST",),
                access="local_analysis",
                description="Read-only local analysis through the private inference runtime.",
            ),
            RouteDescriptor(
                id="device-enrollment.challenge",
                path="/api/v1/device-enrollment/challenges",
                protocol="http",
                methods=("POST",),
                access="enrollment",
                description="Request a device enrollment challenge.",
            ),
            RouteDescriptor(
                id="device-enrollment.complete",
                path="/api/v1/device-enrollment/challenges/{challenge_id}/complete",
                protocol="http",
                methods=("POST",),
                access="enrollment",
                description="Complete device enrollment with a signed proof.",
            ),
        ]

    def _capabilities(self) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                id="discovery.bootstrap",
                status="available",
                access="read_only",
                route_ids=("bootstrap.discovery",),
                description="Non-secret bootstrap discovery.",
            ),
            CapabilityDescriptor(
                id="workspace.read",
                status="available",
                access="read_only",
                route_ids=("workspace.retrieve", "workspace.guide"),
                description="Read workspace configuration.",
            ),
            CapabilityDescriptor(
                id="workspace.configuration",
                status="available",
                access="configuration",
                route_ids=("workspace.configure",),
                description="Persist workspace configuration.",
            ),
            CapabilityDescriptor(
                id="events.streaming",
                status="available",
                access="stream",
                route_ids=("events.stream",),
                description="Resumable audit-event and telemetry stream.",
            ),
            CapabilityDescriptor(
                id="telemetry.monitoring",
                status="available",
                access="read_only",
                route_ids=("telemetry.sample", "health.liveness"),
                description="Bounded local telemetry and liveness.",
            ),
            CapabilityDescriptor(
                id="command_center.overview",
                status="available",
                access="read_only",
                route_ids=(
                    "command_center.overview",
                    "command_center.services",
                    "command_center.activity",
                ),
                description="Executive Command Center aggregation.",
            ),
            CapabilityDescriptor(
                id="assistant.local_analysis",
                status="available",
                access="local_analysis",
                route_ids=("assistant.local_analysis",),
                description="Read-only local analysis.",
            ),
            CapabilityDescriptor(
                id="identity.device_enrollment",
                status="available",
                access="enrollment",
                route_ids=(
                    "device-enrollment.challenge",
                    "device-enrollment.complete",
                ),
                description="Operator pairing without authority.",
            ),
            CapabilityDescriptor(
                id="identity.authentication",
                status="unavailable",
                access="unavailable",
                route_ids=(),
                description="No browser-facing application authentication.",
            ),
            CapabilityDescriptor(
                id="authorization.roles",
                status="unavailable",
                access="unavailable",
                route_ids=(),
                description="No role-based access control.",
            ),
            CapabilityDescriptor(
                id="approvals.privileged_actions",
                status="unavailable",
                access="unavailable",
                route_ids=(),
                description="No privileged-approval workflow.",
            ),
            CapabilityDescriptor(
                id="agents.execution",
                status="unavailable",
                access="unavailable",
                route_ids=(),
                description="No agent execution authority.",
            ),
            CapabilityDescriptor(
                id="secrets.management",
                status="unavailable",
                access="unavailable",
                route_ids=(),
                description="No secret retrieval.",
            ),
        ]
