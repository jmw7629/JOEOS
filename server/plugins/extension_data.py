"""Scoped Extension Storage and Extension Settings for the Plugin Platform.

Extension storage is isolated per plugin and per scope, with an enforced quota.
Settings contributed by extensions are validated against their declared schema
and stored through the platform so a plugin can never weaken JoeOS policy.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .models import SettingDeclaration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExtensionStorageError(RuntimeError):
    pass


class ExtensionStorageService:
    """Per-plugin, per-scope key/value storage with quotas and isolation."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def put(
        self,
        *,
        plugin_id: str,
        key: str,
        value: str,
        scope: str = "global",
        quota_bytes: int = 10 * 1024 * 1024,
        schema_version: int = 1,
    ) -> None:
        if not key or len(key) > 120:
            raise ExtensionStorageError("invalid storage key.")
        if type(value) is not str or len(value.encode("utf-8")) > quota_bytes:
            raise ExtensionStorageError("value exceeds the storage quota.")
        now = _now()
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(value)), 0) AS total FROM plugin_storage WHERE plugin_id = ?",
                (plugin_id,),
            ).fetchone()
            total = int(row["total"]) if row is not None else 0
            current_row = connection.execute(
                "SELECT COALESCE(MAX(LENGTH(value)), 0) AS length FROM plugin_storage WHERE plugin_id = ? AND key = ? AND scope = ?",
                (plugin_id, key, scope),
            ).fetchone()
            current = int(current_row["length"]) if current_row is not None else 0
            if total - current + len(value.encode("utf-8")) > quota_bytes:
                raise ExtensionStorageError("storage quota exceeded.")
            connection.execute(
                """
                INSERT INTO plugin_storage (storage_id, plugin_id, key, value, scope, schema_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(storage_id) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                ("%s:%s:%s" % (plugin_id, key, scope), plugin_id, key, value, scope, schema_version, now),
            )

    def get(self, *, plugin_id: str, key: str, scope: str = "global") -> Optional[str]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT value FROM plugin_storage WHERE plugin_id = ? AND key = ? AND scope = ?",
                (plugin_id, key, scope),
            ).fetchone()
        return str(row["value"]) if row else None

    def delete(self, *, plugin_id: str, key: str, scope: str = "global") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM plugin_storage WHERE plugin_id = ? AND key = ? AND scope = ?",
                (plugin_id, key, scope),
            )

    def list_for(self, *, plugin_id: str) -> Tuple[Dict[str, str], ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT key, scope, LENGTH(value) AS size FROM plugin_storage WHERE plugin_id = ?",
                (plugin_id,),
            ).fetchall()
        return tuple({"key": row["key"], "scope": row["scope"], "size_bytes": int(row["size"])} for row in rows)

    def size_for(self, *, plugin_id: str) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(value)), 0) AS total FROM plugin_storage WHERE plugin_id = ?",
                (plugin_id,),
            ).fetchone()
        return int(row["total"])


class ExtensionSettingsService:
    """Validated contributed settings for extensions."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def set(
        self,
        *,
        plugin_id: str,
        declarations: Tuple[SettingDeclaration, ...],
        key: str,
        value: Any,
        scope: str = "global",
    ) -> Any:
        declaration = next((decl for decl in declarations if decl.key == key), None)
        if declaration is None:
            raise ExtensionStorageError("setting %r is not declared by this plugin." % key)
        validated = self._validate(declaration, value)
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_settings (setting_id, plugin_id, key, value, scope, sensitive, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(setting_id) DO UPDATE SET
                    value = excluded.value, sensitive = excluded.sensitive, updated_at = excluded.updated_at
                """,
                (
                    "%s:%s:%s" % (plugin_id, key, scope),
                    plugin_id,
                    key,
                    str(validated),
                    scope,
                    1 if declaration.sensitive else 0,
                    now,
                ),
            )
        return validated

    def get(
        self,
        *,
        plugin_id: str,
        declarations: Tuple[SettingDeclaration, ...],
        key: str,
        scope: str = "global",
    ) -> Any:
        declaration = next((decl for decl in declarations if decl.key == key), None)
        if declaration is None:
            raise ExtensionStorageError("setting %r is not declared by this plugin." % key)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT value, sensitive FROM plugin_settings WHERE plugin_id = ? AND key = ? AND scope = ?",
                (plugin_id, key, scope),
            ).fetchone()
        if row is None:
            return declaration.default
        value = row["value"]
        if bool(row["sensitive"]):
            return "********"
        return self._coerce(declaration, value)

    def all_for(self, *, plugin_id: str) -> Dict[str, str]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT key, value, scope, sensitive FROM plugin_settings WHERE plugin_id = ?",
                (plugin_id,),
            ).fetchall()
        return {
            str(row["key"]) + (":" + str(row["scope"]) if row["scope"] != "global" else ""): (
                "********" if bool(row["sensitive"]) else str(row["value"])
            )
            for row in rows
        }

    @staticmethod
    def _validate(declaration: SettingDeclaration, value: Any) -> Any:
        kind = declaration.type
        if kind == "string":
            if not isinstance(value, str):
                raise ExtensionStorageError("expected a string setting.")
            return value
        if kind == "password":
            if not isinstance(value, str) or not value:
                raise ExtensionStorageError("expected a nonempty password setting.")
            return value
        if kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ExtensionStorageError("expected a numeric setting.")
            return value
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ExtensionStorageError("expected a boolean setting.")
            return value
        if kind == "enum":
            choices = declaration.validation.get("choices") or []
            if value not in choices:
                raise ExtensionStorageError("enum value must be one of %s." % (choices,))
            return value
        raise ExtensionStorageError("unsupported setting type.")

    @staticmethod
    def _coerce(declaration: SettingDeclaration, value: str) -> Any:
        kind = declaration.type
        try:
            if kind == "number":
                return float(value) if "." in str(value) else int(value)
            if kind == "boolean":
                return value in {"True", "true", "1"}
            if kind == "enum":
                return value
            return value
        except (TypeError, ValueError):
            return declaration.default