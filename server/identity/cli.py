from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import ValidationError

from server.api.bootstrap.models import BootstrapDocument
from server.api.bootstrap.repository import SQLiteServerIdentityRepository

from .repository import SQLiteDeviceIdentityRepository
from .key_protection import (
    IdentityKeyConfigurationError,
    PairingKeyProtector,
    load_or_create_identity_master_key,
)
from .service import DeviceEnrollmentService, EnrollmentOriginError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "joeos.db"


def _database_path(candidate: Optional[str]) -> Path:
    configured = candidate or os.getenv("JOEOS_DB_PATH", "")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _service(database: Path) -> DeviceEnrollmentService:
    created_parent = not database.parent.exists()
    database.parent.mkdir(parents=True, exist_ok=True)
    if created_parent:
        with suppress(OSError):
            database.parent.chmod(0o700)
    connection_factory = lambda: _connect(database)
    with connection_factory() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    server_repository = SQLiteServerIdentityRepository(connection_factory)
    server_repository.prepare()
    service = DeviceEnrollmentService(
        repository=SQLiteDeviceIdentityRepository(
            connection_factory,
            PairingKeyProtector(load_or_create_identity_master_key(database)),
        ),
        server_id_provider=server_repository.get_or_create_server_id,
        allowed_https_hosts=os.getenv("JOEOS_ALLOWED_HOSTS", "").split(","),
    )
    service.prepare()
    with suppress(OSError):
        database.chmod(0o600)
    return service


def _command_output(arguments: Sequence[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            list(arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    value = result.stdout.strip()
    return value or None


def _detected_origin(port: int) -> str:
    return _detected_origins(port)[0]


def _detected_origins(port: int) -> list[str]:
    configured = os.getenv("JOEOS_PUBLIC_ORIGIN", "").strip()
    if configured:
        return [configured]
    candidates = []
    serve_origin = _detected_tailscale_serve_origin(port)
    if serve_origin:
        candidates.append(serve_origin)
    address = _command_output(("tailscale", "ip", "-4"))
    if address:
        candidates.append("http://%s:%s" % (address.splitlines()[0].strip(), port))
    candidates.append("http://127.0.0.1:%s" % port)
    return list(dict.fromkeys(candidates))


def _detected_tailscale_serve_origin(port: int) -> Optional[str]:
    status = _command_output(("tailscale", "serve", "status", "--json"))
    if not status:
        return None
    try:
        document = json.loads(status)
        web = document.get("Web", {})
        funnel = document.get("AllowFunnel", {})
        if not isinstance(web, dict) or not isinstance(funnel, dict):
            return None
        expected_proxy = "http://127.0.0.1:%d" % port
        for authority in sorted(web):
            configuration = web[authority]
            handlers = configuration.get("Handlers", {}) if isinstance(configuration, dict) else {}
            proxies = {
                str(handler.get("Proxy", "")).rstrip("/")
                for handler in handlers.values()
                if isinstance(handler, dict)
            } if isinstance(handlers, dict) else set()
            if expected_proxy not in proxies or funnel.get(authority) is True:
                continue
            parsed = urlsplit("//" + authority)
            host = (parsed.hostname or "").rstrip(".").lower()
            if (
                not host.endswith(".ts.net")
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
            ):
                continue
            return "https://" + host
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _verify_running_backend(origin: str, service: DeviceEnrollmentService) -> None:
    expected_url = origin + "/api/v1/bootstrap"
    request = Request(
        expected_url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "JoeOS-Local-Pairing/1"},
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=3) as response:
            if response.status != 200 or response.geturl() != expected_url:
                raise ValueError
            media_type = response.headers.get_content_type().lower()
            if media_type != "application/json":
                raise ValueError
            body = response.read(65_537)
            if not body or len(body) > 65_536:
                raise ValueError
            document = BootstrapDocument.model_validate_json(body)
    except (HTTPError, URLError, OSError, UnicodeError, ValueError, ValidationError):
        raise EnrollmentOriginError(
            "JoeOS is not reachable at this exact origin. Start JoeOS, verify the address, and retry."
        ) from None

    try:
        enrollment = next(
            capability
            for capability in document.capabilities
            if capability.id == "identity.device_enrollment"
        )
        if (
            document.server.server_id != service.observed_server_id()
            or enrollment.status != "available"
            or enrollment.access != "enrollment"
        ):
            raise ValueError
    except (StopIteration, TypeError, ValueError):
        raise EnrollmentOriginError(
            "The running JoeOS server does not match this local identity database."
        ) from None


def _issue(arguments, service: DeviceEnrollmentService) -> int:
    origins = [arguments.origin] if arguments.origin else _detected_origins(arguments.port)
    try:
        last_error = None
        origin = ""
        for candidate in origins:
            try:
                origin = service.validate_pairing_origin(candidate)
                _verify_running_backend(origin, service)
                break
            except (EnrollmentOriginError, ValueError) as error:
                last_error = error
        else:
            raise last_error or EnrollmentOriginError("No JoeOS origin was detected.")
        offer = service.issue_pairing_offer(origin)
    except (EnrollmentOriginError, ValueError) as error:
        print("Pairing offer was not created: %s" % error, file=sys.stderr)
        return 2
    print("JoeOS iPhone pairing window")
    print("Origin: %s" % offer.audience_origin)
    print("Expires: %s" % offer.expires_at.isoformat().replace("+00:00", "Z"))
    print()
    print(offer.manual_code)
    print()
    print("This one-use code pairs keys only. It grants no role, approval, or execution authority.")
    print("Keep it private and enter it only in the JoeOS native client for the origin shown above.")
    return 0


def _list(service: DeviceEnrollmentService) -> int:
    devices = service.list_devices()
    if not devices:
        print("No JoeOS devices have been paired.")
        return 0
    for device in devices:
        stamp = DeviceEnrollmentService._datetime(device.enrolled_at).isoformat().replace("+00:00", "Z")
        print("%s  %s  %s  %s  %s" % (
            device.device_id,
            device.state,
            device.platform,
            stamp,
            device.display_name,
        ))
    return 0


def _revoke(arguments, service: DeviceEnrollmentService) -> int:
    try:
        device_id = UUID(arguments.device_id)
        revoked = service.revoke_device(device_id, arguments.reason)
    except ValueError as error:
        print("Device was not revoked: %s" % error, file=sys.stderr)
        return 2
    if not revoked:
        print("No active paired device matched that ID.", file=sys.stderr)
        return 1
    print("Revoked JoeOS device %s." % device_id)
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="python -m server.identity.cli",
        description="Local-only JoeOS device pairing and revocation.",
    )
    command.add_argument("--database", help="JoeOS SQLite database path.")
    subcommands = command.add_subparsers(dest="operation", required=True)
    issue = subcommands.add_parser("issue", help="Open one five-minute iPhone pairing window.")
    issue.add_argument("--origin", help="Exact private JoeOS origin shown to the iPhone.")
    issue.add_argument("--port", type=int, default=int(os.getenv("JOEOS_PORT", "8080")))
    subcommands.add_parser("list", help="List paired devices without printing key material.")
    revoke = subcommands.add_parser("revoke", help="Revoke one paired device locally.")
    revoke.add_argument("device_id")
    revoke.add_argument("--reason", default="Revoked by the local JoeOS operator")
    return command


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        service = _service(_database_path(arguments.database))
    except IdentityKeyConfigurationError as error:
        print("JoeOS identity configuration is unavailable: %s" % error, file=sys.stderr)
        return 2
    if arguments.operation == "issue":
        return _issue(arguments, service)
    if arguments.operation == "list":
        return _list(service)
    if arguments.operation == "revoke":
        return _revoke(arguments, service)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
