"""Versioned SQLite storage for the Memory and Knowledge Platform.

The memory database lives under the JoeOS data directory and stores typed
records with provenance. Sensitive excerpts are stored only when explicitly
accepted by policy; secret values are never stored. Storage is versioned and
refuses to load a newer schema rather than silently corrupting.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("memory_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    subtype TEXT NOT NULL DEFAULT 'unknown',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    structured_content TEXT NOT NULL DEFAULT '',
    primary_scope TEXT NOT NULL,
    related_scopes TEXT NOT NULL DEFAULT '',
    subject_entity TEXT,
    predicate TEXT NOT NULL DEFAULT '',
    object_entity TEXT,
    valid_from TEXT,
    valid_to TEXT,
    learned_at TEXT NOT NULL,
    confirmed_at TEXT,
    last_reviewed_at TEXT,
    expires_at TEXT,
    source_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'explicit_user',
    extractor_version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    evidence_ids TEXT NOT NULL DEFAULT '',
    provenance_chain TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL,
    confidence_explanation TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL,
    claim_state TEXT NOT NULL,
    review_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL DEFAULT 'none',
    superseded_state TEXT NOT NULL DEFAULT 'none',
    superseded_by TEXT,
    supersedes TEXT,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    sensitivity_labels TEXT NOT NULL DEFAULT '',
    retention_mode TEXT NOT NULL DEFAULT 'indefinite',
    retrieval_tags TEXT NOT NULL DEFAULT '',
    embedding_state TEXT NOT NULL DEFAULT 'not_embedded',
    embedding_model TEXT,
    embedding_dimension INTEGER,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    stale TEXT NOT NULL DEFAULT 'fresh',
    temporal_state TEXT NOT NULL DEFAULT 'currently_valid',
    related_memories TEXT NOT NULL DEFAULT '',
    related_decisions TEXT NOT NULL DEFAULT '',
    related_tasks TEXT NOT NULL DEFAULT '',
    related_projects TEXT NOT NULL DEFAULT '',
    related_documents TEXT NOT NULL DEFAULT '',
    deletion_state TEXT NOT NULL DEFAULT 'active',
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_records_scope ON memory_records(primary_scope);
CREATE INDEX IF NOT EXISTS idx_mem_records_type ON memory_records(memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_records_deleted ON memory_records(deletion_state);
CREATE INDEX IF NOT EXISTS idx_mem_records_updated ON memory_records(updated_at);
CREATE INDEX IF NOT EXISTS idx_mem_records_claim ON memory_records(claim_state);

CREATE TABLE IF NOT EXISTS memory_versions (
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    snapshot_hash TEXT NOT NULL,
    content_snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, version)
);

CREATE TABLE IF NOT EXISTS memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    source_version TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    timestamp TEXT,
    content_hash TEXT NOT NULL,
    excerpt TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'internal',
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    availability TEXT NOT NULL DEFAULT 'available',
    stale INTEGER NOT NULL DEFAULT 0,
    redaction_state TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_evidence_ref ON memory_evidence(source_reference);

CREATE TABLE IF NOT EXISTS memory_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'explicit_user',
    extractor_version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    learned_at TEXT NOT NULL,
    confidence TEXT NOT NULL,
    attributes TEXT NOT NULL DEFAULT '',
    valid_from TEXT,
    valid_to TEXT,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    merge_state TEXT NOT NULL DEFAULT 'unresolved',
    merged_into TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    stale INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_entities_name ON memory_entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_mem_entities_scope ON memory_entities(scope);

CREATE TABLE IF NOT EXISTS memory_relationships (
    relationship_id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    evidence TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL,
    authority TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'explicit_user',
    extractor_version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    learned_at TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'proposed',
    stale INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_rels_src ON memory_relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_mem_rels_tgt ON memory_relationships(target_entity_id);

CREATE TABLE IF NOT EXISTS memory_duplicates (
    duplicate_id TEXT PRIMARY KEY,
    memory_id_a TEXT NOT NULL,
    memory_id_b TEXT NOT NULL,
    state TEXT NOT NULL,
    similarity REAL NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id TEXT PRIMARY KEY,
    memory_id_a TEXT NOT NULL,
    memory_id_b TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    authority_a TEXT NOT NULL,
    authority_b TEXT NOT NULL,
    confidence_a TEXT NOT NULL,
    confidence_b TEXT NOT NULL,
    temporal_a TEXT,
    temporal_b TEXT,
    recommended_resolution TEXT NOT NULL DEFAULT '',
    review_required INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'open',
    resolved_action TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_review_queue (
    review_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    affected_ids TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'explicit_user',
    extractor_version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    learned_at TEXT NOT NULL,
    authority TEXT NOT NULL,
    confidence TEXT NOT NULL,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    temporal_state TEXT NOT NULL DEFAULT 'currently_valid',
    proposed_action TEXT NOT NULL DEFAULT 'accept',
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_date TEXT,
    modified_date TEXT,
    import_date TEXT NOT NULL,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    content_hash TEXT NOT NULL,
    parser TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    extraction_state TEXT NOT NULL DEFAULT 'none',
    chunking_state TEXT NOT NULL DEFAULT 'none',
    indexing_state TEXT NOT NULL DEFAULT 'none',
    error_state TEXT NOT NULL DEFAULT '',
    related_project TEXT,
    deletion_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_notes (
    note_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    projects TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    retention_mode TEXT NOT NULL DEFAULT 'indefinite',
    version INTEGER NOT NULL DEFAULT 1,
    deletion_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_semantic_chunks (
    chunk_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    content_version INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'explicit_user',
    extractor_version TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    learned_at TEXT NOT NULL,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    confidence TEXT NOT NULL,
    authority TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    embedding_model TEXT NOT NULL DEFAULT 'token-overlap/1.0',
    embedding_dimension INTEGER NOT NULL DEFAULT 0,
    embedding_version TEXT NOT NULL DEFAULT '1.0.0',
    chunking_version TEXT NOT NULL DEFAULT '1.0.0',
    stale INTEGER NOT NULL DEFAULT 0,
    deletion_state TEXT NOT NULL DEFAULT 'active',
    tokens TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_chunks_memory ON memory_semantic_chunks(memory_id);

CREATE TABLE IF NOT EXISTS memory_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_activity (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_value TEXT NOT NULL DEFAULT '',
    refs TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_settings (
    scope TEXT NOT NULL,
    scope_value TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (scope, scope_value, key)
);

CREATE TABLE IF NOT EXISTS memory_retention (
    scope TEXT NOT NULL,
    scope_value TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    fixed_duration_days INTEGER,
    sensitive_override_days INTEGER,
    PRIMARY KEY (scope, scope_value)
);
"""


class MemoryStorage:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "memory.db"
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
            connection.execute("PRAGMA busy_timeout = 10000")
            self._local.connection = connection
        return connection

    def prepare(self) -> None:
        with self._lock:
            connection = self.connect()
            self._verify_or_migrate(connection)
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO memory_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM memory_meta WHERE key = 'memory_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "memory storage version %d is newer than supported version %d" % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "memory storage version %d predates supported version %d; migration required" % (current, STORAGE_VERSION)
                )

    def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def path(self) -> str:
        return str(self._path)

    def backup_to(self, target_dir: str) -> Optional[str]:
        target = Path(target_dir) / ("memory-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8])
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)
