"""REST API for the Project and Repository Intelligence platform."""

from __future__ import annotations

from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    ArchitectureGraph,
    ChangeImpact,
    ContextPack,
    DecisionRecord,
    ConventionRecord,
    DependencyGraph,
    GitIntelligence,
    IndexHealth,
    MemoryEntry,
    MemoryStatus,
    ProjectIdentity,
    ProjectOverview,
    RepositoryFingerprint,
    RetrievalEnvelope,
    RiskFinding,
)
from .service import IntelligenceService

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


def get_intelligence_service(request: Request) -> IntelligenceService:
    service = getattr(request.app.state, "intelligence_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Intelligence service is not initialized.",
        )
    return service


def _require_project(service: IntelligenceService, project_id: str) -> None:
    try:
        service._require_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/intelligence/projects/{project_id}/identity", response_model=ProjectIdentity)
def project_identity(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> ProjectIdentity:
    _require_project(service, project_id)
    return service.identity_for(project_id)


@router.get("/intelligence/projects/{project_id}/fingerprint", response_model=RepositoryFingerprint)
def project_fingerprint(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> RepositoryFingerprint:
    _require_project(service, project_id)
    return service.fingerprint_for(project_id)


@router.get("/intelligence/projects/{project_id}/overview", response_model=ProjectOverview)
def project_overview(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> ProjectOverview:
    _require_project(service, project_id)
    return service.project_overview(project_id)


@router.get("/intelligence/projects/{project_id}/files")
def file_inventory(
    project_id: str,
    classification: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    service: IntelligenceService = Depends(get_intelligence_service),
) -> list:
    _require_project(service, project_id)
    records = service.file_inventory(project_id)
    if classification:
        records = tuple(r for r in records if r.classification == classification)
    return [r.model_dump() for r in records[:limit]]


@router.get("/intelligence/projects/{project_id}/dependencies", response_model=DependencyGraph)
def dependency_graph(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> DependencyGraph:
    _require_project(service, project_id)
    return service.dependency_graph(project_id)


@router.get("/intelligence/projects/{project_id}/architecture", response_model=ArchitectureGraph)
def architecture_graph(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> ArchitectureGraph:
    _require_project(service, project_id)
    return service.architecture_graph(project_id)


@router.get("/intelligence/projects/{project_id}/git", response_model=GitIntelligence)
def git_intelligence(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> GitIntelligence:
    _require_project(service, project_id)
    return service.git_intelligence(project_id)


@router.get("/intelligence/projects/{project_id}/impact")
def change_impact(
    project_id: str,
    target: str = Query(min_length=1),
    service: IntelligenceService = Depends(get_intelligence_service),
) -> list:
    _require_project(service, project_id)
    return [r.model_dump() for r in service.change_impact(project_id, target)]


@router.get("/intelligence/projects/{project_id}/risks")
def risk_findings(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> list:
    _require_project(service, project_id)
    return [r.model_dump() for r in service.risk_findings(project_id)]


@router.get("/intelligence/projects/{project_id}/decisions")
def decisions(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> list:
    _require_project(service, project_id)
    return [r.model_dump() for r in service.decisions(project_id)]


@router.get("/intelligence/projects/{project_id}/conventions")
def conventions(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> list:
    _require_project(service, project_id)
    return [r.model_dump() for r in service.conventions(project_id)]


@router.post("/intelligence/projects/{project_id}/memory", response_model=MemoryEntry)
def add_memory(
    project_id: str,
    entry: MemoryEntry,
    service: IntelligenceService = Depends(get_intelligence_service),
) -> MemoryEntry:
    _require_project(service, project_id)
    return service.add_memory(project_id, entry)


@router.put("/intelligence/projects/{project_id}/memory/{memory_id}/status", response_model=bool)
def update_memory(
    project_id: str,
    memory_id: str,
    payload: dict,
    service: IntelligenceService = Depends(get_intelligence_service),
) -> bool:
    _require_project(service, project_id)
    status_value = str(payload.get("status") or "")
    if status_value not in {"proposed", "review", "accepted", "corrected", "superseded"}:
        raise HTTPException(status_code=422, detail="Invalid memory status.")
    return service.update_memory(project_id, memory_id, status_value)


@router.get("/intelligence/projects/{project_id}/memory")
def memories(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> list:
    _require_project(service, project_id)
    return [r.model_dump() for r in service.memories(project_id)]


@router.get("/intelligence/projects/{project_id}/search", response_model=RetrievalEnvelope)
def search(
    project_id: str,
    q: str = Query(min_length=1),
    limit: int = Query(default=25, ge=1, le=200),
    service: IntelligenceService = Depends(get_intelligence_service),
) -> RetrievalEnvelope:
    _require_project(service, project_id)
    return service.search(project_id, q, limit=limit)


@router.post("/intelligence/projects/{project_id}/context-pack", response_model=ContextPack)
def build_context_pack(
    project_id: str,
    payload: dict,
    service: IntelligenceService = Depends(get_intelligence_service),
) -> ContextPack:
    _require_project(service, project_id)
    objective = str(payload.get("objective") or "")
    targets = payload.get("targets") or []
    if not objective or not isinstance(targets, list) or not targets:
        raise HTTPException(status_code=422, detail="Both 'objective' and a non-empty 'targets' list are required.")
    return service.context_pack(project_id, objective, tuple(str(t) for t in targets))


@router.post("/intelligence/projects/{project_id}/index", status_code=202)
def trigger_index(
    project_id: str,
    payload: dict = None,
    service: IntelligenceService = Depends(get_intelligence_service),
) -> dict:
    _require_project(service, project_id)
    incremental = bool((payload or {}).get("incremental", True))
    if incremental:
        service.trigger_incremental_index(project_id)
    else:
        service.trigger_full_index(project_id)
    return {"indexing": True, "incremental": incremental}


@router.post("/intelligence/projects/{project_id}/index/cancel", status_code=202)
def cancel_index(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> dict:
    _require_project(service, project_id)
    service.cancel_index(project_id)
    return {"cancelled": True}


@router.get("/intelligence/projects/{project_id}/index/health", response_model=IndexHealth)
def index_health(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> IndexHealth:
    _require_project(service, project_id)
    return service.index_health(project_id)


@router.get("/intelligence/projects/{project_id}/index/storage")
def storage_stats(project_id: str, service: IntelligenceService = Depends(get_intelligence_service)) -> dict:
    _require_project(service, project_id)
    return service.storage_stats(project_id)
