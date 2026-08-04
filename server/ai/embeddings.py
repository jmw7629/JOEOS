"""Local-first semantic embeddings.

Embeddings are generated only by an actually available local embedding model.
When the runtime reports none, the service reports ``unavailable`` rather than
fabricating vectors. Chunks are deduplicated by content hash so unchanged
content is never re-embedded, and dimension validation rejects incompatible
models. Only metadata (hash, model, dimension) is persisted — never vectors or
content.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .models import EmbeddingResult
from .providers import ProviderRegistry
from .storage import AIStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingError(RuntimeError):
    pass


class EmbeddingService:
    def __init__(
        self,
        providers: ProviderRegistry,
        storage: Optional[AIStorage] = None,
        *,
        chunk_size: int = 2000,
        max_batch: int = 32,
        now_provider=None,
    ) -> None:
        self._providers = providers
        self._storage = storage
        self._chunk_size = max(100, int(chunk_size))
        self._max_batch = max(1, min(64, int(max_batch)))
        self._lock = threading.RLock()
        self._now = now_provider or time.monotonic

    def available(self) -> bool:
        provider = self._providers.default()
        return bool(provider and provider.availability().embedding_model)

    def embedding_model(self) -> Optional[str]:
        provider = self._providers.default()
        return provider.availability().embedding_model if provider else None

    def chunk(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= self._chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            boundary = text.rfind("\n", start, end)
            if boundary > start + self._chunk_size // 2:
                end = boundary
            chunks.append(text[start:end])
            start = end
        return chunks

    async def embed(
        self,
        texts: List[str],
        *,
        project: str = "",
        source_refs: Optional[List[str]] = None,
        privacy_class: str = "restricted",
    ) -> EmbeddingResult:
        provider = self._providers.default()
        if provider is None:
            raise EmbeddingError("No inference provider is registered.")
        record = provider.availability()
        if not record.embedding_model:
            raise EmbeddingError("No local embedding model is available.")
        model = record.embedding_model

        chunks = []
        refs = []
        for index, text in enumerate(texts):
            for chunk in self.chunk(text):
                chunks.append(chunk)
                refs.append(source_refs[index] if source_refs and index < len(source_refs) else ("chunk-%d" % index))

        deduplicated = 0
        unique_pairs = []
        seen = set()
        if self._storage is not None:
            hashes = [content_hash(chunk) for chunk in chunks]
            existing = self._storage.embedding_dedupe_hashes(hashes)
            for chunk, ref, digest in zip(chunks, refs, hashes):
                pair = (ref, digest)
                if digest in existing or pair in seen:
                    deduplicated += 1
                    continue
                seen.add(pair)
                unique_pairs.append((chunk, ref, digest))
        else:
            for chunk, ref in zip(chunks, refs):
                unique_pairs.append((chunk, ref, content_hash(chunk)))

        vectors: List[List[float]] = []
        dimension = 0
        for start in range(0, len(unique_pairs), self._max_batch):
            batch = unique_pairs[start:start + self._max_batch]
            batch_vectors = await provider.embed([item[0] for item in batch], model=model)
            for vector in batch_vectors:
                if dimension == 0:
                    dimension = len(vector)
                elif len(vector) != dimension:
                    raise EmbeddingError("Incompatible embedding dimensions from the same model.")
            vectors.extend(batch_vectors)
            if self._storage is not None:
                for (chunk, ref, digest), vector in zip(batch, batch_vectors):
                    self._storage.insert_embedding_metadata({
                        "embedding_id": digest[:24],
                        "source_ref": ref[:200],
                        "content_hash": digest,
                        "model": model,
                        "dimension": len(vector),
                        "privacy_class": privacy_class,
                        "created_at": _now_iso(),
                    })

        return EmbeddingResult(
            model=model,
            provider=provider.provider_id,
            dimension=dimension,
            vectors=vectors,
            sources=[ref for _, ref, _ in unique_pairs],
            deduplicated=deduplicated,
        )
