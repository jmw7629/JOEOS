"""Bounded context construction for AI-assisted work.

Selects sources by relevance and recency, deduplicates by content hash,
enforces a token budget, and records every decision (candidates considered,
sources selected/excluded, duplicate tokens removed, privacy decisions). This
keeps context construction bounded and auditable without weakening isolation.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .models import ContextCandidate, ContextResult
from .storage import AIStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokens(text: str) -> int:
    """Deterministic token estimate (chars / 4); used only for budgets."""
    return max(0, (len(text) + 3) // 4)


class ContextBuilder:
    def __init__(
        self,
        storage: Optional[AIStorage] = None,
        *,
        relevance_threshold: float = 0.2,
        default_budget: int = 4000,
        max_candidates: int = 100,
        now_provider=None,
    ) -> None:
        self._storage = storage
        self._relevance_threshold = max(0.0, min(1.0, float(relevance_threshold)))
        self._default_budget = max(200, int(default_budget))
        self._max_candidates = max(10, int(max_candidates))
        self._now = now_provider or time.monotonic

    def build(
        self,
        sources: List[dict],
        *,
        project: str = "",
        token_budget: int = 0,
        purpose: str = "analysis",
    ) -> ContextResult:
        budget = self._default_budget if token_budget <= 0 else max(200, int(token_budget))
        start = time.monotonic()
        candidates = sources[: self._max_candidates]
        considered = len(candidates)

        dedupe_seen = set()
        selected: List[str] = []
        excluded: List[str] = []
        chunks: List[str] = []
        duplicate_tokens_removed = 0
        privacy_decisions: List[str] = []
        tokens_used = 0

        ranked = sorted(
            candidates,
            key=lambda source: (
                -float(source.get("relevance", 0.0) if source.get("relevance") is not None else 0.0),
                -(source.get("recency_score", 0.0) if source.get("recency_score") is not None else 0.0),
            ),
        )

        for source in ranked:
            ref = str(source.get("source_ref") or "")
            text = str(source.get("content") or "")
            relevance = float(source.get("relevance") or 0.0)
            if not text:
                excluded.append(ref or "empty-source")
                continue
            digest = _content_hash(text)
            if digest in dedupe_seen:
                duplicate_tokens_removed += _tokens(text)
                excluded.append(ref or "duplicate")
                continue
            if relevance < self._relevance_threshold and purpose != "exact":
                excluded.append(ref or ("relevance-" + str(round(relevance, 2))))
                continue
            estimate = _tokens(text)
            if tokens_used + estimate > budget:
                excluded.append(ref or "token-budget")
                continue
            if source.get("privacy_class") in ("secret", "credential") and purpose != "privacy-audit":
                privacy_decisions.append(ref + " excluded: privacy_class=" + str(source.get("privacy_class")))
                excluded.append(ref or "privacy")
                continue
            dedupe_seen.add(digest)
            chunks.append(text)
            selected.append(ref)
            tokens_used += estimate
            if tokens_used >= budget:
                break

        construction_ms = (time.monotonic() - start) * 1000.0
        result = ContextResult(
            context_id=_now_iso().replace(":", "").replace("-", ""),
            project=project,
            candidates_considered=considered,
            sources_selected=selected,
            sources_excluded=excluded,
            duplicate_tokens_removed=duplicate_tokens_removed,
            tokens_used=tokens_used,
            token_budget=budget,
            construction_ms=construction_ms,
            privacy_decisions=privacy_decisions,
            chunks=chunks,
        )
        if self._storage is not None:
            self._storage.insert_context(_context_to_dict(result))
        return result


def _context_to_dict(result: ContextResult) -> dict:
    return {
        "context_id": result.context_id,
        "project": result.project,
        "candidates_considered": result.candidates_considered,
        "sources_selected": result.sources_selected,
        "sources_excluded": result.sources_excluded,
        "duplicate_tokens_removed": result.duplicate_tokens_removed,
        "tokens_used": result.tokens_used,
        "token_budget": result.token_budget,
        "construction_ms": result.construction_ms,
        "privacy_decisions": result.privacy_decisions,
        "created_at": _now_iso(),
    }
