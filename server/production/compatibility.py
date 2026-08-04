"""Compatibility Registry.

Tracks explicit versioned contracts (application, schemas, API, plugin API,
backup format) and checks a candidate version against the current one. A
database or backup with a newer unknown format is never written; downgrades
are reported rather than attempted. Compatibility is never guessed from an
application version alone.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Dict, List, Optional

from .models import CompatibilityCheck


class CompatibilityRegistry:
    def __init__(self, schema_versions: Optional[Dict[str, str]] = None) -> None:
        self._schema_versions = dict(schema_versions or {})

    def record_schema_version(self, store: str, version: int) -> None:
        self._schema_versions[store] = str(int(version))

    def schema_version(self, store: str) -> Optional[str]:
        return self._schema_versions.get(store)

    def check_schema(
        self,
        store: str,
        current_schema: int,
        *,
        min_supported: int = 1,
        backup_format_required: int = 1,
    ) -> CompatibilityCheck:
        target = int(self._schema_versions.get(store, str(current_schema)))
        if current_schema > target:
            return CompatibilityCheck(
                component="schema." + store,
                current_version=str(current_schema),
                maximum_compatible=str(target),
                state="incompatible",
                detail="Database schema %s is newer than this application supports (%s). Writes are blocked; do not downgrade automatically." % (current_schema, target),
            )
        if current_schema < min_supported:
            return CompatibilityCheck(
                component="schema." + store,
                current_version=str(current_schema),
                minimum_supported=str(min_supported),
                state="update_required",
                detail="Database schema %s is older than the minimum supported version (%s); migration is required before writes." % (current_schema, min_supported),
            )
        if current_schema < target:
            return CompatibilityCheck(
                component="schema." + store,
                current_version=str(current_schema),
                required_version=str(target),
                state="compatible_with_warning",
                detail="Database schema %s is behind %s and will be migrated before writes." % (current_schema, target),
            )
        return CompatibilityCheck(component="schema." + store, current_version=str(current_schema), required_version=str(target), state="compatible")

    def check_backup_format(self, backup_format: int, required: int = 1) -> CompatibilityCheck:
        if backup_format > required:
            return CompatibilityCheck(
                component="backup.format",
                current_version=str(backup_format),
                maximum_compatible=str(required),
                state="incompatible",
                detail="Backup format %s is newer than this application supports (%s). Restore is blocked." % (backup_format, required),
            )
        if backup_format < 1:
            return CompatibilityCheck(component="backup.format", current_version=str(backup_format), state="incompatible", detail="Backup format is unknown.")
        return CompatibilityCheck(component="backup.format", current_version=str(backup_format), required_version=str(required), state="compatible")

    def all(self) -> List[CompatibilityCheck]:
        return [
            CompatibilityCheck(component="schema." + store, current_version=version, state="compatible")
            for store, version in sorted(self._schema_versions.items())
        ]
