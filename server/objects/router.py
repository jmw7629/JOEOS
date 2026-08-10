"""JoeOS Object API.

Canonical object addressing and authorized inspection:

    GET /api/v1/objects/types
    GET /api/v1/objects/{object_type}/{object_id}
    GET /api/v1/objects/{object_type}/{object_id}/relationships

Resolution enforces the authenticated principal through existing domain
services. Merely knowing an object id never grants access. The browser must
pass a real application session.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from server.identity.authority_router import require_application_session
from server.objects.core import OBJECT_TYPES, ObjectRef, normalize_object_type, safety_gate
from server.objects.resolver import ObjectResolver

router = APIRouter(prefix="/api/v1/objects", tags=["objects"])

# Lazily bound resolver; the backend wires domain hooks at startup.
RESOLVER = ObjectResolver()


def _session(request: Request, principal: Dict[str, Any] = Depends(require_application_session)) -> Dict[str, Any]:
    return principal


@router.get("/types")
def object_types(principal: Dict[str, Any] = Depends(_session)) -> Dict[str, Any]:
    """Registry of canonical Enterprise Object types."""
    return {"types": sorted(OBJECT_TYPES), "count": len(OBJECT_TYPES)}


@router.get("/{object_type}/{object_id}")
def get_object(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Resolve an authorized object summary for a canonical ObjectRef."""
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    return summary


@router.get("/{object_type}/{object_id}/relationships")
def get_object_relationships(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Return typed, authorized relationships from an object."""
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    return {"object": ref.to_dict(), "relationships": RESOLVER.relationships(ref, principal)}


@router.get("/{object_type}/{object_id}/actions")
def get_object_actions(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Return the safety-classified, permitted actions for an object.

    Each capability maps to a uniform safety level and gate so the UI never
    guesses how consequential an action is. Safe actions may run immediately;
    consequential ones must preview; privileged ones require approval;
    destructive ones are heavily protected.
    """
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    safety = summary.get("action_safety", {})
    actions = [safety_gate(cap) for cap in safety]
    actions.sort(key=lambda a: {"safe": 0, "consequential": 1, "privileged": 2, "destructive": 3}.get(a["level"], 3))
    return {"object": ref.to_dict(), "actions": actions}
