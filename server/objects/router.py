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

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from server.identity.authority_router import require_application_session
from server.objects.core import (
    OBJECT_TYPES,
    ObjectRef,
    capabilities_for,
    normalize_object_type,
    safety_for_capability,
    safety_gate,
)
from server.objects.causality import CausalResolver
from server.objects.intelligence import ObjectActivityStore, rank_relationships
from server.objects.resolver import ObjectResolver

router = APIRouter(prefix="/api/v1/objects", tags=["objects"])

# Lazily bound resolver; the backend wires domain hooks at startup.
RESOLVER = ObjectResolver()

# Lazily bound intelligence components; the backend wires the store + causal
# resolver at startup so domain services can record object activity.
ACTIVITY_STORE: Optional[ObjectActivityStore] = None
CAUSAL_RESOLVER: Optional[CausalResolver] = None


def _session(request: Request, principal: Dict[str, Any] = Depends(require_application_session)) -> Dict[str, Any]:
    return principal


@router.get("/types")
def object_types(principal: Dict[str, Any] = Depends(_session)) -> Dict[str, Any]:
    """Registry of canonical Enterprise Object types."""
    return {"types": sorted(OBJECT_TYPES), "count": len(OBJECT_TYPES)}


@router.get("/compare")
def compare_objects_endpoint(
    left_type: str,
    left_id: str,
    right_type: str,
    right_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Type-aware comparison of two compatible Enterprise Objects.

    Shows meaningful type-specific differences (never generic JSON): models
    compare health/provider; providers compare availability/privacy/streaming;
    agents compare role/capabilities/availability. Objects of different types
    are not comparable.
    """
    from server.objects.compare import compare_objects

    left = ObjectRef(object_id=left_id, object_type=left_type)
    right = ObjectRef(object_id=right_id, object_type=right_type)
    return compare_objects(left, right, RESOLVER, principal)


@router.get("/{object_type}/help")
def object_type_help(object_type: str, principal: Dict[str, Any] = Depends(_session)) -> Dict[str, Any]:
    """Self-describing metadata: what an object type is and what you can do with it.

    Lets Joe teach the user 'what can I do here' from the actual registered
    capabilities — never from generic documentation.
    """
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    capabilities = sorted(capabilities_for(kind))
    return {
        "object_type": kind,
        "capabilities": capabilities,
        "actions": [safety_gate(cap) for cap in capabilities],
        "action_safety": {cap: safety_for_capability(cap) for cap in capabilities},
    }


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
    relationships = RESOLVER.relationships(ref, principal)
    ranked = rank_relationships(relationships, summary.get("status"))
    return {"object": ref.to_dict(), "relationships": ranked}


@router.get("/{object_type}/{object_id}/activity")
def get_object_activity(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Human-facing activity timeline for an object (normalized events).

    Raw audit stays in the audit store; this returns semantic, object-centric
    history with traversable related objects.
    """
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    if ACTIVITY_STORE is None:
        return {"object": ref.to_dict(), "activity": []}
    return {"object": ref.to_dict(), "activity": ACTIVITY_STORE.for_object(kind, object_id)}


@router.get("/{object_type}/{object_id}/impact")
def get_object_impact(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Dependency impact analysis: who/what depends on this object.

    Uses authoritative reverse-dependency relationships. Only objects the
    principal may access are returned; no protected identity leaks via counts.
    """
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    impacted = RESOLVER.impact(ref, principal)
    by_type: Dict[str, int] = {}
    for entry in impacted:
        target = entry.get("object") or {}
        t = target.get("object_type") or "object"
        by_type[t] = by_type.get(t, 0) + 1
    return {"object": ref.to_dict(), "impact": impacted, "counts": by_type, "total": len(impacted)}


@router.get("/{object_type}/{object_id}/why")
def get_object_why(
    object_type: str,
    object_id: str,
    principal: Dict[str, Any] = Depends(_session),
) -> Dict[str, Any]:
    """Structured causal context for Joe: 'Why?'.

    Returns grounded evidence (state, dependency health, approval coupling,
    recent activity) and a deterministic conclusion. Joe turns the evidence
    into a human explanation; it never invents the graph.
    """
    kind = normalize_object_type(object_type)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown object type")
    ref = ObjectRef(object_id=object_id, object_type=kind)
    if CAUSAL_RESOLVER is not None:
        return CAUSAL_RESOLVER.explain(ref, principal)
    # Fallback: deterministic conclusion from the summary alone.
    summary = RESOLVER.resolve(ref, principal)
    if summary is None:
        raise HTTPException(status_code=404, detail="Object not found or not accessible")
    from server.objects.intelligence import semantic_status
    status = semantic_status(summary.get("status"))
    return {
        "object": ref.to_dict(),
        "category": status["state"],
        "explanation": "{name} is {state}. {meaning}".format(name=summary.get("name", "This object"), state=status["label"], meaning=status["meaning"]),
        "semantic_status": status,
        "evidence": [{"kind": "state", "label": "Current state", "detail": status["label"] + " — " + status["meaning"], "object": ref.to_dict()}],
    }


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
