from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit
from uuid import uuid4


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
HOSTNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
SINGLE_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


@dataclass(frozen=True)
class BoundaryRejection:
    status_code: int
    code: str
    message: str


class HttpRequestBoundary:
    """Validates the private HTTP transport boundary without pretending it is auth."""

    def __init__(self, allowed_hosts: Iterable[str] = ()) -> None:
        normalized = set()
        for value in allowed_hosts:
            host = self._configured_host(value)
            if host:
                normalized.add(host)
        self._allowed_hosts = frozenset(normalized)

    @classmethod
    def from_environment(cls) -> "HttpRequestBoundary":
        return cls(os.getenv("JOEOS_ALLOWED_HOSTS", "").split(","))

    @staticmethod
    def request_id(candidate: Optional[str]) -> str:
        value = (candidate or "").strip()
        if REQUEST_ID_PATTERN.fullmatch(value):
            return value
        return uuid4().hex

    def authorize(
        self,
        *,
        method: str,
        path: str,
        host_header: Optional[str],
        origin_header: Optional[str],
        sec_fetch_site: Optional[str],
        content_type: Optional[str],
    ) -> Optional[BoundaryRejection]:
        authority = self._request_authority(host_header)
        if authority is None or not self._host_allowed(authority[0]):
            return BoundaryRejection(
                status_code=400,
                code="invalid_host",
                message="The request Host is not allowed by the JoeOS private-network boundary.",
            )
        host, explicit_port = authority

        normalized_method = method.upper()
        if not path.startswith("/api/") or normalized_method not in MUTATING_METHODS:
            return None

        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return BoundaryRejection(
                status_code=415,
                code="json_required",
                message="JoeOS API mutations require an application/json request body.",
            )

        if (sec_fetch_site or "").strip().lower() == "cross-site":
            return BoundaryRejection(
                status_code=403,
                code="cross_site_mutation_blocked",
                message="Cross-site browser mutations are not allowed.",
            )

        if origin_header is not None:
            origin = self._origin_authority(origin_header)
            if origin is None or origin[0] != host or (explicit_port is not None and origin[1] != explicit_port):
                return BoundaryRejection(
                    status_code=403,
                    code="origin_mismatch",
                    message="Browser mutations must originate from the selected JoeOS host.",
                )
        return None

    def _host_allowed(self, host: str) -> bool:
        if host in self._allowed_hosts:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return (
                host == "localhost"
                or host.endswith(".localhost")
                or host.endswith(".local")
                or host.endswith(".ts.net")
                or bool(SINGLE_LABEL_PATTERN.fullmatch(host))
            )
        return any(address in network for network in PRIVATE_NETWORKS)

    @staticmethod
    def _request_authority(value: Optional[str]) -> Optional[tuple[str, Optional[int]]]:
        raw = (value or "").strip()
        if not raw or len(raw) > 512 or any(character in raw for character in "\r\n\0/@"):
            return None
        try:
            parsed = urlsplit("//" + raw)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return None
        if not host or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            return None
        return host.rstrip(".").lower(), port

    @staticmethod
    def _origin_authority(value: str) -> Optional[tuple[str, int]]:
        raw = value.strip()
        if not raw or len(raw) > 512 or raw.lower() == "null":
            return None
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        return parsed.hostname.rstrip(".").lower(), port or (443 if parsed.scheme == "https" else 80)

    @staticmethod
    def _configured_host(value: str) -> Optional[str]:
        host = value.strip().rstrip(".").lower()
        if not host:
            return None
        if host == "*":
            raise ValueError("JOEOS_ALLOWED_HOSTS cannot contain a wildcard.")
        try:
            return str(ipaddress.ip_address(host.strip("[]"))).lower()
        except ValueError:
            if not HOSTNAME_PATTERN.fullmatch(host) or ".." in host:
                raise ValueError("JOEOS_ALLOWED_HOSTS contains an invalid hostname.")
            return host
