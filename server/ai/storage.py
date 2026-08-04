"""Versioned SQLite storage for the Local AI Runtime platform.

Persists AI-assisted interpretation records and embedding metadata (content
hashes, model, dimension — never raw vectors or source content). Retention
keeps history bounded. No prompts, source content, or secrets are stored.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Callable, Optional

STORAGE_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ai_interpretations (
    interpretation_id TEXT PRIMARY KEY,
    interpretation_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    model TEXT NOT NULL DEFAULT '',
    runtime TEXT NOT NULL DEFAULT 'local',
    privacy_class TEXT NOT NULL DEFAULT 'restricted',
    is_ai_assisted INTEGER NOT NULL DEFAULT 1,
    project TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_embeddings (
    embedding_id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    privacy_class TEXT NOT NULL DEFAULT 'restricted',
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(source_ref, content_hash, model)
);
CREATE INDEX IF NOT EXISTS idx_ai_embeddings_hash ON ai_embeddings(content_hash);

CREATE TABLE IF NOT EXISTS ai_context (
    context_id TEXT PRIMARY KEY,
    project TEXT NOT NULL DEFAULT '',
    candidates_considered INTEGER NOT NULL DEFAULT 0,
    sources_selected TEXT NOT NULL DEFAULT '[]',
    sources_excluded TEXT NOT NULL DEFAULT '[]',
    duplicate_tokens_removed INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    token_budget INTEGER NOT NULL DEFAULT 0,
    construction_ms REAL NOT NULL DEFAULT 0,
    privacy_decisions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_RETENTION = {
    "ai_interpretations": 2000,
    "ai_embeddings": 20000,
    "ai_context": 2000,
}


class AIStorage:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], *, retention: Optional[dict] = None) -> None:
        self._connection_factory = connection_factory
        self._retention = dict(DEFAULT_RETENTION)
        if retention:
            self._retention.update(retention)
        self._lock = threading.RLock()
        self.prepare()

    def prepare(self) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO ai_meta (key, value) VALUES (?, ?)",
                ("ai_schema_version", str(STORAGE_VERSION)),
            )

    def insert_interpretation(self, values: dict) -> None:
        import json
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO ai_interpretations (
                    interpretation_id, interpretation_type, summary, basis, confidence,
                    model, runtime, privacy_class, is_ai_assisted, project, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["interpretation_id"], values["interpretation_type"], values["summary"],
                    json.dumps(values.get("basis", []), separators=(",", ":")), values.get("confidence"),
                    values.get("model", ""), values.get("runtime", "local"),
                    values.get("privacy_class", "restricted"),
                    1 if values.get("is_ai_assisted", True) else 0,
                    values.get("project", ""), values.get("created_at", ""),
                ),
            )
            self._prune(connection, "ai_interpretations", self._retention["ai_interpretations"], key_col="interpretation_id")

    def list_interpretations(self, interpretation_type: str = "", limit: int = 100) -> list:
        import json
        sql = "SELECT * FROM ai_interpretations"
        params = []
        if interpretation_type:
            sql += " WHERE interpretation_type = ?"
            params.append(interpretation_type)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["basis"] = json.loads(item.get("basis") or "[]")
            item["is_ai_assisted"] = bool(item.get("is_ai_assisted"))
            result.append(item)
        return result

    def count_interpretations(self) -> int:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute("SELECT COUNT(*) FROM ai_interpretations").fetchone()
        return int(row[0] if row else 0)

    def delete_interpretation(self, interpretation_id: str) -> bool:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute("DELETE FROM ai_interpretations WHERE interpretation_id = ?", (interpretation_id,))
        return cursor.rowcount > 0

    def insert_embedding_metadata(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_embeddings (
                    embedding_id, source_ref, content_hash, model, dimension, privacy_class, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["embedding_id"], values["source_ref"], values["content_hash"],
                    values["model"], values["dimension"], values.get("privacy_class", "restricted"),
                    values.get("created_at", ""),
                ),
            )
            self._prune(connection, "ai_embeddings", self._retention["ai_embeddings"])

    def embedding_dedupe_hashes(self, hashes: list) -> set:
        if not hashes:
            return set()
        placeholders = ",".join("?" for _ in hashes)
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT content_hash FROM ai_embeddings WHERE content_hash IN (%s)" % placeholders,
                hashes,
            ).fetchall()
        return {str(row["content_hash"]) for row in rows}

    def insert_context(self, values: dict) -> None:
        import json
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO ai_context (
                    context_id, project, candidates_considered, sources_selected, sources_excluded,
                    duplicate_tokens_removed, tokens_used, token_budget, construction_ms,
                    privacy_decisions, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["context_id"], values.get("project", ""), values.get("candidates_considered", 0),
                    json.dumps(values.get("sources_selected", []), separators=(",", ":")),
                    json.dumps(values.get("sources_excluded", []), separators=(",", ":")),
                    values.get("duplicate_tokens_removed", 0), values.get("tokens_used", 0),
                    values.get("token_budget", 0), values.get("construction_ms", 0.0),
                    json.dumps(values.get("privacy_decisions", []), separators=(",", ":")),
                    values.get("created_at", ""),
                ),
            )
            self._prune(connection, "ai_context", self._retention["ai_context"], key_col="context_id")

    def _prune(self, connection: sqlite3.Connection, table: str, keep: int, *, key_col: str = "embedding_id") -> None:
        connection.execute(
            "DELETE FROM %s WHERE %s NOT IN (SELECT %s FROM %s ORDER BY created_at DESC LIMIT ?)"
            % (table, key_col, key_col, table),
            (keep,),
        )
