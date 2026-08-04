"""AI-assisted interpretation with explicit provenance.

Produces typed interpretation records that are always labeled AI-assisted and
carry their basis, model, runtime, and privacy classification. These are never
presented as parsed facts: consumers must distinguish them from authoritative
state. Records are persisted with bounded retention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .models import InterpretationRecord
from .storage import AIStorage

ALLOWED_TYPES = (
    "hypothesis",
    "insight",
    "summary",
    "risk_signal",
    "recommendation",
    "convention_candidate",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InterpretationError(RuntimeError):
    pass


class InterpretationService:
    def __init__(self, storage: Optional[AIStorage] = None) -> None:
        self._storage = storage

    def create(
        self,
        *,
        interpretation_type: str,
        summary: str,
        basis: Optional[List[str]] = None,
        confidence: Optional[float] = None,
        model: str = "",
        runtime: str = "local",
        privacy_class: str = "restricted",
        project: str = "",
    ) -> InterpretationRecord:
        if interpretation_type not in ALLOWED_TYPES:
            raise InterpretationError("Unsupported interpretation type: %s" % interpretation_type)
        summary = (summary or "").strip()
        if not summary:
            raise InterpretationError("Interpretation summary is required.")
        if confidence is not None:
            confidence = max(0.0, min(1.0, float(confidence)))
        record = InterpretationRecord(
            interpretation_id=_now_iso().replace(":", "").replace("-", "") + "-%04d" % (abs(hash(summary)) % 10000),
            interpretation_type=interpretation_type,
            summary=summary,
            basis=list(basis or []),
            confidence=confidence,
            model=model,
            runtime=runtime,
            privacy_class=privacy_class,
            is_ai_assisted=True,
            created_at=_now_iso(),
            project=project,
        )
        if self._storage is not None:
            self._storage.insert_interpretation(_record_to_dict(record))
        return record

    def list(self, interpretation_type: str = "", limit: int = 100) -> List[InterpretationRecord]:
        if self._storage is None:
            return []
        rows = self._storage.list_interpretations(interpretation_type=interpretation_type, limit=limit)
        return [
            InterpretationRecord(
                interpretation_id=row["interpretation_id"],
                interpretation_type=row["interpretation_type"],
                summary=row["summary"],
                basis=row["basis"],
                confidence=row["confidence"],
                model=row["model"],
                runtime=row["runtime"],
                privacy_class=row["privacy_class"],
                is_ai_assisted=row["is_ai_assisted"],
                created_at=row["created_at"],
                project=row["project"],
            )
            for row in rows
        ]

    def delete(self, interpretation_id: str) -> bool:
        if self._storage is None:
            return False
        return self._storage.delete_interpretation(interpretation_id)

    def count(self) -> int:
        if self._storage is None:
            return 0
        return self._storage.count_interpretations()


def _record_to_dict(record: InterpretationRecord) -> dict:
    return {
        "interpretation_id": record.interpretation_id,
        "interpretation_type": record.interpretation_type,
        "summary": record.summary,
        "basis": list(record.basis),
        "confidence": record.confidence,
        "model": record.model,
        "runtime": record.runtime,
        "privacy_class": record.privacy_class,
        "is_ai_assisted": record.is_ai_assisted,
        "project": record.project,
        "created_at": record.created_at,
    }
