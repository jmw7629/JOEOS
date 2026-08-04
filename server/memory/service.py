"""Memory and Knowledge Platform facade.

Provides typed persistence for memory records, entities, relationships,
evidence, documents, notes, conflicts, and review items. Retrieval uses a
bounded, deterministic token-overlap semantic index (no dense vectors) with
scope, authority, confidence, and temporal ranking. Secrets and hidden
reasoning are never stored.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

from .models import (
    ActivityEvent,
    ConflictRecord,
    DuplicateCandidate,
    EntityRecord,
    EvidenceRecord,
    ImportResult,
    MemoryHealth,
    MemoryHealthDiagnostics,
    MemoryOverview,
    MemoryRecord,
    MemoryVersion,
    NoteRecord,
    Provenance,
    RelationshipRecord,
    RetrievalEnvelope,
    RetrievalResult,
    ReviewEnvelope,
    ReviewItem,
    SemanticChunk,
)
from .storage import MemoryStorage

_STOP = frozenset(
    """
    a an and are as at be but by for from has have he her his i in is it its
    of on or our she that the their they this to we what when which who will
    with you your not do does did was were been being can could should would
    may might must shall about into over under while after before during
    """.split()
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _tokens(text: str) -> Tuple[str, ...]:
    tokens = re.split(r"[\s/._-]+", text.lower())
    return tuple(t for t in tokens if len(t) >= 3 and t not in _STOP)


class MemoryService:
    def __init__(self, data_dir: str) -> None:
        self._storage = MemoryStorage(data_dir)
        self._storage.prepare()
        self._connection_factory = self._storage.connect

    # ---- records ----

    def propose(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a memory record. AI-extracted material must arrive already
        marked as proposed by its caller; explicit user instruction may arrive
        accepted. Nothing here upgrades authority on its own."""
        now = _now()
        stored = record.model_copy(update={"updated_at": now})
        self._upsert_record(stored)
        self._index_chunk(stored, now)
        if stored.subtype not in {"decision_ref", "convention_ref"}:
            self._emit_activity(
                ActivityEvent(
                    event_id=_id("activity", stored.memory_id, "proposed"),
                    kind="memory_proposed",
                    summary="Memory proposed: %s" % stored.title,
                    scope=stored.primary_scope,
                    scope_value=stored.object_entity or "",
                    references=(stored.memory_id,),
                    created_at=now,
                )
            )
        return stored

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return _memory_from_row(row) if row else None

    def list(self, *, scope: Optional[str] = None, limit: int = 50) -> Tuple[MemoryRecord, ...]:
        with self._connection_factory() as connection:
            if scope:
                rows = connection.execute(
                    "SELECT * FROM memory_records WHERE deletion_state = 'active' AND primary_scope = ? ORDER BY updated_at DESC LIMIT ?",
                    (scope, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memory_records WHERE deletion_state = 'active' ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return tuple(_memory_from_row(row) for row in rows)

    def versions(self, memory_id: str) -> Tuple[MemoryVersion, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_versions WHERE memory_id = ? ORDER BY version DESC",
                (memory_id,),
            ).fetchall()
        return tuple(
            MemoryVersion(
                memory_id=row["memory_id"],
                version=row["version"],
                action=row["action"],
                changed_by=row["changed_by"],
                reason=row["reason"],
                snapshot_hash=row["snapshot_hash"],
                content_snapshot=row["content_snapshot"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def correct(self, memory_id: str, *, new_content: str, reason: str, changed_by: str = "") -> Optional[MemoryRecord]:
        """Create a new version of a memory record with the corrected content."""
        record = self.get(memory_id)
        if record is None:
            return None
        now = _now()
        self._snapshot(record, action="corrected", changed_by=changed_by, reason=reason, now=now)
        new_hash = _id("content", new_content)
        updated = record.model_copy(
            update={
                "content": new_content,
                "content_hash": new_hash,
                "version": record.version + 1,
                "updated_at": now,
                "review_state": "accepted",
                "stale": "fresh",
            }
        )
        self._upsert_record(updated)
        self._index_chunk(updated, now)
        self._emit_activity(
            ActivityEvent(
                event_id=_id("activity", memory_id, "corrected"),
                kind="memory_corrected",
                summary="Memory corrected: %s" % record.title,
                scope=updated.primary_scope,
                references=(memory_id,),
                created_at=now,
            )
        )
        return updated

    def supersede(self, memory_id: str, replacement_id: str, *, reason: str) -> Optional[MemoryRecord]:
        record = self.get(memory_id)
        if record is None or record.deletion_state != "active":
            return None
        now = _now()
        self._snapshot(record, action="superseded", reason=reason, now=now)
        updated = record.model_copy(
            update={
                "claim_state": "superseded",
                "superseded_state": "superseded",
                "superseded_by": replacement_id,
                "temporal_state": "superseded",
                "stale": "superseded",
                "updated_at": now,
            }
        )
        self._upsert_record(updated)
        self._index_chunk(updated, now, stale=True)
        self._emit_activity(
            ActivityEvent(
                event_id=_id("activity", memory_id, "superseded"),
                kind="memory_superseded",
                summary="Memory superseded: %s" % record.title,
                scope=updated.primary_scope,
                references=(memory_id, replacement_id),
                created_at=now,
            )
        )
        return updated

    def request_delete(self, memory_id: str, *, reason: str) -> Optional[MemoryRecord]:
        record = self.get(memory_id)
        if record is None:
            return None
        now = _now()
        self._snapshot(record, action="deleted", reason=reason, now=now)
        updated = record.model_copy(
            update={"deletion_state": "deletion_requested", "updated_at": now}
        )
        self._upsert_record(updated)
        return updated

    def delete(self, memory_id: str, *, reason: str = "explicit deletion") -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        now = _now()
        self._snapshot(record, action="deleted", reason=reason, now=now)
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE memory_records SET deletion_state = 'deleted', deleted_at = ?, updated_at = ? WHERE memory_id = ?",
                (now, now, memory_id),
            )
            connection.execute(
                "UPDATE memory_semantic_chunks SET deletion_state = 'deleted' WHERE memory_id = ?",
                (memory_id,),
            )
        self._emit_activity(
            ActivityEvent(
                event_id=_id("activity", memory_id, "deleted"),
                kind="memory_deleted",
                summary="Memory deleted: %s" % record.title,
                scope=record.primary_scope,
                references=(memory_id,),
                created_at=now,
            )
        )
        return True

    def expire_due(self, now: Optional[str] = None) -> int:
        now = now or _now()
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT memory_id FROM memory_records WHERE deletion_state = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE memory_records SET temporal_state = 'expired', stale = 'invalid', claim_state = 'expired', updated_at = ? WHERE memory_id = ?",
                    (now, row["memory_id"]),
                )
                connection.execute(
                    "UPDATE memory_semantic_chunks SET stale = 1 WHERE memory_id = ?",
                    (row["memory_id"],),
                )
        return len(rows)

    def count_due(self, now: Optional[str] = None) -> int:
        """Read-only count of memory records whose retention has expired.

        Does not mutate state; used by the Self-Maintenance platform to detect
        a real hygiene improvement without applying it.
        """
        now = now or _now()
        with self._connection_factory() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE deletion_state = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).fetchone()[0]

    # ---- evidence / entities / relationships ----

    def add_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_evidence (
                    evidence_id, source_type, source_reference, source_version, location,
                    timestamp, content_hash, excerpt, privacy_classification, trust_level,
                    availability, stale, redaction_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id, evidence.source_type, evidence.source_reference,
                    evidence.source_version, evidence.location, evidence.timestamp,
                    evidence.content_hash, evidence.excerpt, evidence.privacy_classification,
                    evidence.trust_level, evidence.availability, int(evidence.stale),
                    evidence.redaction_state, evidence.created_at,
                ),
            )
        return evidence

    def register_entity(self, record: EntityRecord) -> EntityRecord:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_entities (
                    entity_id, entity_type, canonical_name, aliases, scope, source_kind,
                    source, source_detail, extraction_method, extractor_version, author,
                    learned_at, confidence, attributes, valid_from, valid_to,
                    privacy_classification, merge_state, merged_into, version, stale,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.entity_id, record.entity_type, record.canonical_name,
                    "|".join(record.aliases), record.scope, record.source.kind,
                    record.source.source, record.source.detail, record.source.method,
                    record.source.extractor_version, record.source.author,
                    record.source.learned_at, record.confidence, "|".join(record.attributes),
                    record.valid_from, record.valid_to, record.privacy_classification,
                    record.merge_state, record.merged_into, record.version,
                    int(record.stale), record.created_at, record.updated_at,
                ),
            )
        return record

    def entities(self, *, scope: Optional[str] = None, limit: int = 200) -> Tuple[EntityRecord, ...]:
        with self._connection_factory() as connection:
            if scope:
                rows = connection.execute(
                    "SELECT * FROM memory_entities WHERE scope = ? AND stale = 0 ORDER BY canonical_name LIMIT ?",
                    (scope, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memory_entities WHERE stale = 0 ORDER BY canonical_name LIMIT ?",
                    (limit,),
                ).fetchall()
        return tuple(_entity_from_row(row) for row in rows)

    def register_relationship(self, record: RelationshipRecord) -> RelationshipRecord:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_relationships (
                    relationship_id, source_entity_id, relationship_type, target_entity_id,
                    scope, valid_from, valid_to, evidence, confidence, authority,
                    source_kind, source, source_detail, extraction_method, extractor_version,
                    author, learned_at, review_state, stale, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.relationship_id, record.source_entity_id, record.relationship_type,
                    record.target_entity_id, record.scope, record.valid_from, record.valid_to,
                    "|".join(record.evidence), record.confidence, record.authority,
                    record.source.kind, record.source.source, record.source.detail,
                    record.source.method, record.source.extractor_version, record.source.author,
                    record.learned_at, record.review_state, int(record.stale), record.version,
                    record.created_at, record.updated_at,
                ),
            )
        return record

    # ---- retrieval ----

    def search(self, query: str, *, scope: Optional[str] = None, limit: int = 16) -> RetrievalEnvelope:
        started = time.monotonic()
        tokens = _tokens(query)
        if not tokens:
            return RetrievalEnvelope(query=query, scope=scope or "user", results=(), seconds=0.0)
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, memory_type, title, content, primary_scope, source_kind, source,
                       confidence, authority, valid_from, valid_to, stale, temporal_state,
                       privacy_classification, content_hash, claim_state, evidence_ids, updated_at
                FROM memory_records
                WHERE deletion_state = 'active' AND claim_state != 'rejected'
                """,
            ).fetchall()
        candidates: List[Tuple[float, dict]] = []
        query_set = set(tokens)
        for row in rows:
            doc_tokens = _tokens((row["title"] or "") + " " + (row["content"] or ""))
            if not doc_tokens:
                continue
            overlap = len(query_set.intersection(doc_tokens))
            if overlap == 0:
                continue
            score = overlap / max(1, min(len(query_set), len(set(doc_tokens)))) * 0.7
            if query.lower() in (row["title"] or "").lower():
                score += 0.25
            score = min(1.0, score)
            if row["confidence"] == "confirmed" or row["confidence"] == "high_confidence":
                score += 0.05
            if row["stale"] == "fresh" or row["temporal_state"] == "currently_valid":
                score += 0.02
            if scope and row["primary_scope"] == scope:
                score += 0.03
            candidates.append((score, dict(row)))
        candidates.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, row in candidates[:limit]:
            results.append(
                RetrievalResult(
                    result_id=_id("result", row["memory_id"]),
                    memory_id=row["memory_id"],
                    score=round(min(1.0, score), 4),
                    reason="token overlap match on query terms",
                    ranking_factors=("token-overlap", "authority", "freshness"),
                    memory_type=row["memory_type"],
                    scope=row["primary_scope"],
                    authority=row["authority"],
                    confidence=row["confidence"],
                    freshness=row["stale"],
                    temporal_state=row["temporal_state"],
                    privacy_classification=row["privacy_classification"],
                    evidence=tuple(x for x in row["evidence_ids"].split("|") if x),
                    disputed=row["claim_state"] == "disputed",
                    inferred=row["authority"] in {"inference", "hypothesis"},
                    excerpt=(row["content"] or "")[:1200],
                )
            )
        return RetrievalEnvelope(
            query=query,
            scope=scope or "user",
            results=tuple(results),
            truncated=len(candidates) > limit,
            semantic=True,
            seconds=round(time.monotonic() - started, 4),
            generated_at=_now(),
        )

    # ---- review queue ----

    def review_queue(self, *, state: str = "open", limit: int = 50) -> ReviewEnvelope:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_review_queue WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        items = tuple(_review_from_row(row) for row in rows)
        return ReviewEnvelope(items=items, truncated=len(rows) > limit, generated_at=_now())

    def review_action(self, review_id: str, action: str, *, note: str = "") -> bool:
        if action not in {"accept", "reject", "defer"}:
            return False
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE memory_review_queue SET state = ?, updated_at = ? WHERE review_id = ? AND state = 'open'",
                (action if action != "accept" else "accepted", _now(), review_id),
            )
        return cursor.rowcount > 0

    # ---- health ----

    def health(self) -> MemoryHealth:
        with self._connection_factory() as connection:
            counts = {
                "accepted": connection.execute("SELECT COUNT(*) FROM memory_records WHERE claim_state = 'accepted'").fetchone()[0],
                "proposed": connection.execute("SELECT COUNT(*) FROM memory_records WHERE claim_state = 'proposed'").fetchone()[0],
                "disputed": connection.execute("SELECT COUNT(*) FROM memory_records WHERE claim_state = 'disputed'").fetchone()[0],
                "stale": connection.execute("SELECT COUNT(*) FROM memory_records WHERE stale != 'fresh' AND deletion_state = 'active'").fetchone()[0],
                "expired": connection.execute("SELECT COUNT(*) FROM memory_records WHERE temporal_state = 'expired'").fetchone()[0],
                "deletion_backlog": connection.execute("SELECT COUNT(*) FROM memory_records WHERE deletion_state = 'deletion_requested'").fetchone()[0],
                "embedding_backlog": connection.execute("SELECT COUNT(*) FROM memory_records WHERE embedding_state = 'pending'").fetchone()[0],
                "unresolved_conflicts": connection.execute("SELECT COUNT(*) FROM memory_conflicts WHERE state = 'open'").fetchone()[0],
                "unavailable_evidence": connection.execute("SELECT COUNT(*) FROM memory_evidence WHERE availability != 'available'").fetchone()[0],
            }
        diagnostics = MemoryHealthDiagnostics(
            accepted=counts["accepted"],
            proposed=counts["proposed"],
            disputed=counts["disputed"],
            stale=counts["stale"],
            expired=counts["expired"],
            deletion_backlog=counts["deletion_backlog"],
            embedding_backlog=counts["embedding_backlog"],
            unresolved_conflicts=counts["unresolved_conflicts"],
            unavailable_evidence=counts["unavailable_evidence"],
            storage_version=1,
            storage_size_bytes=self._storage.size_bytes(),
        )
        if counts["unresolved_conflicts"] > 0 or counts["deletion_backlog"] > 0:
            state = "degraded"
            message = "Open conflicts or deletion backlog present."
        elif counts["stale"] > 0 or counts["expired"] > 0:
            state = "partially_available"
            message = "Stale or expired memories require attention."
        else:
            state = "healthy"
            message = "Memory platform healthy."
        return MemoryHealth(
            state=state,
            message=message,
            diagnostics=diagnostics,
            updated_at=_now(),
        )

    def overview(self) -> MemoryOverview:
        health = self.health()
        with self._connection_factory() as connection:
            awaiting = connection.execute(
                "SELECT COUNT(*) FROM memory_review_queue WHERE state = 'open'"
            ).fetchone()[0]
            conflicts = connection.execute(
                "SELECT COUNT(*) FROM memory_conflicts WHERE state = 'open'"
            ).fetchone()[0]
            projects = connection.execute(
                "SELECT DISTINCT value FROM memory_meta WHERE key = 'projects_with_memory'"
            ).fetchall()
        return MemoryOverview(
            health=health,
            recent=self.list(limit=8),
            awaiting_review=awaiting,
            open_conflicts=conflicts,
            stale_memories=health.diagnostics.stale,
            expiring_soon=self._expiring_soon(),
            projects_with_memory=tuple(p["value"] for p in projects),
            semantic_available=True,
            generated_at=_now(),
        )

    def _expiring_soon(self) -> int:
        soon = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        with self._connection_factory() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE deletion_state = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (soon,),
            ).fetchone()[0]

    def import_records(self, records: Tuple[MemoryRecord, ...]) -> ImportResult:
        imported = 0
        skipped = 0
        errors = []
        for record in records:
            existing = self.get(record.memory_id)
            if existing is not None:
                skipped += 1
                continue
            try:
                self.propose(record)
                imported += 1
            except Exception as exc:
                errors.append("%s: %s" % (record.memory_id, type(exc).__name__))
        return ImportResult(imported=imported, skipped=skipped, errors=tuple(errors[:8]), summary="Imported %d records." % imported)

    def backup(self) -> Optional[str]:
        return self._storage.backup_to(str(self._storage._data_dir / "backups"))

    def storage_stats(self) -> dict:
        return {"path": self._storage.path(), "size_bytes": self._storage.size_bytes(), "version": 1}

    # ---- internals ----

    def _upsert_record(self, record: MemoryRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_records (
                    memory_id, memory_type, subtype, title, content, structured_content,
                    primary_scope, related_scopes, subject_entity, predicate, object_entity,
                    valid_from, valid_to, learned_at, confirmed_at, last_reviewed_at,
                    expires_at, source_kind, source, source_detail, extraction_method,
                    extractor_version, author, evidence_ids, provenance_chain, confidence,
                    confidence_explanation, authority, claim_state, review_state,
                    conflict_state, superseded_state, superseded_by, supersedes,
                    privacy_classification, sensitivity_labels, retention_mode,
                    retrieval_tags, embedding_state, embedding_model, embedding_dimension,
                    content_hash, version, stale, temporal_state, related_memories,
                    related_decisions, related_tasks, related_projects, related_documents,
                    deletion_state, deleted_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id, record.memory_type, record.subtype, record.title,
                    record.content, record.structured_content, record.primary_scope,
                    "|".join(record.related_scopes), record.subject_entity, record.predicate,
                    record.object_entity, record.valid_from, record.valid_to,
                    record.learned_at, record.confirmed_at, record.last_reviewed_at,
                    record.expires_at, record.source.kind, record.source.source,
                    record.source.detail, record.source.method, record.source.extractor_version,
                    record.source.author, "|".join(record.evidence_ids),
                    "|".join(record.provenance_chain), record.confidence,
                    record.confidence_explanation, record.authority, record.claim_state,
                    record.review_state, record.conflict_state, record.superseded_state,
                    record.superseded_by, record.supersedes, record.privacy_classification,
                    "|".join(record.sensitivity_labels), record.retention_mode,
                    "|".join(record.retrieval_tags), record.embedding_state,
                    record.embedding_model, record.embedding_dimension, record.content_hash,
                    record.version, record.stale, record.temporal_state,
                    "|".join(record.related_memories), "|".join(record.related_decisions),
                    "|".join(record.related_tasks), "|".join(record.related_projects),
                    "|".join(record.related_documents), record.deletion_state,
                    record.deleted_at, record.created_at, record.updated_at,
                ),
            )

    def _index_chunk(self, record: MemoryRecord, now: str, *, stale: bool = False) -> None:
        tokens = _tokens(record.title + " " + record.content)
        chunk_id = _id("chunk", record.memory_id, record.version)
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_semantic_chunks (
                    chunk_id, memory_id, scope, content_version, source, source_kind,
                    source_detail, extraction_method, extractor_version, author, learned_at,
                    privacy_classification, confidence, authority, valid_from, valid_to,
                    embedding_model, embedding_dimension, embedding_version, chunking_version,
                    stale, deletion_state, tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id, record.memory_id, record.primary_scope, record.version,
                    record.source.source, record.source.kind, record.source.detail,
                    record.source.method, record.source.extractor_version, record.source.author,
                    record.learned_at, record.privacy_classification, record.confidence,
                    record.authority, record.valid_from, record.valid_to, "token-overlap/1.0",
                    len(set(tokens)), "1.0.0", "1.0.0", int(stale), record.deletion_state,
                    " ".join(tokens), now,
                ),
            )

    def _snapshot(self, record: MemoryRecord, *, action: str, reason: str, now: str, changed_by: str = "") -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO memory_versions (
                    memory_id, version, action, changed_by, reason, snapshot_hash,
                    content_snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id, record.version + 1, action, changed_by, reason,
                    record.content_hash, record.content[:8000], now,
                ),
            )

    def _emit_activity(self, event: ActivityEvent) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_activity (
                    event_id, kind, summary, scope, scope_value, refs, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id, event.kind, event.summary, event.scope,
                    event.scope_value, "|".join(event.references), event.created_at,
                ),
            )


def _memory_from_row(row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row["memory_id"],
        memory_type=row["memory_type"],
        subtype=row["subtype"],
        title=row["title"],
        content=row["content"],
        structured_content=row["structured_content"],
        primary_scope=row["primary_scope"],
        related_scopes=tuple(x for x in row["related_scopes"].split("|") if x),
        subject_entity=row["subject_entity"],
        predicate=row["predicate"],
        object_entity=row["object_entity"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        learned_at=row["learned_at"],
        confirmed_at=row["confirmed_at"],
        last_reviewed_at=row["last_reviewed_at"],
        expires_at=row["expires_at"],
        source=Provenance(
            kind=row["source_kind"],
            source=row["source"],
            detail=row["source_detail"],
            method=row["extraction_method"],
            extractor_version=row["extractor_version"],
            learned_at=row["learned_at"],
            author=row["author"],
        ),
        evidence_ids=tuple(x for x in row["evidence_ids"].split("|") if x),
        provenance_chain=tuple(x for x in row["provenance_chain"].split("|") if x),
        confidence=row["confidence"],
        confidence_explanation=row["confidence_explanation"],
        authority=row["authority"],
        claim_state=row["claim_state"],
        review_state=row["review_state"],
        conflict_state=row["conflict_state"],
        superseded_state=row["superseded_state"],
        superseded_by=row["superseded_by"],
        supersedes=row["supersedes"],
        privacy_classification=row["privacy_classification"],
        sensitivity_labels=tuple(x for x in row["sensitivity_labels"].split("|") if x),
        retention_mode=row["retention_mode"],
        retrieval_tags=tuple(x for x in row["retrieval_tags"].split("|") if x),
        embedding_state=row["embedding_state"],
        embedding_model=row["embedding_model"],
        embedding_dimension=row["embedding_dimension"],
        content_hash=row["content_hash"],
        version=row["version"],
        stale=row["stale"],
        temporal_state=row["temporal_state"],
        related_memories=tuple(x for x in row["related_memories"].split("|") if x),
        related_decisions=tuple(x for x in row["related_decisions"].split("|") if x),
        related_tasks=tuple(x for x in row["related_tasks"].split("|") if x),
        related_projects=tuple(x for x in row["related_projects"].split("|") if x),
        related_documents=tuple(x for x in row["related_documents"].split("|") if x),
        deletion_state=row["deletion_state"],
        deleted_at=row["deleted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _entity_from_row(row) -> EntityRecord:
    return EntityRecord(
        entity_id=row["entity_id"],
        entity_type=row["entity_type"],
        canonical_name=row["canonical_name"],
        aliases=tuple(x for x in row["aliases"].split("|") if x),
        scope=row["scope"],
        source=Provenance(
            kind=row["source_kind"],
            source=row["source"],
            detail=row["source_detail"],
            method=row["extraction_method"],
            extractor_version=row["extractor_version"],
            learned_at=row["learned_at"],
            author=row["author"],
        ),
        confidence=row["confidence"],
        attributes=tuple(x for x in row["attributes"].split("|") if x),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        privacy_classification=row["privacy_classification"],
        merge_state=row["merge_state"],
        merged_into=row["merged_into"],
        version=row["version"],
        stale=bool(row["stale"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _review_from_row(row) -> ReviewItem:
    return ReviewItem(
        review_id=row["review_id"],
        kind=row["kind"],
        reason=row["reason"],
        affected_ids=tuple(x for x in row["affected_ids"].split("|") if x),
        source=Provenance(
            kind=row["source_kind"],
            source=row["source"],
            detail=row["source_detail"],
            method=row["extraction_method"],
            extractor_version=row["extractor_version"],
            learned_at=row["learned_at"],
            author=row["author"],
        ),
        authority=row["authority"],
        confidence=row["confidence"],
        privacy_classification=row["privacy_classification"],
        temporal_state=row["temporal_state"],
        proposed_action=row["proposed_action"],
        state=row["state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
