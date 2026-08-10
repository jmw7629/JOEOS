"""JoeOS Object Intelligence — activity, causality, impact, semantic status.

Builds on the Enterprise Object System (server/objects) to turn the object
graph into an intelligent graph:

- Object activity timeline (human-facing, from authoritative events)
- Semantic status (state + meaning + impact + next step)
- Capability explanation (available / blocked by policy / permission / state /
  dependency / health / requires approval)
- Impact analysis (who depends on this object)
- Causal "Why?" context (structured evidence for Joe)

Nothing here invents structure: the graph, events, and state come from the
authoritative domain services already wired into the ObjectResolver.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional

from server.objects.core import ObjectRef, normalize_object_type, safety_level
from server.objects.resolver import ObjectResolver


# ---------------------------------------------------------------------------
# Semantic status
# ---------------------------------------------------------------------------

_SEMANTIC_STATUS: Dict[str, Dict[str, Any]] = {
    "healthy": {"label": "Healthy", "meaning": "Operating normally.", "impact": "No impact.", "next": "None required.", "tone": "success"},
    "degraded": {"label": "Degraded", "meaning": "Partially available or reduced performance.", "impact": "Eligible work may fall back to alternatives.", "next": "Review health and restore full availability.", "tone": "warning"},
    "blocked": {"label": "Blocked", "meaning": "Cannot make progress on the current objective.", "impact": "Downstream work waits.", "next": "Resolve the blocking dependency or wait for approval.", "tone": "danger"},
    "waiting": {"label": "Waiting", "meaning": "Awaiting a human decision or external input.", "impact": "No work advances until resolved.", "next": "Review the pending decision.", "tone": "warning"},
    "offline": {"label": "Offline", "meaning": "Not reachable or not running.", "impact": "Dependent work cannot use it.", "next": "Restore the service or route to an alternative.", "tone": "danger"},
    "failed": {"label": "Failed", "meaning": "The last operation did not succeed.", "impact": "The failed operation must be retried or reworked.", "next": "Inspect the failure cause and retry.", "tone": "danger"},
    "recovering": {"label": "Recovering", "meaning": "Returning to normal after a disturbance.", "impact": "Limited availability during recovery.", "next": "Allow recovery to complete.", "tone": "warning"},
    "running": {"label": "Running", "meaning": "Actively executing work.", "impact": "Work is progressing.", "next": "None required.", "tone": "success"},
    "enabled": {"label": "Enabled", "meaning": "Active and eligible to run.", "impact": "May trigger on schedule.", "next": "None required.", "tone": "success"},
    "disabled": {"label": "Disabled", "meaning": "Not eligible to run.", "impact": "Scheduled work does not trigger.", "next": "Enable when appropriate.", "tone": "warning"},
    "installed": {"label": "Installed", "meaning": "Present and ready for use.", "impact": "Eligible for assignment.", "next": "None required.", "tone": "success"},
    "available": {"label": "Available", "meaning": "Ready to be used.", "impact": "Eligible for assignment.", "next": "None required.", "tone": "success"},
    "idle": {"label": "Idle", "meaning": "Present but not working.", "impact": "No active load.", "next": "None required.", "tone": "success"},
    "succeeded": {"label": "Succeeded", "meaning": "Completed successfully.", "impact": "No open work.", "next": "None required.", "tone": "success"},
    "completed": {"label": "Completed", "meaning": "Finished.", "impact": "No open work.", "next": "None required.", "tone": "success"},
    "cancelled": {"label": "Cancelled", "meaning": "Stopped before completion.", "impact": "Work is not advancing.", "next": "Restart if still needed.", "tone": "warning"},
    "unknown": {"label": "Unknown", "meaning": "No current state is recorded.", "impact": "Unknown.", "next": "Check the object directly.", "tone": "default"},
    "attention": {"label": "Needs attention", "meaning": "Requires a human decision.", "impact": "Work is waiting on you.", "next": "Review and decide.", "tone": "danger"},
    "retired": {"label": "Retired", "meaning": "Decommissioned.", "impact": "No active work.", "next": "None required.", "tone": "default"},
    "error": {"label": "Error", "meaning": "The last operation errored.", "impact": "The error must be resolved.", "next": "Inspect and resolve.", "tone": "danger"},
}

_STATUS_NORMALIZATION = {
    "ok": "healthy",
    "healthy": "healthy",
    "good": "healthy",
    "degraded": "degraded",
    "warning": "degraded",
    "unhealthy": "degraded",
    "blocked": "blocked",
    "waiting": "waiting",
    "pending": "waiting",
    "offline": "offline",
    "unavailable": "offline",
    "down": "offline",
    "failed": "failed",
    "error": "error",
    "recovering": "recovering",
    "running": "running",
    "active": "running",
    "working": "running",
    "busy": "running",
    "in_progress": "running",
    "enabled": "enabled",
    "disabled": "disabled",
    "paused": "disabled",
    "installed": "installed",
    "available": "available",
    "idle": "idle",
    "succeeded": "succeeded",
    "completed": "completed",
    "done": "completed",
    "success": "healthy",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "retired": "retired",
    "locked": "attention",
    "lockdown": "attention",
}


def semantic_status(raw: Any) -> Dict[str, Any]:
    """Normalize a raw state string into a human semantic status.

    Type-specific vocabulary is mapped to the shared semantic vocabulary where
    the meaning is the same; the raw value is preserved as ``raw``.
    """
    value = str(raw or "").strip().lower()
    normalized = _STATUS_NORMALIZATION.get(value)
    if normalized is None:
        # HTTP/status-code interpretation: 2xx healthy, 4xx error, 5xx error.
        code_match = re.search(r"(^|\s)([1-5][0-9]{2})(\s|$)", value)
        if code_match:
            code = int(code_match.group(2))
            if 200 <= code < 300:
                normalized = "healthy"
            elif code in (401, 403):
                normalized = "waiting" if code == 401 else "blocked"
            else:
                normalized = "error"
        # Try looser matches (e.g. "not_started", "in_review").
        if normalized is None:
            if "fail" in value or "err" in value:
                normalized = "failed" if "fail" in value else "error"
            elif "block" in value:
                normalized = "blocked"
            elif "wait" in value or "pending" in value or "queue" in value:
                normalized = "waiting"
            elif "not_start" in value or "todo" in value or "planned" in value:
                normalized = "waiting"
            elif "review" in value or "approval" in value:
                normalized = "waiting"
            elif "offline" in value or "unavail" in value:
                normalized = "offline"
            elif "degrad" in value:
                normalized = "degraded"
            elif "recover" in value:
                normalized = "recovering"
            elif "complet" in value or "done" in value or "succeed" in value:
                normalized = "completed" if "complet" in value else "succeeded"
            elif "cancel" in value:
                normalized = "cancelled"
            else:
                normalized = "unknown"
    entry = _SEMANTIC_STATUS.get(normalized, _SEMANTIC_STATUS["unknown"])
    return {"raw": str(raw or ""), "state": normalized, **entry}


# ---------------------------------------------------------------------------
# Capability explanation
# ---------------------------------------------------------------------------

_CAPABILITY_LABEL: Dict[str, str] = {
    "view": "View", "inspect": "Inspect", "edit": "Edit", "execute": "Execute",
    "approve": "Approve", "reject": "Reject", "archive": "Archive", "restore": "Restore",
    "duplicate": "Duplicate", "move": "Move", "link": "Link", "comment": "Comment",
    "share": "Share", "export": "Export", "compare": "Compare", "version": "Version",
    "rollback": "Rollback", "automate": "Automate", "schedule": "Schedule",
    "attach": "Attach", "search": "Search", "ask_joe": "Ask Joe",
}


def capability_reason(capability: str, *, available: bool = True, reason: str = "") -> Dict[str, Any]:
    """Explain a capability's availability with a machine + human reason."""
    label = _CAPABILITY_LABEL.get(capability, capability.replace("_", " ").title())
    status = "available" if available else "unavailable"
    if not reason:
        if available:
            reason = "Available."
        else:
            reason = "Unavailable."
    return {"capability": capability, "label": label, "status": status, "reason": reason}


# ---------------------------------------------------------------------------
# Object activity timeline (persistent, object-centric)
# ---------------------------------------------------------------------------

class ObjectActivityStore:
    """Authoritative object-centric activity timeline.

    Domain services (and the backend event sink) record normalized activity
    entries here. The timeline is human-facing; raw audit stays in the audit
    store. Entries reference ObjectRefs so related objects are traversable.
    """

    _MAX_ROWS = 4000

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]):
        self._connection_factory = connection_factory
        self._prepare()

    def _prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS object_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    result TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    related_type TEXT,
                    related_id TEXT,
                    provenance TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_obj_act_obj ON object_activity(object_type, object_id, id)"
            )

    def record(
        self,
        *,
        actor: str,
        action: str,
        object_type: str,
        object_id: str,
        result: str = "ok",
        detail: str = "",
        related_type: Optional[str] = None,
        related_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        recorded_at: Optional[str] = None,
    ) -> None:
        from server.objects.intelligence import _now  # local import to avoid cycle
        stamp = recorded_at or _now()
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO object_activity
                    (recorded_at, actor, action, object_type, object_id, result, detail, related_type, related_id, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (stamp, str(actor)[:80], str(action)[:40], str(object_type)[:40], str(object_id)[:120],
                 str(result)[:20], str(detail)[:500], (str(related_type)[:40] if related_type else None),
                 (str(related_id)[:120] if related_id else None),
                 json_dumps(provenance) if provenance else None),
            )
            connection.execute(
                "DELETE FROM object_activity WHERE id NOT IN (SELECT id FROM object_activity ORDER BY id DESC LIMIT ?)",
                (self._MAX_ROWS,),
            )

    def for_object(self, object_type: str, object_id: str, limit: int = 40) -> List[Dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT recorded_at, actor, action, object_type, object_id, result, detail, related_type, related_id, provenance
                FROM object_activity
                WHERE object_type = ? AND object_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (object_type, object_id, max(1, min(200, int(limit)))),
            ).fetchall()
        return [
            {
                "recorded_at": row[0],
                "actor": row[1],
                "action": row[2],
                "object_type": row[3],
                "object_id": row[4],
                "result": row[5],
                "detail": row[6],
                "related": ({"object_type": row[7], "object_id": row[8]} if row[7] and row[8] else None),
                "provenance": json_loads(row[9]) if row[9] else None,
            }
            for row in rows
        ]

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT recorded_at, actor, action, object_type, object_id, result, detail, related_type, related_id
                FROM object_activity ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [
            {
                "recorded_at": row[0],
                "actor": row[1],
                "action": row[2],
                "object": {"object_type": row[3], "object_id": row[4]},
                "result": row[5],
                "detail": row[6],
                "related": ({"object_type": row[7], "object_id": row[8]} if row[7] and row[8] else None),
            }
            for row in rows
        ]


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    import json
    return json.dumps(value, default=str)


def json_loads(value: str) -> Any:
    import json
    try:
        return json.loads(value)
    except Exception:
        return None


Callable = Callable  # typing alias used in annotations


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------

_RELATIONSHIP_WEIGHT = {
    "depends_on": 1.0,
    "blocked_by": 1.0,
    "uses": 0.9,
    "uses_model": 0.9,
    "uses_provider": 0.9,
    "runs_on": 0.85,
    "hosted_by": 0.7,
    "produced_by": 0.7,
    "generated": 0.7,
    "assigned_to": 0.8,
    "part_of": 0.8,
    "belongs_to": 0.8,
    "requires_approval": 0.75,
    "schedules": 0.6,
    "scheduled_by": 0.6,
    "contains": 0.6,
    "references": 0.5,
    "created": 0.5,
    "created_by": 0.5,
    "related_to": 0.3,
    "approves": 0.3,
    "executed_by": 0.4,
    "managed_by": 0.3,
}


def relationship_weight(relation: str) -> float:
    return _RELATIONSHIP_WEIGHT.get(str(relation or "").lower(), 0.4)


def rank_relationships(relationships: List[Dict[str, Any]], object_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rank relationships by likely importance to the user.

    Deterministic rules over authoritative data:
      - relationships from an object in a degraded/failed/blocked state rank
        higher (they explain why and what it affects);
      - relationships to objects that appear unhealthy rank higher;
      - typed weights break ties.
    """
    current_state = str(object_status or "").lower()
    elevated = any(key in current_state for key in ("degrad", "fail", "error", "block", "offline", "unavail", "attention", "wait"))

    def score(entry: Dict[str, Any]) -> float:
        weight = relationship_weight(entry.get("relation"))
        score_value = weight
        target = entry.get("object") or {}
        target_status = str(target.get("status") or target.get("lifecycle_state") or "").lower()
        if elevated:
            score_value += 0.5
        if any(key in target_status for key in ("degrad", "fail", "error", "block", "offline", "unavail", "attention")):
            score_value += 0.3
        return score_value

    ranked = sorted(relationships, key=score, reverse=True)
    for index, entry in enumerate(ranked):
        entry["importance"] = max(1, min(5, round(score(entry) * 2)))
    return ranked
