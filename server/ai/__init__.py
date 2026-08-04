"""JoeOS Local AI Runtime platform.

A provider-neutral AI layer over the private local Lemonade server. Provides
the Provider Registry (local only unless a cloud provider is explicitly
policy-approved), local-first embeddings with content-hash deduplication and
honest availability, bounded context construction with full decision tracking,
and AI-assisted interpretation that is always labeled as AI-assisted with
provenance — never presented as parsed facts.

See `docs/architecture/AI_RUNTIME.md` for the design and honest guarantees.
"""

from .context import ContextBuilder
from .embeddings import EmbeddingError, EmbeddingService, content_hash
from .interpret import InterpretationError, InterpretationService
from .models import (
    AIOverview,
    ContextCandidate,
    ContextResult,
    EmbeddingMetadata,
    EmbeddingResult,
    InferenceResult,
    InterpretationRecord,
    ProviderRecord,
)
from .providers import LocalLemonadeProvider, ProviderRegistry
from .router import router as ai_router
from .service import AIService
from .storage import AIStorage

__all__ = [
    "AIOverview",
    "AIService",
    "AIStorage",
    "ContextBuilder",
    "ContextCandidate",
    "ContextResult",
    "EmbeddingError",
    "EmbeddingMetadata",
    "EmbeddingResult",
    "EmbeddingService",
    "InferenceResult",
    "InterpretationError",
    "InterpretationRecord",
    "InterpretationService",
    "LocalLemonadeProvider",
    "ProviderRecord",
    "ProviderRegistry",
    "ai_router",
    "content_hash",
]
