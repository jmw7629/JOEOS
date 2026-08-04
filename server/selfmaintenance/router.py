"""Self-Maintenance and Continuous Improvement REST API.

Read-only discovery of real maintenance checks, evidence-based improvement
proposals, and the maintenance log. Triggering a maintenance pass and applying
an improvement are governance-gated mutations that never fabricate success.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/v1/selfmaintenance", tags=["selfmaintenance"])


def _get_service(request: Request):
    service = getattr(request.app.state, "selfmaintenance_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Self-maintenance platform is unavailable.")
    return service


def _governance(service) -> tuple:
    blocked = getattr(service, "governance_blocked", None)
    if blocked is None:
        return (False, "")
    return blocked()


@router.get("/overview")
def overview(request: Request) -> Dict[str, Any]:
    return _get_service(request).overview()


@router.post("/run")
def run_maintenance(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    try:
        return service.run_maintenance()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/improvements/{improvement_id}/apply")
def apply_improvement(request: Request, improvement_id: str, payload: Optional[dict] = None) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    payload = payload or {}
    desired_state = str(payload.get("state") or "").strip()
    if desired_state == "dismissed":
        proposal = next((p for p in service.proposals() if p.improvement_id == improvement_id), None)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Improvement not found.")
        service.coordinator.registry.set_state(improvement_id, "dismissed")
        service.coordinator.append_log("info", "improvement", "Dismissed %s." % improvement_id)
        return {"improvement_id": improvement_id, "state": "dismissed", "applied": False}
    if desired_state:
        raise HTTPException(status_code=400, detail="Unsupported state: %s" % desired_state)
    try:
        ok, detail = service.apply_improvement(improvement_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=409, detail=detail)
    return {"improvement_id": improvement_id, "applied": True, "detail": detail}