"""Trusted local-console CLI for the P3B control plane.

Inspects provider/model/tool definitions and health. Never prints secret
values; credentials are only ever stored as opaque secret references.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence
from uuid import UUID

from server.identity.key_protection import load_or_create_identity_master_key

from .repository import SQLiteControlStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "joeos.db"


def _database_path(candidate: Optional[str]) -> Path:
    configured = candidate or __import__("os").getenv("JOEOS_DB_PATH", "")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE


def _store(database: Path) -> SQLiteControlStore:
    def connect():
        connection = sqlite3.connect(str(database), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    store = SQLiteControlStore(connect)
    store.prepare()
    return store


def _providers(arguments, store: SQLiteControlStore) -> int:
    for provider in store.list_providers():
        print("%s  %s  %s  %s  streaming:%s  %s" % (
            provider.key, provider.location, provider.status, provider.health,
            "yes" if provider.streaming else "no", provider.display_name,
        ))
    return 0


def _models(arguments, store: SQLiteControlStore) -> int:
    for model in store.list_models():
        print("%s  %s  %s  streaming:%s  tools:%s  %s" % (
            model.key, model.status, model.model_identifier,
            "yes" if model.streaming else "no",
            "yes" if model.tool_calling else "no", model.display_name,
        ))
    return 0


def _tools(arguments, store: SQLiteControlStore) -> int:
    for tool in store.list_tools():
        print("%s  %s  risk:%s  effect:%s  execution:%s  %s" % (
            tool.key, tool.status, tool.risk, tool.side_effect,
            tool.execution_availability, tool.display_name,
        ))
    return 0


def _provider_health(arguments, store: SQLiteControlStore) -> int:
    provider = store.get_provider_by_key(arguments.key)
    if provider is None:
        print("Provider not found.", file=sys.stderr)
        return 1
    print("key=%s  status=%s  health=%s  location=%s  streaming=%s" % (
        provider.key, provider.status, provider.health, provider.location, provider.streaming))
    return 0


def _model_capabilities(arguments, store: SQLiteControlStore) -> int:
    models = [m for m in store.list_models() if m.key == arguments.key]
    if not models:
        print("Model not found.", file=sys.stderr)
        return 1
    model = models[0]
    print("key=%s  status=%s  streaming=%s  tools=%s  structured=%s  vision=%s  reasoning=%s" % (
        model.key, model.status, model.streaming, model.tool_calling,
        model.structured_output, model.vision, model.reasoning,
    ))
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="python -m server.actions.cli",
        description="Local-console control-plane inspection.",
    )
    command.add_argument("--database", help="JoeOS SQLite database path.")
    subcommands = command.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("providers", help="List provider definitions.")
    subcommands.add_parser("models", help="List model definitions.")
    subcommands.add_parser("tools", help="List tool definitions.")
    health = subcommands.add_parser("provider-health", help="Inspect provider health.")
    health.add_argument("--key", required=True)
    caps = subcommands.add_parser("model-capabilities", help="Inspect model capabilities.")
    caps.add_argument("--key", required=True)
    return command


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    store = _store(_database_path(arguments.database))
    if arguments.operation == "providers":
        return _providers(arguments, store)
    if arguments.operation == "models":
        return _models(arguments, store)
    if arguments.operation == "tools":
        return _tools(arguments, store)
    if arguments.operation == "provider-health":
        return _provider_health(arguments, store)
    if arguments.operation == "model-capabilities":
        return _model_capabilities(arguments, store)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
