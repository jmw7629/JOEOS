"""Contribution Registry for the JoeOS Plugin Platform.

The single authoritative registry for plugin-contributed commands, panels,
views, tools, agents, providers, parsers, importers, themes, automations, and
hardware adapters. Core contribution IDs can never be overwritten; collisions
are resolved deterministically. Contributions are registered in a disabled
state until their plugin is activated.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from .models import ContributionRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


CORE_CONTRIBUTION_IDS = frozenset(
    {
        # Commands that belong to the core and cannot be shadowed.
        "joeos.open_palette",
        "joeos.open_plugins",
        "joeos.open_settings",
        "joeos.save_workspace",
        "joeos.ask_assistant",
        # Panels that belong to the core.
        "joeos.panel.dashboard",
        "joeos.panel.mission_control",
        # Core service/status identities.
        "joeos.status.runtime",
    }
)


class ContributionError(RuntimeError):
    pass


class ContributionRegistry:
    """Persists plugin contributions and enforces authoritative uniqueness."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(
        self,
        *,
        plugin_id: str,
        contribution_id: str,
        contribution_type: str,
        title: str = "",
        description: str = "",
        commands: Tuple[str, ...] = (),
        requires_permissions: Tuple[str, ...] = (),
    ) -> ContributionRecord:
        if contribution_id in CORE_CONTRIBUTION_IDS:
            raise ContributionError("contribution id %r is reserved for the core." % contribution_id)
        now = _now()
        with self._lock, self._connection_factory() as connection:
            existing = connection.execute(
                "SELECT plugin_id FROM plugin_contributions WHERE contribution_id = ?",
                (contribution_id,),
            ).fetchone()
            if existing is not None and str(existing["plugin_id"]) != plugin_id:
                raise ContributionError(
                    "contribution id %r is already owned by another plugin." % contribution_id
                )
            connection.execute(
                """
                INSERT INTO plugin_contributions (
                    contribution_id, plugin_id, type, title, description, commands,
                    requires_permissions, state, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'registered', ?)
                ON CONFLICT(contribution_id) DO UPDATE SET
                    type = excluded.type, title = excluded.title,
                    description = excluded.description, commands = excluded.commands,
                    requires_permissions = excluded.requires_permissions,
                    state = 'registered', registered_at = excluded.registered_at
                """,
                (
                    contribution_id,
                    plugin_id,
                    contribution_type,
                    title[:120],
                    description[:240],
                    "\n".join(commands),
                    "\n".join(requires_permissions),
                    now,
                ),
            )
        return ContributionRecord(
            contribution_id=contribution_id,
            plugin_id=plugin_id,
            type=contribution_type,
            title=title,
            description=description,
            commands=commands,
            requires_permissions=requires_permissions,
            state="registered",
            registered_at=now,
        )

    def set_state(self, *, contribution_id: str, state: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_contributions SET state = ? WHERE contribution_id = ?",
                (state, contribution_id),
            )

    def set_plugin_state(self, *, plugin_id: str, state: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE plugin_contributions SET state = ? WHERE plugin_id = ?",
                (state, plugin_id),
            )

    def get(self, *, contribution_id: str) -> Optional[ContributionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_contributions WHERE contribution_id = ?", (contribution_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list_for(self, *, plugin_id: str, include_removed: bool = False) -> Tuple[ContributionRecord, ...]:
        clause = "" if include_removed else " AND state != 'removed'"
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_contributions WHERE plugin_id = ?"
                + clause
                + " ORDER BY contribution_id",
                (plugin_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def list_active(self) -> Tuple[ContributionRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_contributions WHERE state = 'active' ORDER BY plugin_id, contribution_id",
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def list_all(self) -> Tuple[ContributionRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_contributions ORDER BY plugin_id, contribution_id"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def unregister_all(self, *, plugin_id: str) -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "DELETE FROM plugin_contributions WHERE plugin_id = ?", (plugin_id,)
            )
        return cursor.rowcount

    @staticmethod
    def _row(row: sqlite3.Row) -> ContributionRecord:
        return ContributionRecord(
            contribution_id=str(row["contribution_id"]),
            plugin_id=str(row["plugin_id"]),
            type=str(row["type"]),
            title=str(row["title"]),
            description=str(row["description"]),
            commands=tuple(part for part in str(row["commands"]).split("\n") if part),
            requires_permissions=tuple(part for part in str(row["requires_permissions"]).split("\n") if part),
            state=str(row["state"]),
            registered_at=str(row["registered_at"]),
        )