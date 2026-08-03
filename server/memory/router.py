"""REST API for the Memory and Knowledge Platform."""

from __future__ import annotations

from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    EntityRecord,
    EvidenceRecord,
    ImportResult,
    MemoryHealth,
    MemoryOverview,
    MemoryRecord,
    MemoryVersion,
    NoteRecord,
    RelationshipRecord,
    RetrievalEnvelope,
    ReviewEnvelope,
    ReviewItem,
)
from .service import MemoryService

router = APIRouter(prefix="/api/v1", tags=["memory"])


def get_memory_service(request: Request) -> MemoryService:
    service = getattr(request.app.state, "memory_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Memory service is not initialized.")
    return service


@router.get("/memory/health", response_model=MemoryHealth)
def health(service: MemoryService = Depends(get_memory_service)) -> MemoryHealth:
    return service.health()


@router.get("/memory/overview", response_model=MemoryOverview)
def overview(service: MemoryService = Depends(get_memory_service)) -> MemoryOverview:
    return service.overview()


@router.post("/memory/records", response_model=MemoryRecord, status_code=status.HTTP_201_CREATED)
def propose_record(record: MemoryRecord, service: MemoryService = Depends(get_memory_service)) -> MemoryRecord:
    return service.propose(record)


@router.get("/memory/records/{memory_id}", response_model=MemoryRecord)
def get_record(memory_id: str, service: MemoryService = Depends(get_memory_service)) -> MemoryRecord:
    record = service.get(memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory record not found.")
    return record


@router.get("/memory/records")
def list_records(
    scope: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    service: MemoryService = Depends(get_memory_service),
) -> List[dict]:
    return [r.model_dump() for r in service.list(scope=scope, limit=limit)]


@router.get("/memory/search", response_model=RetrievalEnvelope)
def search(
    q: str = Query(min_length=1),
    scope: Optional[str] = Query(default=None),
    limit: int = Query(default=16, ge=1, le=50),
    service: MemoryService = Depends(get_memory_service),
) -> RetrievalEnvelope:
    return service.search(q, scope=scope, limit=limit)


@router.get("/memory/review", response_model=ReviewEnvelope)
def review_queue(
    state: str = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
    service: MemoryService = Depends(get_memory_service),
) -> ReviewEnvelope:
    return service.review_queue(state=state, limit=limit)


@router.post("/memory/review/{review_id}")
def resolve_review(review_id: str, payload: dict, service: MemoryService = Depends(get_memory_service)) -> dict:
    action = str(payload.get("action") or "")
    if not service.review_action(review_id, action, note=str(payload.get("note") or "")):
        raise HTTPException(status_code=404, detail="Review item not found or not open.")
    return {"review_id": review_id, "action": action, "resolved": True}


@router.get("/memory/records/{memory_id}/versions", response_model=List[MemoryVersion])
def record_versions(memory_id: str, service: MemoryService = Depends(get_memory_service)) -> List[MemoryVersion]:
    return list(service.versions(memory_id))


@router.post("/memory/records/{memory_id}/correct", response_model=MemoryRecord)
def correct_record(memory_id: str, payload: dict, service: MemoryService = Depends(get_memory_service)) -> MemoryRecord:
    record = service.correct(memory_id, new_content=str(payload.get("content") or ""), reason=str(payload.get("reason") or ""), changed_by=str(payload.get("changed_by") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Memory record not found.")
    return record


@router.post("/memory/records/{memory_id}/supersede")
def supersede_record(memory_id: str, payload: dict, service: MemoryService = Depends(get_memory_service)) -> dict:
    record = service.supersede(memory_id, str(payload.get("replacement_id") or ""), reason=str(payload.get("reason") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Memory record not found.")
    return {"superseded": True, "memory_id": memory_id, "replacement_id": payload.get("replacement_id")}


@router.post("/memory/records/{memory_id}/delete")
def delete_record(memory_id: str, payload: dict = None, service: MemoryService = Depends(get_memory_service)) -> dict:
    deleted = service.delete(memory_id, reason=str((payload or {}).get("reason") or "explicit deletion"))
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory record not found.")
    return {"deleted": True, "memory_id": memory_id}


@router.post("/memory/evidence", response_model=EvidenceRecord)
def add_evidence(evidence: EvidenceRecord, service: MemoryService = Depends(get_memory_service)) -> EvidenceRecord:
    return service.add_evidence(evidence)


@router.post("/memory/entities", response_model=EntityRecord)
def register_entity(entity: EntityRecord, service: MemoryService = Depends(get_memory_service)) -> EntityRecord:
    return service.register_entity(entity)


@router.get("/memory/entities")
def list_entities(
    scope: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    service: MemoryService = Depends(get_memory_service),
) -> List[dict]:
    return [e.model_dump() for e in service.entities(scope=scope, limit=limit)]


@router.post("/memory/relationships", response_model=RelationshipRecord)
def register_relationship(rel: RelationshipRecord, service: MemoryService = Depends(get_memory_service)) -> RelationshipRecord:
    return service.register_relationship(rel)


@router.post("/memory/import", response_model=ImportResult)
def import_records(payload: dict, service: MemoryService = Depends(get_memory_service)) -> ImportResult:
    records = payload.get("records") or []
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=422, detail="A non-empty 'records' list is required.")
    validated = tuple(MemoryRecord.model_validate(r) for r in records)
    return service.import_records(validated)


@router.post("/memory/backup")
def backup_memory(service: MemoryService = Depends(get_memory_service)) -> dict:
    path = service.backup()
    return {"backup_created": path is not None, "path": path}


@router.get("/memory/storage")
def storage_stats(service: MemoryService = Depends(get_memory_service)) -> dict:
    return service.storage_stats()


@router.post("/memory/expire", status_code=202)
def expire_due(service: MemoryService = Depends(get_memory_service)) -> dict:
    count = service.expire_due()
    return {"expired": count}