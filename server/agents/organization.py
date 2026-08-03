"""Organization Registry: organization, units, roles, and agent identity.

Configured agents are profiles; they are never presented as actively running
intelligence. Permissions are never granted by unit or role name alone; the
authoritative security and Tool Broker systems enforce actual authority.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import (
    AgentProfile,
    CapabilityAssignment,
    OrganizationRecord,
    OrganizationalUnit,
    RoleDefinition,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


class OrganizationService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    # ---- organization ----

    def get_organization(self) -> Optional[OrganizationRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM org_organization LIMIT 1"
            ).fetchone()
        return _organization_from_row(row) if row else None

    def create_organization(self, name: str, purpose: str = "") -> OrganizationRecord:
        existing = self.get_organization()
        if existing is not None:
            return existing
        now = _now()
        organization_id = "org_" + _id("organization", name)[:22]
        record = OrganizationRecord(
            organization_id=organization_id,
            name=name,
            purpose=purpose,
            created_at=now,
            updated_at=now,
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO org_organization (
                    organization_id, name, purpose, owner, mode, state,
                    default_escalation_path, default_review_policy, policy_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id, name, purpose, "user", "personal", "enabled",
                    "user", "independent_when_high_risk", 1, now, now,
                ),
            )
        return record

    def get_or_create_organization(self) -> OrganizationRecord:
        existing = self.get_organization()
        if existing is not None:
            return existing
        return self.create_organization("JoeOS AI Organization")

    # ---- units ----

    def create_unit(self, name: str, unit_type: str, purpose: str = "", parent_unit: Optional[str] = None, leader: Optional[str] = None) -> OrganizationalUnit:
        now = _now()
        record = OrganizationalUnit(
            unit_id="unit_" + _id("unit", name)[:22],
            unit_type=unit_type,
            name=name,
            purpose=purpose,
            parent_unit=parent_unit,
            leader=leader,
            created_at=now,
            updated_at=now,
        )
        self._upsert_unit(record)
        return record

    def _upsert_unit(self, record: OrganizationalUnit) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_units (
                    unit_id, unit_type, name, purpose, parent_unit, leader,
                    capabilities, escalation_target, supported_mission_types,
                    enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.unit_id, record.unit_type, record.name, record.purpose,
                    record.parent_unit, record.leader, "|".join(record.capabilities),
                    record.escalation_target, "|".join(record.supported_mission_types),
                    int(record.enabled), record.created_at, record.updated_at,
                ),
            )

    def units(self) -> Tuple[OrganizationalUnit, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_units ORDER BY name").fetchall()
        return tuple(_unit_from_row(row) for row in rows)

    def unit(self, unit_id: str) -> Optional[OrganizationalUnit]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_units WHERE unit_id = ?", (unit_id,)).fetchone()
        return _unit_from_row(row) if row else None

    def set_unit_enabled(self, unit_id: str, enabled: bool) -> Optional[OrganizationalUnit]:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE org_units SET enabled = ?, updated_at = ? WHERE unit_id = ?",
                (int(enabled), _now(), unit_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.unit(unit_id)

    # ---- roles ----

    def create_role(self, title: str, required_capabilities: Tuple[str, ...] = (), purpose: str = "") -> RoleDefinition:
        now = _now()
        record = RoleDefinition(
            role_id="role_" + _id("role", title)[:22],
            title=title,
            purpose=purpose,
            required_capabilities=required_capabilities,
            created_at=now,
            updated_at=now,
        )
        self._upsert_role(record)
        return record

    def _upsert_role(self, record: RoleDefinition) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_roles (
                    role_id, title, purpose, responsibilities, required_capabilities,
                    preferred_capabilities, allowed_workload_classes,
                    preferred_model_profile, allowed_tools, prohibited_tools,
                    required_review_relationships, escalation_path,
                    maximum_delegation_depth, default_task_limits, quality_criteria,
                    memory_access_policy, privacy_restrictions, role_version, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.role_id, record.title, record.purpose,
                    "|".join(record.responsibilities), "|".join(record.required_capabilities),
                    "|".join(record.preferred_capabilities),
                    "|".join(record.allowed_workload_classes),
                    record.preferred_model_profile, "|".join(record.allowed_tools),
                    "|".join(record.prohibited_tools),
                    "|".join(record.required_review_relationships), record.escalation_path,
                    record.maximum_delegation_depth, "|".join(record.default_task_limits),
                    "|".join(record.quality_criteria), record.memory_access_policy,
                    "|".join(record.privacy_restrictions), record.role_version,
                    int(record.enabled), record.created_at, record.updated_at,
                ),
            )

    def roles(self) -> Tuple[RoleDefinition, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_roles ORDER BY title").fetchall()
        return tuple(_role_from_row(row) for row in rows)

    def role(self, role_id: str) -> Optional[RoleDefinition]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_roles WHERE role_id = ?", (role_id,)).fetchone()
        return _role_from_row(row) if row else None

    # ---- agents ----

    def create_agent(self, name: str, role_id: str, department: Optional[str] = None, team: Optional[str] = None) -> AgentProfile:
        now = _now()
        record = AgentProfile(
            agent_id="agent_" + _id("agent", name)[:22],
            display_name=name,
            role_id=role_id,
            department=department,
            team=team,
            created_at=now,
            updated_at=now,
        )
        self._upsert_agent(record)
        return record

    def _upsert_agent(self, record: AgentProfile) -> None:
        skills = "¬".join(_serialize_skill(s) for s in record.skills)
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_agents (
                    agent_id, display_name, role_id, department, team, status,
                    availability, capabilities, skills, model_preferences,
                    runtime_restrictions, tool_permissions, project_restrictions,
                    privacy_restrictions, memory_scope, maximum_workload,
                    current_mission, current_task, queue_depth, reliability_state,
                    config_version, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.agent_id, record.display_name, record.role_id,
                    record.department, record.team, record.status, record.availability,
                    "|".join(record.capabilities), skills,
                    "|".join(record.model_preferences),
                    "|".join(record.runtime_restrictions),
                    "|".join(record.tool_permissions),
                    "|".join(record.project_restrictions),
                    "|".join(record.privacy_restrictions), record.memory_scope,
                    record.maximum_workload, record.current_mission, record.current_task,
                    record.queue_depth, record.reliability_state, record.config_version,
                    int(record.enabled), record.created_at, record.updated_at,
                ),
            )

    def agents(self, *, include_inactive: bool = False) -> Tuple[AgentProfile, ...]:
        with self._connection_factory() as connection:
            if include_inactive:
                rows = connection.execute("SELECT * FROM org_agents ORDER BY display_name").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM org_agents WHERE enabled = 1 ORDER BY display_name"
                ).fetchall()
        return tuple(_agent_from_row(row) for row in rows)

    def agent(self, agent_id: str) -> Optional[AgentProfile]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return _agent_from_row(row) if row else None

    def update_agent_state(self, agent_id: str, *, status: Optional[str] = None, availability: Optional[str] = None, mission_id: Optional[str] = None, task_id: Optional[str] = None) -> Optional[AgentProfile]:
        current = self.agent(agent_id)
        if current is None:
            return None
        now = _now()
        updates = {
            "status": status if status is not None else current.status,
            "availability": availability if availability is not None else current.availability,
            "current_mission": mission_id if mission_id is not None else current.current_mission,
            "current_task": task_id if task_id is not None else current.current_task,
            "updated_at": now,
        }
        updated = current.model_copy(update=updates)
        self._upsert_agent(updated)
        return updated

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> Optional[AgentProfile]:
        current = self.agent(agent_id)
        if current is None:
            return None
        now = _now()
        updated = current.model_copy(
            update={"enabled": enabled, "availability": "offline" if not enabled else current.availability, "updated_at": now}
        )
        self._upsert_agent(updated)
        return updated

    def require_agent(self, agent_id: str) -> AgentProfile:
        record = self.agent(agent_id)
        if record is None:
            raise KeyError("agent not found: %s" % agent_id)
        return record


def _serialize_skill(skill: CapabilityAssignment) -> str:
    return "|".join(
        (skill.capability, skill.skill, skill.confidence, skill.validation_state, skill.source, skill.model_dependency, skill.tool_dependency, str(int(skill.recent_success)), str(int(skill.recent_failure)))
    )


def _parse_skills(raw: str) -> Tuple[CapabilityAssignment, ...]:
    result = []
    for part in raw.split("¬"):
        if not part:
            continue
        fields = part.split("|")
        if len(fields) < 2:
            continue
        result.append(
            CapabilityAssignment(
                capability=fields[0],
                skill=fields[1],
                confidence=fields[2] if len(fields) > 2 else "configured",
                validation_state=fields[3] if len(fields) > 3 else "unvalidated",
                source=fields[4] if len(fields) > 4 else "configured",
                model_dependency=fields[5] if len(fields) > 5 else "",
                tool_dependency=fields[6] if len(fields) > 6 else "",
                recent_success=fields[7] == "1" if len(fields) > 7 else False,
                recent_failure=fields[8] == "1" if len(fields) > 8 else False,
            )
        )
    return tuple(result)


def _deserialize_skills(raw: str) -> Tuple[CapabilityAssignment, ...]:
    return _parse_skills(raw)


def _organization_from_row(row) -> OrganizationRecord:
    return OrganizationRecord(
        organization_id=row["organization_id"],
        name=row["name"],
        purpose=row["purpose"],
        owner=row["owner"],
        mode=row["mode"],
        state=row["state"],
        default_mission_leader=row["default_mission_leader"],
        default_escalation_path=row["default_escalation_path"],
        default_review_policy=row["default_review_policy"],
        policy_version=row["policy_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _unit_from_row(row) -> OrganizationalUnit:
    return OrganizationalUnit(
        unit_id=row["unit_id"],
        unit_type=row["unit_type"],
        name=row["name"],
        purpose=row["purpose"],
        parent_unit=row["parent_unit"],
        leader=row["leader"],
        capabilities=tuple(x for x in row["capabilities"].split("|") if x),
        escalation_target=row["escalation_target"],
        supported_mission_types=tuple(x for x in row["supported_mission_types"].split("|") if x),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _role_from_row(row) -> RoleDefinition:
    return RoleDefinition(
        role_id=row["role_id"],
        title=row["title"],
        purpose=row["purpose"],
        responsibilities=tuple(x for x in row["responsibilities"].split("|") if x),
        required_capabilities=tuple(x for x in row["required_capabilities"].split("|") if x),
        preferred_capabilities=tuple(x for x in row["preferred_capabilities"].split("|") if x),
        allowed_workload_classes=tuple(x for x in row["allowed_workload_classes"].split("|") if x),
        preferred_model_profile=row["preferred_model_profile"],
        allowed_tools=tuple(x for x in row["allowed_tools"].split("|") if x),
        prohibited_tools=tuple(x for x in row["prohibited_tools"].split("|") if x),
        required_review_relationships=tuple(x for x in row["required_review_relationships"].split("|") if x),
        escalation_path=row["escalation_path"],
        maximum_delegation_depth=row["maximum_delegation_depth"],
        default_task_limits=tuple(x for x in row["default_task_limits"].split("|") if x),
        quality_criteria=tuple(x for x in row["quality_criteria"].split("|") if x),
        memory_access_policy=row["memory_access_policy"],
        privacy_restrictions=tuple(x for x in row["privacy_restrictions"].split("|") if x),
        role_version=row["role_version"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _agent_from_row(row) -> AgentProfile:
    return AgentProfile(
        agent_id=row["agent_id"],
        display_name=row["display_name"],
        role_id=row["role_id"],
        department=row["department"],
        team=row["team"],
        status=row["status"],
        availability=row["availability"],
        capabilities=tuple(x for x in row["capabilities"].split("|") if x),
        skills=_deserialize_skills(row["skills"]),
        model_preferences=tuple(x for x in row["model_preferences"].split("|") if x),
        runtime_restrictions=tuple(x for x in row["runtime_restrictions"].split("|") if x),
        tool_permissions=tuple(x for x in row["tool_permissions"].split("|") if x),
        project_restrictions=tuple(x for x in row["project_restrictions"].split("|") if x),
        privacy_restrictions=tuple(x for x in row["privacy_restrictions"].split("|") if x),
        memory_scope=row["memory_scope"],
        maximum_workload=row["maximum_workload"],
        current_mission=row["current_mission"],
        current_task=row["current_task"],
        queue_depth=row["queue_depth"],
        reliability_state=row["reliability_state"],
        config_version=row["config_version"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )