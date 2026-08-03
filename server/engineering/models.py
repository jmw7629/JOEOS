"""Engineering Workspace platform contracts."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["reported", "inferred", "uncertain"]
TrustState = Literal["untrusted", "session", "trusted"]
RiskLevel = Literal["low", "medium", "high"]
CommandState = Literal[
    "suggested",
    "approved",
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "blocked",
]
FileKind = Literal["file", "directory", "symlink"]
GitFileState = Literal["clean", "modified", "staged", "added", "deleted", "untracked", "conflicted"]


class StrictEngineeringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CharacteristicEvidence(StrictEngineeringModel):
    characteristic: str = Field(min_length=1, max_length=80)
    source_file: str = Field(min_length=1, max_length=240)
    confidence: Confidence


class ProjectRecord(StrictEngineeringModel):
    project_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=1024)
    trust_state: TrustState = "untrusted"
    fingerprint: str = Field(min_length=8, max_length=64)
    characteristics: Tuple[CharacteristicEvidence, ...] = Field(default=(), max_length=64)
    healthy: bool = True
    warnings: Tuple[str, ...] = Field(default=(), max_length=16)
    created_at: str
    updated_at: str


class FileEntry(StrictEngineeringModel):
    name: str = Field(min_length=1, max_length=240)
    path: str = Field(min_length=1, max_length=1024)
    kind: FileKind
    size: int = Field(ge=0)
    modified_at: Optional[str] = None
    language: Optional[str] = None
    git_state: GitFileState = "clean"
    is_secret: bool = False
    hidden: bool = False


class DirectoryListing(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    project_id: str
    directory: str
    entries: Tuple[FileEntry, ...] = Field(max_length=2048)
    truncated: bool = False


class DocumentRevision(StrictEngineeringModel):
    size: int = Field(ge=0)
    modified_at: Optional[str] = None
    sha256: str = Field(min_length=64, max_length=64)


class DocumentState(StrictEngineeringModel):
    path: str
    content: str
    masked_secrets: int = Field(ge=0)
    revision: DocumentRevision


class DocumentWriteRequest(StrictEngineeringModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=2_000_000)
    base_revision: Optional[str] = Field(default=None, min_length=64, max_length=64)


class DocumentWriteResult(StrictEngineeringModel):
    path: str
    saved: bool
    conflict: bool = False
    conflict_message: Optional[str] = None
    revision: Optional[DocumentRevision] = None


class SecretMatch(StrictEngineeringModel):
    file: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=0)
    category: str = Field(min_length=1, max_length=60)
    masked: str = Field(min_length=1, max_length=80)
    confidence: Literal["high", "medium"] = "medium"
    source: str = Field(min_length=1, max_length=40)
    remediation: str = Field(min_length=1, max_length=240)


class SecretScanResult(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    matches: Tuple[SecretMatch, ...] = Field(max_length=128)
    files_scanned: int = Field(ge=0)
    truncated: bool = False


class SecretPolicy(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    masked_categories: Tuple[str, ...]
    secret_path_names: Tuple[str, ...]
    protected_operations: Tuple[str, ...]
    notice: str


class GitStatus(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    project_id: str
    branch: Optional[str] = None
    detached: bool = False
    ahead: int = Field(ge=0)
    behind: int = Field(ge=0)
    staged: Tuple[str, ...] = Field(default=(), max_length=512)
    unstaged: Tuple[str, ...] = Field(default=(), max_length=512)
    untracked: Tuple[str, ...] = Field(default=(), max_length=512)
    conflicted: Tuple[str, ...] = Field(default=(), max_length=128)
    last_commit: Optional[str] = None
    secret_matches: Tuple[SecretMatch, ...] = Field(default=(), max_length=64)


class DiffHunk(StrictEngineeringModel):
    line: int = Field(ge=1)
    type: Literal["add", "remove", "context"]
    text: str = Field(max_length=4096)


class DiffEntry(StrictEngineeringModel):
    path: str = Field(min_length=1, max_length=1024)
    state: GitFileState
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    hunks: Tuple[DiffHunk, ...] = Field(default=(), max_length=4096)
    secret_matches: Tuple[SecretMatch, ...] = Field(default=(), max_length=32)


class CommitResult(StrictEngineeringModel):
    project_id: str
    commit: str = Field(min_length=7, max_length=40)
    summary: str
    secret_matches: Tuple[SecretMatch, ...] = Field(default=(), max_length=64)
    committed: bool


class CommandContract(StrictEngineeringModel):
    execution_id: str = Field(min_length=8, max_length=40, pattern=r"^[a-z0-9-]+$")
    project_id: Optional[str] = Field(default=None, max_length=80)
    command_id: Optional[str] = Field(default=None, max_length=80)
    source: Literal["user", "agent", "registered"] = "user"
    executable: str = Field(min_length=1, max_length=512)
    args: Tuple[str, ...] = Field(default=(), max_length=256)
    cwd: Optional[str] = Field(default=None, max_length=1024)
    shell: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    risk: RiskLevel
    requires_approval: bool = False
    trust_required: bool = True
    network_required: bool = False
    output_retention: int = Field(default=1000, ge=100, le=20_000)
    approved: bool = False
    task_id: Optional[str] = Field(default=None, max_length=80)


class CommandResult(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    execution_id: str
    state: CommandState
    exit_code: Optional[int] = None
    stdout: str = Field(max_length=100_000)
    stderr: str = Field(max_length=100_000)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    error_code: Optional[str] = None
    error_category: Optional[str] = None
    blocked_reason: Optional[str] = None
    risk: RiskLevel


class SearchResult(StrictEngineeringModel):
    path: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    snippet: str = Field(max_length=4096)
    redacted: bool = False


class SearchEnvelope(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    project_id: str
    query: str
    results: Tuple[SearchResult, ...] = Field(max_length=1024)
    truncated: bool = False
    files_scanned: int = Field(ge=0)
    seconds: float = Field(ge=0)


class ProjectEnvelope(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    projects: Tuple[ProjectRecord, ...] = Field(max_length=256)


class CommandValidation(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    contract: CommandContract
    risk: RiskLevel
    approval_required: bool
    trust_required: bool
    network_required: bool
    allowed: bool
    blocked_reason: Optional[str] = None


class ActivityEntry(StrictEngineeringModel):
    id: int = Field(ge=0)
    project_id: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=40)
    status: str = Field(min_length=1, max_length=40)
    detail: str = ""
    created_at: str
