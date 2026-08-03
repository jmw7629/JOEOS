"""Versioned SQLite storage with migration and corruption detection.

The index database lives under the JoeOS data directory and holds only
metadata. Content hashes let incremental scans skip unchanged files without
storing file contents.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("intelligence_schema_version", "1")


class Storage:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "intelligence.db"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self._path))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            self._local.connection = connection
        return connection

    def prepare(self) -> None:
        with self._lock:
            connection = self.connect()
            self._verify_or_migrate(connection)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intelligence_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS intelligence_files (
                    file_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    extension TEXT NOT NULL DEFAULT '',
                    language TEXT,
                    binary INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT,
                    content_hash TEXT NOT NULL,
                    classification TEXT NOT NULL DEFAULT 'unknown',
                    classification_confidence TEXT NOT NULL DEFAULT 'uncertain',
                    tracked INTEGER NOT NULL DEFAULT 1,
                    ignored INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    generated INTEGER NOT NULL DEFAULT 0,
                    secret_sensitive INTEGER NOT NULL DEFAULT 0,
                    git_state TEXT NOT NULL DEFAULT 'clean',
                    parser TEXT,
                    parser_available INTEGER NOT NULL DEFAULT 0,
                    symbol_count INTEGER NOT NULL DEFAULT 0,
                    reference_count INTEGER NOT NULL DEFAULT 0,
                    last_indexed_at TEXT,
                    stale INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_id, rel_path)
                );
                CREATE INDEX IF NOT EXISTS idx_intel_files_project ON intelligence_files(project_id);

                CREATE TABLE IF NOT EXISTS intelligence_symbols (
                    symbol_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    language TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'unknown',
                    exported INTEGER NOT NULL DEFAULT 0,
                    signature TEXT NOT NULL DEFAULT '',
                    parent_symbol TEXT,
                    module TEXT NOT NULL DEFAULT '',
                    documentation TEXT NOT NULL DEFAULT '',
                    parser TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    content_version TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_intel_symbols_project ON intelligence_symbols(project_id);
                CREATE INDEX IF NOT EXISTS idx_intel_symbols_file ON intelligence_symbols(file_id);
                CREATE INDEX IF NOT EXISTS idx_intel_symbols_name ON intelligence_symbols(name);

                CREATE TABLE IF NOT EXISTS intelligence_references (
                    reference_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_symbol_id TEXT,
                    target_symbol_id TEXT,
                    source_file_id TEXT NOT NULL,
                    target_file_id TEXT,
                    rel_path TEXT NOT NULL,
                    target_text TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    resolution TEXT NOT NULL,
                    parser TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_intel_refs_project ON intelligence_references(project_id);
                CREATE INDEX IF NOT EXISTS idx_intel_refs_source ON intelligence_references(source_file_id);
                CREATE INDEX IF NOT EXISTS idx_intel_refs_target ON intelligence_references(target_text);

                CREATE TABLE IF NOT EXISTS intelligence_dependency_edges (
                    source_file_id TEXT NOT NULL,
                    target_file_id TEXT NOT NULL,
                    source_rel_path TEXT NOT NULL,
                    target_rel_path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    direct INTEGER NOT NULL DEFAULT 1,
                    resolution TEXT NOT NULL,
                    project_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intel_edges_project ON intelligence_dependency_edges(project_id);

                CREATE TABLE IF NOT EXISTS intelligence_risk_findings (
                    risk_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    affected_items TEXT NOT NULL DEFAULT '',
                    mitigation TEXT NOT NULL DEFAULT '',
                    review_required INTEGER NOT NULL DEFAULT 0,
                    recommended_tests TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intel_risks_project ON intelligence_risk_findings(project_id);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO intelligence_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM intelligence_meta WHERE key = 'intelligence_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "index storage version %d is newer than supported version %d" % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "index storage version %d predates supported version %d; reindex required" % (current, STORAGE_VERSION)
                )

    def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def path(self) -> str:
        return str(self._path)

    def backup_to(self, target_dir: str) -> Optional[str]:
        target = Path(target_dir) / ("intelligence-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8])
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)
