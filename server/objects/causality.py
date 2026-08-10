"""JoeOS Object Causality — structured "Why?" evidence for Joe.

Builds a bounded, structured explanation context from an ObjectRef using real
authoritative state: current status, recent activity, typed relationships,
dependency health, and capability/policy constraints.

Joe turns this structured evidence into a human explanation. Joe never invents
the graph — this resolver is the ground truth it reasons over.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from server.objects.core import ObjectRef, normalize_object_type
from server.objects.intelligence import semantic_status
from server.objects.resolver import ObjectResolver

# Reason categories a causal explanation may conclude with.
CATEGORY_FAILURE = "failure"
CATEGORY_BLOCKED = "blocked"
CATEGORY_DEGRADED = "degraded"
CATEGORY_WAITING = "waiting"
CATEGORY_POLICY = "policy"
CATEGORY_PERMISSION = "permission"
CATEGORY_DEPENDENCY = "dependency"
CATEGORY_HEALTH = "health"
CATEGORY_APPROVAL = "approval"
CATEGORY_OK = "ok"


class CausalResolver:
    """Deterministic causal context for an object, server-side."""

    def __init__(self, resolver: ObjectResolver, activity_store) -> None:
        self._resolver = resolver
        self._activity = activity_store

    def explain(self, ref: ObjectRef, principal: Dict[str, Any], *, limit_activity: int = 12) -> Dict[str, Any]:
        kind = normalize_object_type(ref.object_type)
        if not kind:
            return {"object": ref.to_dict(), "explanation": "Unknown object type.", "category": "unknown", "evidence": []}
        summary = self._resolver.resolve(ref, principal)
        if summary is None:
            return {
                "object": ref.to_dict(),
                "explanation": "This object is not accessible to the current user.",
                "category": CATEGORY_PERMISSION,
                "evidence": [],
            }
        status = semantic_status(summary.get("status"))
        relationships = self._resolver.relationships(ref, principal)
        activity = self._activity.for_object(ref.object_type, ref.object_id, limit=limit_activity)

        evidence: List[Dict[str, Any]] = []
        evidence.append({
            "kind": "state",
            "label": "Current state",
            "detail": status["label"] + " — " + status["meaning"],
            "object": ref.to_dict(),
        })

        # Causal signals from relationships (authoritative).
        health_edges = []
        dependency_edges = []
        approval_edges = []
        for rel in relationships:
            relation = str(rel.get("relation") or "").lower()
            target = rel.get("object") or {}
            if relation in ("depends_on", "uses", "uses_model", "uses_provider", "runs_on", "hosted_by", "part_of", "assigned_to"):
                dependency_edges.append(rel)
                target_status = str(target.get("status") or target.get("lifecycle_state") or "").lower()
                if any(k in target_status for k in ("degrad", "fail", "error", "block", "offline", "unavail", "attention")):
                    health_edges.append(rel)
            if "approval" in relation or relation in ("requires_approval", "approves"):
                approval_edges.append(rel)

        if health_edges:
            evidence.append({
                "kind": "dependency_health",
                "label": "Unhealthy dependency",
                "detail": "A dependency this object relies on is degraded or failed.",
                "objects": [e.get("object") for e in health_edges[:5]],
            })
        if dependency_edges:
            evidence.append({
                "kind": "dependencies",
                "label": "Dependencies",
                "detail": "The objects this relies on.",
                "objects": [e.get("object") for e in dependency_edges[:8]],
            })
        if approval_edges:
            evidence.append({
                "kind": "approval",
                "label": "Approval relationship",
                "detail": "This work is coupled to an approval.",
                "objects": [e.get("object") for e in approval_edges[:5]],
            })

        # Activity as history.
        if activity:
            evidence.append({
                "kind": "history",
                "label": "Recent activity",
                "detail": "The most recent events on this object.",
                "activity": activity[:6],
            })

        # Build a grounded conclusion.
        category, explanation = self._conclude(summary, status, health_edges, approval_edges, activity)
        return {
            "object": ref.to_dict(),
            "category": category,
            "explanation": explanation,
            "semantic_status": status,
            "evidence": evidence,
        }

    def _conclude(self, summary: Dict[str, Any], status: Dict[str, Any], health_edges: List[Dict[str, Any]], approval_edges: List[Dict[str, Any]], activity: List[Dict[str, Any]]) -> Any:
        state = status["state"]
        name = summary.get("name") or summary.get("object", {}).get("object_id", "this object")
        if health_edges:
            targets = ", ".join(
                str((e.get("object") or {}).get("display_hint") or (e.get("object") or {}).get("object_id") or "a dependency")
                for e in health_edges[:3]
            )
            return CATEGORY_DEPENDENCY, f"{name} is {state} because a dependency it relies on is unhealthy: {targets}."
        if approval_edges:
            return CATEGORY_APPROVAL, f"{name} is {state} because it is coupled to an approval that needs a decision."
        if state in ("failed", "error", "blocked"):
            return CATEGORY_FAILURE, f"{name} is {state}. Inspect the recent activity for the failure cause."
        if state in ("degraded", "offline", "unavailable"):
            return CATEGORY_HEALTH, f"{name} is {state}. Its health or availability is reduced."
        if state in ("waiting", "pending", "attention"):
            return CATEGORY_WAITING, f"{name} is {state} — it is waiting for a decision or external input."
        return CATEGORY_OK, f"{name} is {state}. No causal problem is currently detected."
