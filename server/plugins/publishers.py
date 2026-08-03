"""Publisher Registry for the JoeOS Plugin Platform.

A publisher is a stable identity separate from plugin identity, display names,
package filenames, and marketplace listing titles. Publisher trust never
replaces plugin permission review, and one publisher cannot overwrite another
publisher's plugins. Familiar names are never treated as verified identity.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

from .models import PublisherRecord, PublisherVerificationState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PublisherNotFoundError(KeyError):
    pass


class PublisherService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    # ---- first-party bootstrap ----

    def register_first_party(self, publisher_id: str, display_name: str) -> PublisherRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_publishers (
                    publisher_id, display_name, verification_state, trusted,
                    first_party, signing_fingerprints, official_website, support,
                    revoked, blocked, known_plugin_ids, last_verified_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'first_party', 1, 1, '', '', '', 0, 0, '', ?, ?, ?)
                ON CONFLICT(publisher_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    updated_at = excluded.updated_at
                """,
                (publisher_id, display_name, now, now, now),
            )
            row = connection.execute(
                "SELECT * FROM plugin_publishers WHERE publisher_id = ?", (publisher_id,)
            ).fetchone()
        return self._row_to_record(row)

    # ---- queries ----

    def get(self, publisher_id: str) -> Optional[PublisherRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_publishers WHERE publisher_id = ?", (publisher_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def require(self, publisher_id: str) -> PublisherRecord:
        record = self.get(publisher_id)
        if record is None:
            raise PublisherNotFound(publisher_id)
        return record

    def list(self) -> Tuple[PublisherRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_publishers ORDER BY display_name"
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    # ---- state transitions ----

    def set_trust(self, publisher_id: str, trusted: bool) -> PublisherRecord:
        record = self.require(publisher_id)
        if record.revoked or record.blocked:
            raise PublisherStateError("Cannot edit trust of a revoked or blocked publisher.")
        state: PublisherVerificationState = (
            "user_trusted" if trusted else "unverified"
        )
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE plugin_publishers
                SET trusted = ?, verification_state = ?, updated_at = ?
                WHERE publisher_id = ?
                """,
                (1 if trusted else 0, state, now, publisher_id),
            )
        return self.require(publisher_id)

    def set_verification_state(self, publisher_id: str, state: PublisherVerificationState) -> PublisherRecord:
        trusted = 1 if state in {"first_party", "verified", "user_trusted"} else 0
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """INSERT INTO plugin_publishers (
                       publisher_id, display_name, verification_state, trusted,
                       first_party, signing_fingerprints, official_website, support,
                       revoked, blocked, known_plugin_ids, last_verified_at,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 0, '', '', '', 0, 0, '', '', ?, ?)
                   ON CONFLICT(publisher_id) DO UPDATE SET
                       verification_state = excluded.verification_state,
                       trusted = excluded.trusted,
                       updated_at = excluded.updated_at
                """,
                (
                    publisher_id,
                    publisher_id,
                    state,
                    trusted,
                    _now(),
                    _now(),
                ),
            )
        return self.require(publisher_id)

    def revoke(self, publisher_id: str, blocked: bool = False) -> PublisherRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE plugin_publishers
                SET revoked = ?, blocked = ?, trusted = 0,
                    verification_state = ?, updated_at = ?
                WHERE publisher_id = ?
                """,
                (1 if not blocked else 0, 1 if blocked else 0, "blocked" if blocked else "revoked", now, publisher_id),
            )
        return self.require(publisher_id)

    def set_signing_fingerprints(self, publisher_id: str, fingerprints: Tuple[str, ...]) -> PublisherRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """UPDATE plugin_publishers
                   SET signing_fingerprints = ?, updated_at = ?
                   WHERE publisher_id = ?""",
                ("\n".join(fingerprints), now, publisher_id),
            )
        return self.require(publisher_id)

    # ---- lookup registered plugin ids for a publisher ----

    def set_known_plugin_ids(self, publisher_id: str, plugin_ids: Tuple[str, ...]) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """UPDATE plugin_publishers
                   SET known_plugin_ids = ?, updated_at = ?
                   WHERE publisher_id = ?""",
                ("\n".join(plugin_ids), _now(), publisher_id),
            )

    def _row_to_record(self, row: sqlite3.Row) -> PublisherRecord:
        state = str(row["verification_state"])
        if state not in {"first_party", "verified", "user_trusted", "unverified", "unknown", "revoked", "blocked"}:
            state = "unknown"
        return PublisherRecord(
            publisher_id=str(row["publisher_id"]),
            display_name=str(row["display_name"]),
            verification_state=state,
            trusted=bool(row["trusted"]),
            first_party=bool(row["first_party"]),
            signing_fingerprints=tuple(
                part for part in (str(row["signing_fingerprints"]).split("\n") if row["signing_fingerprints"] else ())
            ),
            official_website=str(row["official_website"]),
            support=str(row["support"]),
            revoked=bool(row["revoked"]),
            blocked=bool(row["blocked"]),
            known_plugin_ids=tuple(
                part for part in (str(row["known_plugin_ids"]).split("\n") if row["known_plugin_ids"] else ())
            ),
            last_verified_at=str(row["last_verified_at"]),
            created_at=str(row["created_at"]),
        )


class PublisherNotFound(KeyError):
    pass


class PublisherStateError(RuntimeError):
    pass