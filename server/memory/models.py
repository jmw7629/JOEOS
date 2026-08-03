"""Memory and Knowledge Platform contracts.

Every memory record carries provenance, authority, confidence, temporal
validity, scope, privacy, and retention metadata. AI-extracted material is
proposed until policy or user review promotes it to accepted. Nothing is
fabricated, and hidden reasoning is never stored.
"""

from __future__ import annotations

from typing import Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

# ---- Enumerated taxonomies -------------------------------------------------

MemoryType = Literal[
    "working", "session", "episodic", "semantic", "procedural", "preference",
    "project", "repository", "task", "mission", "agent", "workspace",
    "document",
]
MemorySubtype = Literal[
    "instruction", "objective", "plan", "tool_result", "open_question",
    "calculation", "execution_state", "event", "outcome", "fact", "concept",
    "definition", "term", "procedure", "checklist", "preference", "setting",
    "goal", "milestone", "limitation", "glossary", "failure", "lesson",
    "observation", "summary", "note", "decision_ref", "convention_ref",
    "entity_ref", "relationship_ref", "document_ref", "unknown",
]
MemoryScope = Literal[
    "request", "conversation", "session", "user", "workspace", "project",
    "repository", "branch", "mission", "task", "agent", "organization",
    "device", "runtime",
]
AuthorityLevel = Literal[
    "system_policy", "explicit_user_instruction", "approved_project_policy",
    "accepted_architectural_decision", "verified_repository_fact",
    "trusted_document_fact", "user_provided_claim", "agent_observation",
    "model_extracted_claim", "inference", "hypothesis", "disputed",
    "deprecated", "unknown",
]
ConfidenceClass = Literal[
    "confirmed", "high_confidence", "moderate_confidence", "low_confidence",
    "uncertain", "disputed", "unknown",
]
ClaimState = Literal[
    "proposed", "accepted", "disputed", "rejected", "superseded", "expired",
    "unverified",
]
ReviewState = Literal[
    "proposed", "pending_review", "accepted", "rejected", "needs_revision",
]
ConflictState = Literal["none", "open", "resolved", "accepted_override", "disputed"]
SupersededState = Literal["none", "superseded", "deprecated", "superseding"]
DeletionState = Literal[
    "active", "deletion_requested", "deleting", "deleted", "partially_deleted",
    "blocked_by_retention", "failed", "externally_retained",
]
TemporalState = Literal[
    "currently_valid", "historically_valid", "future_planned", "expired",
    "superseded", "time_uncertain", "ongoing", "point_in_time",
]
PrivacyClassification = Literal[
    "public", "internal", "private", "confidential_project", "restricted",
    "secret_bearing", "personal", "local_only", "device_only",
    "task_restricted", "agent_restricted",
]
RetentionMode = Literal[
    "request_only", "session_only", "task_duration", "mission_duration",
    "fixed_duration", "until_project_completion", "until_superseded",
    "indefinite", "governed_by_source", "user_defined",
]
StaleState = Literal[
    "fresh", "possibly_stale", "stale", "invalid", "needs_review",
    "superseded", "source_unavailable",
]
EvidenceAvailability = Literal[
    "available", "unavailable", "source_deleted", "redacted", "not_applicable",
]
EvidenceSourceType = Literal[
    "user_statement", "source_file", "code_symbol", "git_commit", "git_diff",
    "project_document", "uploaded_document", "task_result", "tool_result",
    "test_result", "build_result", "system_event", "accepted_decision",
    "approved_note", "external_source", "model_extraction",
    "repository_intelligence", "conversation_summary",
]
ExtractionMethod = Literal[
    "explicit_user", "rule_based", "model_extraction", "repository_parsing",
    "document_parsing", "task_derived", "system_event", "import",
]
EntityType = Literal[
    "user", "person", "team", "organization", "project", "repository",
    "workspace", "branch", "commit", "file", "symbol", "service", "route",
    "api", "database", "schema", "task", "mission", "agent", "model",
    "runtime", "device", "document", "conversation", "note", "decision",
    "convention", "event", "command", "process", "artifact", "location",
    "concept", "term",
]
RelationshipType = Literal[
    "owns", "manages", "uses", "depends_on", "implements", "belongs_to",
    "created", "modified", "supersedes", "conflicts_with", "supports",
    "blocked_by", "assigned_to", "related_to", "documented_by", "tested_by",
    "deployed_by", "governed_by", "prefers", "located_in", "produced",
    "consumed", "reviewed_by", "approved_by",
]
EntityMatchState = Literal[
    "exact", "likely", "possible", "conflict", "duplicate", "user_confirmed_merge",
    "split", "unresolved",
]
ReviewAction = Literal[
    "accept", "reject", "edit", "merge", "keep_separate", "mark_disputed",
    "mark_verified", "change_scope", "change_retention", "change_privacy",
    "open_evidence", "delete", "defer",
]
HealthState = Literal[
    "healthy", "indexing", "consolidating", "degraded", "stale",
    "partially_available", "storage_unavailable", "embedding_unavailable",
    "migration_required", "corrupted", "privacy_policy_blocked", "unknown",
]

# ---- Base -------------------------------------------------------------

AUTHORITY_RANK: Tuple[AuthorityLevel, ...] = (
    "system_policy", "explicit_user_instruction", "approved_project_policy",
    "accepted_architectural_decision", "verified_repository_fact",
    "trusted_document_fact", "user_provided_claim", "agent_observation",
    "model_extracted_claim", "inference", "hypothesis", "disputed",
    "deprecated", "unknown",
)

CONFIDENCE_RANK: Tuple[ConfidenceClass, ...] = (
    "confirmed", "high_confidence", "moderate_confidence", "low_confidence",
    "uncertain", "disputed", "unknown",
)


def authority_rank(authority: str) -> int:
    try:
        return AUTHORITY_RANK.index(authority)
    except ValueError:
        return AUTHORITY_RANK.index("unknown")


def confidence_rank(confidence: str) -> int:
    try:
        return CONFIDENCE_RANK.index(confidence)
    except ValueError:
        return CONFIDENCE_RANK.index("unknown")


class StrictMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---- Provenance and evidence -------------------------------------------

class Provenance(StrictMemoryModel):
    kind: EvidenceSourceType
    source: str = Field(min_length=1, max_length=240)
    detail: str = ""
    method: ExtractionMethod = "explicit_user"
    extractor_version: str = ""
    learned_at: str
    author: str = ""


class EvidenceRecord(StrictMemoryModel):
    evidence_id: str = Field(min_length=8, max_length=80)
    source_type: EvidenceSourceType
    source_reference: str = Field(min_length=1, max_length=1024)
    source_version: str = ""
    location: str = ""
    timestamp: Optional[str] = None
    content_hash: str = Field(min_length=64, max_length=64)
    excerpt: str = Field(default="", max_length=1200)
    privacy_classification: PrivacyClassification = "internal"
    trust_level: str = "unknown"
    availability: EvidenceAvailability = "available"
    stale: bool = False
    redaction_state: str = "none"
    created_at: str


# ---- Memory record -------------------------------------------------------

class MemoryRecord(StrictMemoryModel):
    schema_version: Literal[1] = 1
    memory_id: str = Field(min_length=8, max_length=80)
    memory_type: MemoryType
    subtype: MemorySubtype = "unknown"
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=8000)
    structured_content: str = ""
    primary_scope: MemoryScope
    related_scopes: Tuple[MemoryScope, ...] = Field(default=(), max_length=16)
    subject_entity: Optional[str] = None
    predicate: str = ""
    object_entity: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    learned_at: str
    confirmed_at: Optional[str] = None
    last_reviewed_at: Optional[str] = None
    expires_at: Optional[str] = None
    source: Provenance
    evidence_ids: Tuple[str, ...] = Field(default=(), max_length=32)
    provenance_chain: Tuple[str, ...] = Field(default=(), max_length=32)
    confidence: ConfidenceClass = "uncertain"
    confidence_explanation: str = ""
    authority: AuthorityLevel = "unknown"
    claim_state: ClaimState = "proposed"
    review_state: ReviewState = "proposed"
    conflict_state: ConflictState = "none"
    superseded_state: SupersededState = "none"
    superseded_by: Optional[str] = None
    supersedes: Optional[str] = None
    privacy_classification: PrivacyClassification = "private"
    sensitivity_labels: Tuple[str, ...] = Field(default=(), max_length=16)
    retention_mode: RetentionMode = "indefinite"
    retrieval_tags: Tuple[str, ...] = Field(default=(), max_length=32)
    embedding_state: Literal["not_embedded", "pending", "embedded", "stale"] = "not_embedded"
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    content_hash: str = Field(min_length=64, max_length=64)
    version: int = Field(ge=1, le=1024)
    stale: StaleState = "fresh"
    temporal_state: TemporalState = "currently_valid"
    related_memories: Tuple[str, ...] = Field(default=(), max_length=32)
    related_decisions: Tuple[str, ...] = Field(default=(), max_length=32)
    related_tasks: Tuple[str, ...] = Field(default=(), max_length=32)
    related_projects: Tuple[str, ...] = Field(default=(), max_length=32)
    related_documents: Tuple[str, ...] = Field(default=(), max_length=32)
    deletion_state: DeletionState = "active"
    deleted_at: Optional[str] = None
    created_at: str
    updated_at: str


class MemoryVersion(StrictMemoryModel):
    memory_id: str = Field(min_length=8, max_length=80)
    version: int = Field(ge=1)
    action: Literal["created", "corrected", "verified", "disputed", "superseded", "expired", "deleted", "restored"] = "created"
    changed_by: str = ""
    reason: str = ""
    snapshot_hash: str = Field(min_length=64, max_length=64)
    content_snapshot: str = Field(default="", max_length=8000)
    created_at: str


# ---- Entities and relationships ------------------------------------------

class EntityRecord(StrictMemoryModel):
    schema_version: Literal[1] = 1
    entity_id: str = Field(min_length=8, max_length=80)
    entity_type: EntityType
    canonical_name: str = Field(min_length=1, max_length=240)
    aliases: Tuple[str, ...] = Field(default=(), max_length=32)
    scope: MemoryScope
    source: Provenance
    confidence: ConfidenceClass = "uncertain"
    attributes: Tuple[str, ...] = Field(default=(), max_length=64)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    privacy_classification: PrivacyClassification = "private"
    merge_state: EntityMatchState = "unresolved"
    merged_into: Optional[str] = None
    version: int = Field(ge=1)
    stale: bool = False
    created_at: str
    updated_at: str


class RelationshipRecord(StrictMemoryModel):
    schema_version: Literal[1] = 1
    relationship_id: str = Field(min_length=8, max_length=80)
    source_entity_id: str = Field(min_length=8, max_length=80)
    relationship_type: RelationshipType
    target_entity_id: str = Field(min_length=8, max_length=80)
    scope: MemoryScope
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence: Tuple[str, ...] = Field(default=(), max_length=32)
    confidence: ConfidenceClass = "uncertain"
    authority: AuthorityLevel = "unknown"
    source: Provenance
    review_state: ReviewState = "proposed"
    stale: bool = False
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class KnowledgeGraph(StrictMemoryModel):
    schema_version: Literal[1] = 1
    scope: MemoryScope
    scope_value: str = ""
    entities: Tuple[EntityRecord, ...] = Field(default=(), max_length=2048)
    relationships: Tuple[RelationshipRecord, ...] = Field(default=(), max_length=4096)
    truncated: bool = False
    generated_at: str


# ---- Duplicates and conflicts --------------------------------------------

class DuplicateCandidate(StrictMemoryModel):
    duplicate_id: str = Field(min_length=8, max_length=80)
    memory_id_a: str = Field(min_length=8, max_length=80)
    memory_id_b: str = Field(min_length=8, max_length=80)
    state: Literal["exact_duplicate", "likely_duplicate", "related_but_distinct", "conflicting", "uncertain"]
    similarity: float = Field(ge=0.0, le=1.0)
    evidence: Tuple[str, ...] = Field(default=(), max_length=16)
    reviewed: bool = False
    created_at: str


class ConflictRecord(StrictMemoryModel):
    conflict_id: str = Field(min_length=8, max_length=80)
    memory_id_a: str = Field(min_length=8, max_length=80)
    memory_id_b: str = Field(min_length=8, max_length=80)
    conflict_type: str = Field(min_length=1, max_length=120)
    scope: MemoryScope
    evidence: Tuple[str, ...] = Field(default=(), max_length=32)
    authority_a: AuthorityLevel = "unknown"
    authority_b: AuthorityLevel = "unknown"
    confidence_a: ConfidenceClass = "unknown"
    confidence_b: ConfidenceClass = "unknown"
    temporal_a: Optional[str] = None
    temporal_b: Optional[str] = None
    recommended_resolution: str = ""
    review_required: bool = True
    state: Literal["open", "resolved", "accepted_override", "superseded"] = "open"
    resolved_action: str = ""
    created_at: str
    updated_at: str


class ReviewItem(StrictMemoryModel):
    review_id: str = Field(min_length=8, max_length=80)
    kind: Literal[
        "memory_proposal", "sensitive_proposal", "duplicate_candidate",
        "conflict", "stale_memory", "expired_memory", "unsupported_claim",
        "entity_merge", "entity_split", "correction", "deletion_failure",
        "evidence_unavailable", "consolidation_proposal",
    ]
    reason: str = Field(min_length=1, max_length=240)
    affected_ids: Tuple[str, ...] = Field(default=(), max_length=16)
    source: Provenance
    authority: AuthorityLevel = "unknown"
    confidence: ConfidenceClass = "uncertain"
    privacy_classification: PrivacyClassification = "private"
    temporal_state: TemporalState = "currently_valid"
    proposed_action: ReviewAction = "accept"
    state: Literal["open", "accepted", "rejected", "deferred"] = "open"
    created_at: str
    updated_at: str


# ---- Documents and notes --------------------------------------------------

class DocumentRecord(StrictMemoryModel):
    schema_version: Literal[1] = 1
    document_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    source: str = Field(min_length=1, max_length=1024)
    format: str = ""
    version: str = ""
    author: str = ""
    created_date: Optional[str] = None
    modified_date: Optional[str] = None
    import_date: str
    privacy_classification: PrivacyClassification = "private"
    content_hash: str = Field(min_length=64, max_length=64)
    parser: str = ""
    parser_version: str = ""
    extraction_state: Literal["none", "pending", "extracting", "complete", "failed"] = "none"
    chunking_state: Literal["none", "pending", "complete", "failed"] = "none"
    indexing_state: Literal["none", "pending", "complete", "failed"] = "none"
    error_state: str = ""
    related_project: Optional[str] = None
    deletion_state: DeletionState = "active"
    created_at: str
    updated_at: str


class NoteRecord(StrictMemoryModel):
    schema_version: Literal[1] = 1
    note_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=24000)
    tags: Tuple[str, ...] = Field(default=(), max_length=32)
    projects: Tuple[str, ...] = Field(default=(), max_length=32)
    entities: Tuple[str, ...] = Field(default=(), max_length=32)
    privacy_classification: PrivacyClassification = "private"
    retention_mode: RetentionMode = "indefinite"
    version: int = Field(ge=1)
    deletion_state: DeletionState = "active"
    created_at: str
    updated_at: str


# ---- Semantic index -------------------------------------------------------

class SemanticChunk(StrictMemoryModel):
    chunk_id: str = Field(min_length=8, max_length=80)
    memory_id: str = Field(min_length=8, max_length=80)
    scope: MemoryScope
    content_version: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=240)
    provenance: Provenance
    privacy_classification: PrivacyClassification = "private"
    confidence: ConfidenceClass = "uncertain"
    authority: AuthorityLevel = "unknown"
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    embedding_model: str = "token-overlap/1.0"
    embedding_dimension: int = Field(ge=0, le=4096)
    embedding_version: str = "1.0.0"
    chunking_version: str = "1.0.0"
    stale: bool = False
    deletion_state: DeletionState = "active"
    tokens: Tuple[str, ...] = Field(default=(), max_length=512)
    created_at: str


# ---- Retrieval ------------------------------------------------------------

class RetrievalResult(StrictMemoryModel):
    result_id: str = Field(min_length=8, max_length=80)
    memory_id: str = Field(min_length=8, max_length=80)
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=240)
    ranking_factors: Tuple[str, ...] = Field(default=(), max_length=16)
    memory_type: MemoryType = "semantic"
    scope: MemoryScope
    authority: AuthorityLevel = "unknown"
    confidence: ConfidenceClass = "uncertain"
    freshness: StaleState = "fresh"
    temporal_state: TemporalState = "currently_valid"
    privacy_classification: PrivacyClassification = "private"
    evidence: Tuple[str, ...] = Field(default=(), max_length=16)
    disputed: bool = False
    inferred: bool = False
    excerpt: str = Field(default="", max_length=1200)


class RetrievalEnvelope(StrictMemoryModel):
    schema_version: Literal[1] = 1
    query: str
    scope: MemoryScope
    scope_value: str = ""
    results: Tuple[RetrievalResult, ...] = Field(default=(), max_length=128)
    truncated: bool = False
    semantic: bool = False
    seconds: float = Field(ge=0.0)
    generated_at: str


class ContextPackItem(StrictMemoryModel):
    item_id: str = Field(min_length=8, max_length=80)
    memory_id: str = Field(min_length=8, max_length=80)
    reason: str = Field(min_length=1, max_length=240)
    scope: MemoryScope
    authority: AuthorityLevel = "unknown"
    confidence: ConfidenceClass = "uncertain"
    freshness: StaleState = "fresh"
    privacy_classification: PrivacyClassification = "private"
    token_estimate: int = Field(ge=0, le=65536)
    evidence: Tuple[str, ...] = Field(default=(), max_length=16)
    excerpt: str = Field(default="", max_length=1200)


class ContextPack(StrictMemoryModel):
    schema_version: Literal[1] = 1
    pack_id: str = Field(min_length=8, max_length=80)
    objective: str = Field(min_length=1, max_length=500)
    scope: MemoryScope
    scope_value: str = ""
    items: Tuple[ContextPackItem, ...] = Field(default=(), max_length=256)
    excluded_summary: str = ""
    precedence: Tuple[str, ...] = Field(default=(), max_length=16)
    generated_at: str


# ---- Retention, health, telemetry, activity ------------------------------

class RetentionPolicy(StrictMemoryModel):
    scope: MemoryScope
    scope_value: str = ""
    mode: RetentionMode = "indefinite"
    fixed_duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    sensitive_override_days: Optional[int] = Field(default=None, ge=1, le=3650)


class MemoryHealthDiagnostics(StrictMemoryModel):
    accepted: int = Field(ge=0)
    proposed: int = Field(ge=0)
    disputed: int = Field(ge=0)
    stale: int = Field(ge=0)
    expired: int = Field(ge=0)
    deletion_backlog: int = Field(ge=0)
    embedding_backlog: int = Field(ge=0)
    unresolved_conflicts: int = Field(ge=0)
    unavailable_evidence: int = Field(ge=0)
    storage_version: int = Field(ge=0)
    storage_size_bytes: int = Field(ge=0)
    last_backup: Optional[str] = None
    last_consolidation: Optional[str] = None
    recent_failures: Tuple[str, ...] = Field(default=(), max_length=32)


class MemoryHealth(StrictMemoryModel):
    schema_version: Literal[1] = 1
    state: HealthState = "unknown"
    message: str = ""
    diagnostics: MemoryHealthDiagnostics
    updated_at: str


class TelemetrySample(StrictMemoryModel):
    proposals: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    corrections: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    stale: int = Field(ge=0)
    expired: int = Field(ge=0)
    deletion_requests: int = Field(ge=0)
    deletion_failures: int = Field(ge=0)
    retrievals: int = Field(ge=0)
    retrieval_seconds: float = Field(ge=0.0)
    context_pack_items: int = Field(ge=0)
    semantic_chunks: int = Field(ge=0)
    embedding_failures: int = Field(ge=0)
    consolidation_seconds: float = Field(ge=0.0)
    ingestion_seconds: float = Field(ge=0.0)
    storage_size_bytes: int = Field(ge=0)
    migrations: int = Field(ge=0)
    corruption_recoveries: int = Field(ge=0)
    updated_at: str


class ActivityEvent(StrictMemoryModel):
    event_id: str = Field(min_length=8, max_length=80)
    kind: Literal[
        "memory_proposed", "memory_accepted", "memory_rejected",
        "memory_corrected", "memory_superseded", "memory_disputed",
        "memory_deleted", "memory_expired", "conflict_detected",
        "conflict_resolved", "entity_merged", "entity_split",
        "document_imported", "document_deleted", "semantic_index_rebuilt",
        "backup_created", "restore_completed",
    ]
    summary: str = Field(min_length=1, max_length=240)
    scope: MemoryScope
    scope_value: str = ""
    references: Tuple[str, ...] = Field(default=(), max_length=16)
    created_at: str


class ReviewEnvelope(StrictMemoryModel):
    schema_version: Literal[1] = 1
    items: Tuple[ReviewItem, ...] = Field(default=(), max_length=512)
    truncated: bool = False
    generated_at: str


class MemoryOverview(StrictMemoryModel):
    schema_version: Literal[1] = 1
    available: bool = True
    health: MemoryHealth
    recent: Tuple[MemoryRecord, ...] = Field(default=(), max_length=16)
    awaiting_review: int = Field(ge=0)
    open_conflicts: int = Field(ge=0)
    stale_memories: int = Field(ge=0)
    expiring_soon: int = Field(ge=0)
    deletion_failures: int = Field(ge=0)
    projects_with_memory: Tuple[str, ...] = Field(default=(), max_length=64)
    documents_indexed: int = Field(ge=0)
    semantic_available: bool = False
    active_context_count: int = Field(ge=0)
    needs_attention: Tuple[str, ...] = Field(default=(), max_length=16)
    generated_at: str


# ---- Import / export ------------------------------------------------------

class ImportResult(StrictMemoryModel):
    imported: int = Field(ge=0)
    skipped: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    errors: Tuple[str, ...] = Field(default=(), max_length=32)
    summary: str = ""


class ExportEnvelope(StrictMemoryModel):
    schema_version: Literal[1] = 1
    scope: MemoryScope
    scope_value: str = ""
    redacted: bool = True
    records: Tuple[MemoryRecord, ...] = Field(default=(), max_length=4096)
    entities: Tuple[EntityRecord, ...] = Field(default=(), max_length=4096)
    relationships: Tuple[RelationshipRecord, ...] = Field(default=(), max_length=4096)
    notes: Tuple[NoteRecord, ...] = Field(default=(), max_length=4096)
    documents: Tuple[DocumentRecord, ...] = Field(default=(), max_length=4096)
    generated_at: str
