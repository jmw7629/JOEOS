"""Repository Intelligence platform contracts.

Every record carries provenance: a source kind, the source identifier, and a
confidence. Nothing is fabricated. Parser-derived facts, Git history facts,
manifest facts, and user-entered facts remain distinguishable. Semantic
embedding results are separate from parsed facts.
"""

from __future__ import annotations

from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["reported", "inferred", "uncertain"]
ProvenanceKind = Literal[
    "manifest", "git", "parser", "classification", "user", "ai_inference",
    "file_system", "heuristic", "documentation",
]
FreshnessState = Literal["current", "likely_current", "stale", "invalid", "rebuilding", "unavailable", "unknown"]
IndexHealthState = Literal[
    "healthy", "indexing", "degraded", "stale", "partially_available",
    "corrupted", "incompatible", "unavailable", "cancelled", "unknown",
]
IndexPhase = Literal[
    "queued", "scanning", "classifying", "parsing", "linking", "embedding",
    "validating", "finalizing", "completed", "completed_with_warnings",
    "cancelled", "failed",
]
SymbolKind = Literal[
    "module", "namespace", "class", "interface", "type", "enum", "function",
    "method", "constructor", "property", "field", "variable", "constant",
    "component", "route", "endpoint", "service", "event", "command", "agent",
    "tool", "schema", "table", "migration", "test", "fixture", "task",
    "configuration_key", "decorator", "section", "struct",
]
ReferenceKind = Literal[
    "import", "export", "call", "construction", "inheritance",
    "implementation", "property_access", "event_publish", "event_subscribe",
    "route_registration", "api_invocation", "command_registration",
    "service_lookup", "tool_registration", "dependency_injection",
    "schema_usage", "test_relationship", "documentation_link",
    "configuration_reference",
]
ResolutionState = Literal[
    "resolved", "partially_resolved", "unresolved", "dynamically_resolved",
    "inferred", "external_dependency",
]
FileClassification = Literal[
    "source", "test", "configuration", "documentation", "asset", "generated",
    "dependency", "migration", "schema", "build_output", "cache", "binary",
    "secret_bearing", "environment", "infrastructure", "deployment",
    "workflow", "package_manifest", "lockfile", "localization", "design_token",
    "style", "route", "component", "service", "model", "utility", "script",
    "tool", "data", "fixture", "snapshot", "unknown",
]
GitFileState = Literal["clean", "modified", "staged", "added", "deleted", "untracked", "conflicted"]
RiskSeverity = Literal["info", "low", "medium", "high", "critical"]
ImpactLikelihood = Literal["direct", "likely", "possible", "insufficient_evidence"]
DecisionStatus = Literal["proposed", "accepted", "rejected", "superseded", "deprecated", "unknown"]
ConventionSource = Literal["explicit_rule", "lint_config", "strong_pattern", "weak_pattern", "ai_inference", "user_override"]
MemoryStatus = Literal["proposed", "review", "accepted", "corrected", "superseded"]
OwnershipKind = Literal["explicit_owner", "configured_reviewer", "recent_contributor", "inferred_contributor", "unowned"]


class StrictIntelligenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Provenance(StrictIntelligenceModel):
    kind: ProvenanceKind
    source: str = Field(min_length=1, max_length=240)
    detail: str = ""
    detected_at: str


class FingerprintComponent(StrictIntelligenceModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=240)
    stable_across_branches: bool = True
    provenance: Provenance


class RepositoryFingerprint(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    fingerprint: str = Field(min_length=8, max_length=64)
    components: Tuple[FingerprintComponent, ...] = Field(max_length=64)
    fingerprint_version: str = "1.0.0"
    generated_at: str


class ProjectIdentity(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    root: str = Field(min_length=1, max_length=1024)
    repository_root: Optional[str] = None
    remote_url: Optional[str] = None
    default_branch: Optional[str] = None
    active_branch: Optional[str] = None
    current_commit: Optional[str] = None
    dirty: bool = False
    fingerprint: str = Field(min_length=8, max_length=64)
    project_type: str = "unknown"
    languages: Tuple[str, ...] = Field(default=(), max_length=32)
    frameworks: Tuple[str, ...] = Field(default=(), max_length=32)
    package_manager: Optional[str] = None
    build_system: Optional[str] = None
    test_system: Optional[str] = None
    trust_state: str = "untrusted"
    availability: Literal["available", "unavailable", "review"] = "available"
    registered_at: str
    last_indexed_at: Optional[str] = None
    index_version: Optional[str] = None


class FileInventoryRecord(StrictIntelligenceModel):
    file_id: str = Field(min_length=8, max_length=40)
    project_id: str
    rel_path: str = Field(min_length=1, max_length=1024)
    file_name: str = Field(min_length=1, max_length=240)
    extension: str = ""
    language: Optional[str] = None
    binary: bool = False
    size: int = Field(ge=0)
    modified_at: Optional[str] = None
    content_hash: str = Field(min_length=64, max_length=64)
    classification: FileClassification = "unknown"
    classification_confidence: Confidence = "uncertain"
    tracked: bool = True
    ignored: bool = False
    hidden: bool = False
    generated: bool = False
    secret_sensitive: bool = False
    git_state: GitFileState = "clean"
    parser: Optional[str] = None
    parser_available: bool = False
    symbol_count: int = Field(default=0, ge=0)
    reference_count: int = Field(default=0, ge=0)
    last_indexed_at: Optional[str] = None
    stale: bool = False


class ClassificationEvidence(StrictIntelligenceModel):
    classification: FileClassification
    confidence: Confidence
    source: str = Field(min_length=1, max_length=240)
    date: str


class LanguageDetection(StrictIntelligenceModel):
    language: str
    confidence: Confidence
    evidence: Tuple[str, ...] = Field(default=(), max_length=16)
    parser_available: bool = False
    symbol_extraction_available: bool = False
    reference_extraction_available: bool = False


class FrameworkDetection(StrictIntelligenceModel):
    framework: str
    version: Optional[str] = None
    confidence: Confidence
    evidence_files: Tuple[str, ...] = Field(default=(), max_length=32)
    source: str = Field(min_length=1, max_length=240)
    detected_at: str


class SymbolRecord(StrictIntelligenceModel):
    symbol_id: str = Field(min_length=8, max_length=80)
    project_id: str
    file_id: str = Field(min_length=8, max_length=40)
    name: str = Field(min_length=1, max_length=240)
    qualified_name: str = Field(min_length=1, max_length=1024)
    kind: SymbolKind
    language: str
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    visibility: Literal["public", "private", "protected", "internal", "unknown"] = "unknown"
    exported: bool = False
    signature: str = ""
    parent_symbol: Optional[str] = None
    module: str = ""
    documentation: str = ""
    parser: str
    confidence: Confidence
    content_version: str = Field(min_length=64, max_length=64)
    stale: bool = False


class ReferenceRecord(StrictIntelligenceModel):
    reference_id: str = Field(min_length=8, max_length=80)
    project_id: str
    source_symbol_id: Optional[str] = Field(default=None, max_length=80)
    target_symbol_id: Optional[str] = Field(default=None, max_length=80)
    source_file_id: str = Field(min_length=8, max_length=40)
    target_file_id: Optional[str] = Field(default=None, max_length=40)
    rel_path: str = Field(min_length=1, max_length=1024)
    target_text: str = Field(min_length=1, max_length=240)
    kind: ReferenceKind
    line: int = Field(ge=1)
    resolution: ResolutionState = "unresolved"
    parser: str = Field(min_length=1, max_length=80)
    confidence: Confidence = "uncertain"
    stale: bool = False


class DependencyEdge(StrictIntelligenceModel):
    source_file_id: str = Field(min_length=8, max_length=40)
    target_file_id: str = Field(min_length=8, max_length=40)
    source_rel_path: str = Field(min_length=1, max_length=1024)
    target_rel_path: str = Field(min_length=1, max_length=1024)
    kind: str = "import"
    direct: bool = True
    provenance: Provenance
    resolution: ResolutionState = "resolved"


class DependencyGraph(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    nodes: Tuple[str, ...] = Field(default=(), max_length=65536)
    edges: Tuple[DependencyEdge, ...] = Field(default=(), max_length=65536)
    cycles: Tuple[Tuple[str, ...], ...] = Field(default=(), max_length=64)
    external_dependencies: Tuple[str, ...] = Field(default=(), max_length=1024)
    generated_at: str
    stale: bool = False


class ArchitectureNode(StrictIntelligenceModel):
    node_id: str = Field(min_length=8, max_length=80)
    project_id: str
    name: str = Field(min_length=1, max_length=240)
    kind: str = "module"
    evidence: str = Field(min_length=1, max_length=240)
    confidence: Confidence = "inferred"
    derived_from: Literal["evidence", "ai_inference"] = "evidence"
    source_file: Optional[str] = None


class ArchitectureGraph(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    nodes: Tuple[ArchitectureNode, ...] = Field(default=(), max_length=8192)
    edges: Tuple[Tuple[str, str, str], ...] = Field(default=(), max_length=16384)
    generated_at: str
    stale: bool = False


class GitHistoryStat(StrictIntelligenceModel):
    file_id: Optional[str] = Field(default=None, max_length=40)
    rel_path: str = Field(min_length=1, max_length=1024)
    commits: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    authors: int = Field(ge=0)
    first_commit: Optional[str] = None
    last_commit: Optional[str] = None
    churn: int = Field(ge=0)


class ChangeHotspot(StrictIntelligenceModel):
    rel_path: str = Field(min_length=1, max_length=1024)
    score: int = Field(ge=0, le=100)
    concern: Literal["low_concern", "moderate_concern", "elevated_concern", "high_concern", "insufficient_data"] = "insufficient_data"
    factors: Tuple[str, ...] = Field(default=(), max_length=32)
    contributors: Tuple[str, ...] = Field(default=(), max_length=8)


class OwnershipRecord(StrictIntelligenceModel):
    area: str = Field(min_length=1, max_length=240)
    path_scope: str = Field(min_length=1, max_length=1024)
    owner: Optional[str] = Field(default=None, max_length=240)
    kind: OwnershipKind = "unowned"
    source: str = Field(min_length=1, max_length=240)
    confidence: Confidence = "uncertain"
    time_range: Optional[str] = None
    review_required: bool = False
    fallback_owner: Optional[str] = Field(default=None, max_length=240)


class GitIntelligence(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    stats: Tuple[GitHistoryStat, ...] = Field(default=(), max_length=65536)
    hotspots: Tuple[ChangeHotspot, ...] = Field(default=(), max_length=1024)
    ownership: Tuple[OwnershipRecord, ...] = Field(default=(), max_length=1024)
    recent_commits: Tuple[str, ...] = Field(default=(), max_length=256)
    history_depth: int = Field(ge=0)
    generated_at: str
    stale: bool = False


class ChangeImpact(StrictIntelligenceModel):
    project_id: str
    target: str = Field(min_length=1, max_length=1024)
    impacted_file_id: str = Field(min_length=8, max_length=40)
    impacted_path: str = Field(min_length=1, max_length=1024)
    relationship: str = Field(min_length=1, max_length=80)
    confidence: Confidence = "uncertain"
    depth: int = Field(ge=1, le=64)
    likelihood: ImpactLikelihood = "possible"
    recommended_validation: Tuple[str, ...] = Field(default=(), max_length=32)


class RiskFinding(StrictIntelligenceModel):
    risk_id: str = Field(min_length=8, max_length=80)
    project_id: str
    category: str = Field(min_length=1, max_length=120)
    severity: RiskSeverity = "info"
    confidence: Confidence = "uncertain"
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    affected_items: Tuple[str, ...] = Field(default=(), max_length=128)
    mitigation: str = ""
    review_required: bool = False
    recommended_tests: Tuple[str, ...] = Field(default=(), max_length=32)
    generated_at: str


class DecisionRecord(StrictIntelligenceModel):
    decision_id: str = Field(min_length=8, max_length=80)
    project_id: str
    title: str = Field(min_length=1, max_length=240)
    status: DecisionStatus = "proposed"
    date: str
    owner: Optional[str] = Field(default=None, max_length=120)
    context: str = ""
    decision: str = ""
    alternatives: Tuple[str, ...] = Field(default=(), max_length=64)
    consequences: Tuple[str, ...] = Field(default=(), max_length=64)
    affected_systems: Tuple[str, ...] = Field(default=(), max_length=64)
    affected_files: Tuple[str, ...] = Field(default=(), max_length=256)
    supersedes: Optional[str] = Field(default=None, max_length=80)
    source: Provenance
    approval_state: Literal["none", "proposed", "approved", "rejected"] = "none"
    approved_by: Optional[str] = Field(default=None, max_length=120)


class ConventionRecord(StrictIntelligenceModel):
    convention_id: str = Field(min_length=8, max_length=80)
    project_id: str
    convention: str = Field(min_length=1, max_length=500)
    source_kind: ConventionSource = "weak_pattern"
    scope: str = "project"
    confidence: Confidence = "uncertain"
    authoritative: bool = False
    date: str
    exceptions: Tuple[str, ...] = Field(default=(), max_length=64)
    affected_languages: Tuple[str, ...] = Field(default=(), max_length=32)
    affected_paths: Tuple[str, ...] = Field(default=(), max_length=64)
    provenance: Provenance


class MemoryEntry(StrictIntelligenceModel):
    memory_id: str = Field(min_length=8, max_length=80)
    project_id: str
    type: str = "note"
    summary: str = Field(min_length=1, max_length=1000)
    status: MemoryStatus = "proposed"
    source: Provenance
    confidence: Confidence = "uncertain"
    created_at: str
    updated_at: str
    expiration: Optional[str] = None
    related_files: Tuple[str, ...] = Field(default=(), max_length=256)
    related_symbols: Tuple[str, ...] = Field(default=(), max_length=256)
    related_decisions: Tuple[str, ...] = Field(default=(), max_length=64)
    related_tasks: Tuple[str, ...] = Field(default=(), max_length=64)
    superseded_by: Optional[str] = Field(default=None, max_length=80)


class IndexDiagnostics(StrictIntelligenceModel):
    indexed_files: int = Field(ge=0)
    excluded_files: int = Field(ge=0)
    parse_failures: int = Field(ge=0)
    unresolved_references: int = Field(ge=0)
    stale_files: int = Field(ge=0)
    symbols: int = Field(ge=0)
    relationships: int = Field(ge=0)
    last_full_index: Optional[str] = None
    last_incremental_update: Optional[str] = None
    parser_versions: Tuple[str, ...] = Field(default=(), max_length=64)
    storage_version: str = ""
    storage_size_bytes: int = Field(ge=0)
    recent_errors: Tuple[str, ...] = Field(default=(), max_length=64)


class IndexHealth(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    state: IndexHealthState = "unknown"
    phase: Optional[IndexPhase] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    message: str = ""
    diagnostics: IndexDiagnostics = Field(default_factory=lambda: IndexDiagnostics(
        indexed_files=0, excluded_files=0, parse_failures=0, unresolved_references=0,
        stale_files=0, symbols=0, relationships=0, parser_versions=(), storage_version="",
        storage_size_bytes=0, recent_errors=(),
    ))
    updated_at: str


class RetrievalResult(StrictIntelligenceModel):
    result_id: str = Field(min_length=8, max_length=80)
    project_id: str
    kind: str = "file"
    target: str = Field(min_length=1, max_length=1024)
    score: float = Field(ge=0.0, le=1.0)
    ranking_factors: Tuple[str, ...] = Field(default=(), max_length=32)
    provenance: Provenance
    freshness: FreshnessState = "unknown"
    privacy_classification: str = "public"
    excerpt: str = ""
    navigation_target: str = Field(min_length=1, max_length=1024)


class RetrievalEnvelope(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    query: str
    results: Tuple[RetrievalResult, ...] = Field(default=(), max_length=512)
    truncated: bool = False
    hybrid: bool = False
    seconds: float = Field(ge=0)


class ContextPackItem(StrictIntelligenceModel):
    item_id: str = Field(min_length=8, max_length=80)
    kind: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=1024)
    source: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=240)
    token_estimate: int = Field(ge=0)
    confidence: Confidence = "uncertain"
    freshness: FreshnessState = "unknown"
    privacy_classification: str = "public"


class ContextPack(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    pack_id: str = Field(min_length=8, max_length=80)
    project_id: str
    objective: str = Field(min_length=1, max_length=500)
    items: Tuple[ContextPackItem, ...] = Field(default=(), max_length=512)
    excluded_summary: str = ""
    generated_at: str


class ProjectOverview(StrictIntelligenceModel):
    schema_version: Literal[1] = 1
    project_id: str
    identity: ProjectIdentity
    fingerprint: RepositoryFingerprint
    files: int = Field(ge=0)
    symbols: int = Field(ge=0)
    parse_failures: int = Field(ge=0)
    cycles: int = Field(ge=0)
    hotspots: Tuple[ChangeHotspot, ...] = Field(default=(), max_length=64)
    decisions: Tuple[DecisionRecord, ...] = Field(default=(), max_length=64)
    conventions: Tuple[ConventionRecord, ...] = Field(default=(), max_length=64)
    health: IndexHealth
    generated_at: str
