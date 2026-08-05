"""Trusted local-console CLI for the runner execution plane.

Enrollment challenges, runner lifecycle, and emergency stop. Never prints
private keys or secret values.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Sequence
from uuid import UUID, uuid4

from server.identity.key_protection import load_or_create_identity_master_key

from .repository import SQLiteRunnerStore
from .service import RunnerService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "joeos.db"


def _database_path(candidate: Optional[str]) -> Path:
    import os
    configured = candidate or os.getenv("JOEOS_DB_PATH", "")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE


def _service(database: Path) -> RunnerService:
    def connect():
        connection = sqlite3.connect(str(database), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    from server.api.bootstrap.repository import SQLiteServerIdentityRepository
    installation = SQLiteServerIdentityRepository(connect)
    installation.prepare()
    service = RunnerService(
        SQLiteRunnerStore(connect),
        installation_id=installation.get_or_create_server_id,
    )
    service.prepare()
    return service


def _principal_from_db(database: Path) -> dict:
    def connect():
        connection = sqlite3.connect(str(database), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    with connect() as connection:
        org = connection.execute("SELECT id FROM authority_organizations LIMIT 1").fetchone()
        ws = connection.execute("SELECT id FROM authority_workspaces LIMIT 1").fetchone()
        user = connection.execute("SELECT id FROM authority_users WHERE status='active' LIMIT 1").fetchone()
    if not org or not ws or not user:
        return {}
    caps = [
        "runner.read", "runner.manage", "runner.enroll", "execution.read",
        "execution.request", "execution.cancel", "execution.emergency_stop",
        "artifact.read", "executor.read", "executor.manage",
        "secret.reference.read", "secret.reference.manage",
    ]
    return {
        "session_id": uuid4(), "device_id": uuid4(),
        "user": {"id": UUID(str(user["id"])), "status": "active"},
        "organization": {"id": UUID(str(org["id"]))},
        "workspace": {"id": UUID(str(ws["id"]))},
        "roles": ["joeos.owner"], "capabilities": caps,
    }


def _enroll_challenge(arguments, service: RunnerService) -> int:
    principal = _principal_from_db(_database_path(arguments.database))
    if not principal:
        print("Bootstrap the local owner first.", file=sys.stderr)
        return 1
    result = service.create_enrollment_challenge(principal, arguments.fingerprint)
    print("Runner enrollment challenge created.")
    print("  challenge_id:    %s" % result["challenge_id"])
    print("  installation_id: %s" % result["installation_id"])
    print("  expires_at:      %s" % result["expires_at"])
    print("Run the enrollment command on the runner with this challenge and the runner public key.")
    return 0


def _list_runners(arguments, service: RunnerService) -> int:
    for runner in service._store.list_runners():
        print("%s  %s  %s  %s  %s" % (runner.key, runner.status, runner.health,
                                       runner.runner_version, runner.display_name))
    return 0


def _revoke(arguments, service: RunnerService) -> int:
    principal = _principal_from_db(_database_path(arguments.database))
    service.revoke_runner(principal, UUID(arguments.runner_id))
    print("Runner %s revoked; connections closed and secret leases invalidated." % arguments.runner_id)
    return 0


def _emergency_stop(arguments, service: RunnerService) -> int:
    principal = _principal_from_db(_database_path(arguments.database))
    if not principal:
        print("Bootstrap the local owner first.", file=sys.stderr)
        return 1
    result = service.emergency_stop(principal, scope="workspace")
    print("Emergency stop: dispatch paused, %d queued job(s) cancelled." % result["queued_cancelled"])
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="python -m server.runners.cli",
        description="Local-console runner plane operations.",
    )
    command.add_argument("--database", help="JoeOS SQLite database path.")
    subcommands = command.add_subparsers(dest="operation", required=True)
    challenge = subcommands.add_parser("enroll-challenge", help="Create a one-time runner enrollment challenge.")
    challenge.add_argument("--fingerprint", required=True, help="Expected machine fingerprint.")
    subcommands.add_parser("runners", help="List runners.")
    revoke = subcommands.add_parser("revoke", help="Revoke a runner.")
    revoke.add_argument("runner_id")
    subcommands.add_parser("emergency-stop", help="Pause dispatch and cancel queued jobs.")
    return command


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    service = _service(_database_path(arguments.database))
    if arguments.operation == "enroll-challenge":
        return _enroll_challenge(arguments, service)
    if arguments.operation == "runners":
        return _list_runners(arguments, service)
    if arguments.operation == "revoke":
        return _revoke(arguments, service)
    if arguments.operation == "emergency-stop":
        return _emergency_stop(arguments, service)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
