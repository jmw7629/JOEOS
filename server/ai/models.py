"""Typed models for the JoeOS Local AI Runtime platform.

Everything here is either measured/reported by an authoritative provider
(never fabricated) or declared configuration. AI-assisted interpretations are
always labeled as AI-assisted and carry provenance; they are never presented as
parsed facts. Cloud routing is never silent: only explicitly approved
providers may be used, and privacy classification is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ProviderRecord:
    provider_id: str
    name: str
    kind: str = "local"  # local | cloud
    available: bool = False
    reason: str = ""
    model: Optional[str] = None
    embedding_model: Optional[str] = None
    base_url: str = "loopback"  # never a private host identifier
    privacy_class: str = "restricted"
    cloud_approved: bool = False
    supports_streaming: bool = False


@dataclass(frozen=True)
class StreamDelta:
    """One genuine provider stream event. Partial content only when the provider
    truly streams; otherwise a single completed delta with finish_reason."""
    content: str
    provider: str
    model: str
    finish_reason: str = "completed"
    tokens_used: Optional[int] = None
    done: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class InferenceResult:
    reply: str
    model: str
    provider: str
    runtime: str = "local"
    finish_reason: str = "completed"
    tokens_used: Optional[int] = None
    latency_ms: Optional[float] = None
    cancelled: bool = False


@dataclass(frozen=True)
class EmbeddingResult:
    model: str
    provider: str
    dimension: int
    vectors: List[List[float]]
    sources: List[str]
    deduplicated: int = 0


@dataclass(frozen=True)
class EmbeddingMetadata:
    embedding_id: str
    source_ref: str
    content_hash: str
    model: str
    dimension: int
    privacy_class: str = "restricted"
    created_at: str = ""


@dataclass(frozen=True)
class ContextCandidate:
    source_ref: str
    content_hash: str
    relevance: float
    tokens: int
    included: bool = False
    excluded_reason: str = ""


@dataclass(frozen=True)
class ContextResult:
    context_id: str
    project: str = ""
    candidates_considered: int = 0
    sources_selected: List[str] = field(default_factory=list)
    sources_excluded: List[str] = field(default_factory=list)
    duplicate_tokens_removed: int = 0
    tokens_used: int = 0
    token_budget: int = 0
    construction_ms: float = 0.0
    privacy_decisions: List[str] = field(default_factory=list)
    chunks: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class InterpretationRecord:
    interpretation_id: str
    interpretation_type: str
    summary: str
    basis: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    model: str = ""
    runtime: str = "local"
    privacy_class: str = "restricted"
    is_ai_assisted: bool = True
    created_at: str = ""
    project: str = ""


@dataclass(frozen=True)
class AIOverview:
    provider_available: bool = False
    provider_reason: str = ""
    model: Optional[str] = None
    embedding_available: bool = False
    embedding_model: Optional[str] = None
    interpretation_count: int = 0
    generated_at: str = ""
    message: str = ""
