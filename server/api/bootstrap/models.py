from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Literal, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HttpRouteMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
RouteProtocol = Literal["http", "websocket"]
RouteAccess = Literal[
    "read_only", "configuration", "stream", "local_analysis", "enrollment", "read_write", "approval"
]
CapabilityAccess = Literal[
    "read_only", "configuration", "stream", "local_analysis", "enrollment", "read_write", "approval", "unavailable"
]
CapabilityStatus = Literal["available", "unavailable"]

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,80}$")
SAFE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./{}~-]*$")
GENERATED_AT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)$"
)
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class StrictBootstrapModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ServerIdentity(StrictBootstrapModel):
    server_id: UUID
    product_id: Literal["joeos"] = "joeos"
    display_name: Literal["JoeOS Local Command Center"] = "JoeOS Local Command Center"
    server_version: str = Field(min_length=1, max_length=40, pattern=r"^\d+\.\d+\.\d+$")
    api_version: Literal["v1"] = "v1"
    deployment_mode: Literal["local_first"] = "local_first"

    @field_validator("server_id")
    @classmethod
    def validate_server_id(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("server_id must be a UUIDv4.")
        return value


class SecurityPosture(StrictBootstrapModel):
    ownership_model: Literal["single_owner"] = "single_owner"
    network_boundary: Literal["operator_managed_private_tailnet"] = "operator_managed_private_tailnet"
    application_authentication: Literal["unavailable"] = "unavailable"
    device_enrollment: Literal["operator_pairing_v1"] = "operator_pairing_v1"
    role_based_access: Literal["unavailable"] = "unavailable"
    privileged_actions: Literal["unavailable"] = "unavailable"
    public_internet_ready: Literal[False] = False
    secrets_returned: Literal[False] = False
    warning: str = Field(min_length=1, max_length=240)


class DeviceEnrollmentProfile(StrictBootstrapModel):
    protocol: Literal["joeos-device-enrollment-v1"] = "joeos-device-enrollment-v1"
    offer_authority: Literal["local_console_only"] = "local_console_only"
    pairing_secret_bytes: Literal[32] = 32
    offer_ttl_seconds: Literal[300] = 300
    challenge_ttl_seconds: Literal[120] = 120
    key_algorithm: Literal["ES256"] = "ES256"
    public_key_format: Literal["spki_der_base64url"] = "spki_der_base64url"
    signature_format: Literal["x962_der_base64url"] = "x962_der_base64url"
    proof_algorithm: Literal["HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256"] = (
        "HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256"
    )
    required_key_purposes: Tuple[Literal["device_authentication"], Literal["approval"]] = (
        "device_authentication",
        "approval",
    )
    activation_state: Literal["active_unassigned"] = "active_unassigned"
    grants_authority: Literal[False] = False


class RouteDescriptor(StrictBootstrapModel):
    id: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=512)
    protocol: RouteProtocol
    methods: Tuple[str, ...] = Field(min_length=1, max_length=4)
    access: RouteAccess
    stability: Literal["stable"] = "stable"
    description: str = Field(min_length=1, max_length=240)

    @field_validator("id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError("route id must be a lowercase dot-separated identifier.")
        return value

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "://" in value
            or re.search(r"(^|/)\.\.?($|/)", value)
            or SAFE_PATH_PATTERN.fullmatch(value) is None
        ):
            raise ValueError("route path must be a safe relative path.")
        return value

    @model_validator(mode="after")
    def validate_protocol_methods(self) -> "RouteDescriptor":
        if self.protocol == "websocket":
            if self.methods != ("WEBSOCKET",):
                raise ValueError("WebSocket routes must declare exactly the WEBSOCKET method.")
        elif "WEBSOCKET" in self.methods or any(
            method not in HTTP_METHODS for method in self.methods
        ):
            raise ValueError("HTTP routes may only declare HTTP methods.")
        return self


class CapabilityDescriptor(StrictBootstrapModel):
    id: str = Field(min_length=1, max_length=80)
    status: CapabilityStatus
    access: CapabilityAccess
    route_ids: Tuple[str, ...] = Field(max_length=12)
    description: str = Field(min_length=1, max_length=240)

    @field_validator("id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError("capability id must be a lowercase dot-separated identifier.")
        return value


class BootstrapDocument(StrictBootstrapModel):
    schema_version: Literal[2] = 2
    generated_at: str
    server: ServerIdentity
    security: SecurityPosture
    device_enrollment: DeviceEnrollmentProfile
    capabilities: List[CapabilityDescriptor] = Field(max_length=128)
    routes: List[RouteDescriptor] = Field(max_length=128)

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: str) -> str:
        if GENERATED_AT_PATTERN.fullmatch(value) is None:
            raise ValueError("generated_at must be a timezone-aware UTC timestamp.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("generated_at must be a timezone-aware UTC timestamp.") from None
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("generated_at must be timezone-aware UTC.")
        return value

    @model_validator(mode="after")
    def validate_document(self) -> "BootstrapDocument":
        route_ids = [route.id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("Route identifiers must be unique.")
        capability_ids = [capability.id for capability in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("Capability identifiers must be unique.")
        route_by_id = {route.id: route for route in self.routes}
        for capability in self.capabilities:
            for route_id in capability.route_ids:
                if route_id not in route_by_id:
                    raise ValueError("Capability references an unknown route.")
            if capability.status == "unavailable":
                if capability.access != "unavailable" or capability.route_ids:
                    raise ValueError("Unavailable capabilities grant no access or routes.")
            else:
                if capability.access == "unavailable" or not capability.route_ids:
                    raise ValueError("Available capabilities must declare access and routes.")
                for route_id in capability.route_ids:
                    if route_by_id[route_id].access != capability.access:
                        raise ValueError("Capability access must match a referenced route.")

        discovery = route_by_id.get("bootstrap.discovery")
        if (
            discovery is None
            or discovery.path != "/api/v1/bootstrap"
            or discovery.protocol != "http"
            or discovery.methods != ("GET",)
            or discovery.access != "read_only"
        ):
            raise ValueError("Bootstrap discovery route is missing or incompatible.")
        if not any(
            capability.id == "discovery.bootstrap"
            and capability.status == "available"
            and capability.access == "read_only"
            and capability.route_ids == ("bootstrap.discovery",)
            for capability in self.capabilities
        ):
            raise ValueError("Bootstrap discovery capability is missing or incompatible.")

        enrollment_routes = [route for route in self.routes if route.access == "enrollment"]
        expected_paths = {
            "device-enrollment.challenge": "/api/v1/device-enrollment/challenges",
            "device-enrollment.complete": "/api/v1/device-enrollment/challenges/{challenge_id}/complete",
        }
        if len(enrollment_routes) != len(expected_paths):
            raise ValueError("Bootstrap must advertise exactly the two enrollment routes.")
        for route in enrollment_routes:
            if (
                route.id not in expected_paths
                or route.path != expected_paths[route.id]
                or route.protocol != "http"
                or route.methods != ("POST",)
            ):
                raise ValueError("Enrollment routes are incompatible.")
        enrollment_capabilities = [
            capability for capability in self.capabilities if capability.access == "enrollment"
        ]
        if len(enrollment_capabilities) != 1:
            raise ValueError("Bootstrap must advertise exactly one enrollment capability.")
        if enrollment_capabilities[0].route_ids != (
            "device-enrollment.challenge",
            "device-enrollment.complete",
        ):
            raise ValueError(
                "The enrollment capability must reference both enrollment routes in order."
            )
        return self
