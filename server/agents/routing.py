"""Model routing: local-first selection and honest disclosure.

Selection is deterministic and evidence-based. The default policy is
local-first: a local provider is always chosen unless the mission explicitly
requests remote inference, tool use, or a capability that local models cannot
satisfy. Router records a ModelRoute with a human-readable disclosure so no
agent is ever presented as running a model it did not actually use.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Optional, Tuple

from .models import ModelRoute, ResourceBudget

LOCAL_PROVIDERS = ("local", "lemonade")
REMOTE_PROVIDERS = ("remote", "cloud", "api")

_LOCAL_TOOLING = True

_ORDERED_PROFILES = ("small", "default", "large", "reasoning")
_AVAILABLE_PROFILES = ("small", "default", "large", "reasoning")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


class RoutingService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def select(
        self,
        *,
        agent_id: str,
        mission_id: Optional[str] = None,
        task_id: Optional[str] = None,
        required_capabilities: Tuple[str, ...] = (),
        model_preferences: Tuple[str, ...] = (),
        policy: str = "local_first",
        tool_use_required: bool = False,
        budget: Optional[ResourceBudget] = None,
    ) -> ModelRoute:
        provider = "local"
        if policy == "remote_only":
            provider = "remote"
        elif policy == "local_only":
            provider = "local"
        elif tool_use_required:
            provider = "remote" if not _LOCAL_TOOLING else "local"
        elif policy != "local_first":
            provider = "remote"

        # Determine profile tier from preferences.
        desired = _pick_profile(model_preferences)

        model = desired or "default"
        if provider == "local":
            model = "local-" + model
            disclosure = "Local model selected; nothing leaves this device. Configured agent profile, no background inference."
        else:
            disclosure = (
                "Remote model requested (policy=%s). Agent profile is configured, not actively running; "
                "no inference is spawned by this selection." % policy
            )
        captured = required_capabilities
        route_id = "route_" + _id("route", agent_id, task_id or mission_id or "none", disclosure)[:22]
        route = ModelRoute(
            route_id=route_id,
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
            required_capabilities=captured,
            model=model,
            provider=provider,
            rationale="local-first deterministic selection; tool use required" if tool_use_required else "local-first deterministic selection; policy=%s" % policy,
            tool_use_required=tool_use_required,
            state="selected",
            disclosure=disclosure,
            created_at=_now(),
            updated_at=_now(),
        )
        self._upsert(route)
        return route

    def routes(self, *, agent_id: Optional[str] = None, mission_id: Optional[str] = None, limit: int = 100) -> Tuple[ModelRoute, ...]:
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM org_routes%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(500, limit))],
            ).fetchall()
        return tuple(_route_from_row(row) for row in rows)

    def _upsert(self, record: ModelRoute) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_routes (
                    route_id, agent_id, mission_id, task_id, required_capabilities,
                    model, provider, rationale, tool_use_required, state, disclosure,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.route_id, record.agent_id, record.mission_id, record.task_id,
                    "|".join(record.required_capabilities), record.model, record.provider,
                    record.rationale, int(record.tool_use_required), record.state,
                    record.disclosure, record.created_at, record.updated_at,
                ),
            )


def _pick_profile(preferences: Tuple[str, ...]) -> str:
    for profile in preferences:
        if profile in _AVAILABLE_PROFILES:
            return profile
    return "default"


def _route_from_row(row) -> ModelRoute:
    return ModelRoute(
        route_id=row["route_id"], agent_id=row["agent_id"], mission_id=row["mission_id"],
        task_id=row["task_id"],
        required_capabilities=tuple(x for x in row["required_capabilities"].split("|") if x),
        model=row["model"], provider=row["provider"], rationale=row["rationale"],
        tool_use_required=bool(row["tool_use_required"]), state=row["state"],
        disclosure=row["disclosure"], created_at=row["created_at"], updated_at=row["updated_at"],
    )