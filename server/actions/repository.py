"""CRUD operations for the P3B control plane (extends the storage schema)."""

from __future__ import annotations

import json
import sqlite3
from typing import Callable, Dict, List, Optional
from uuid import UUID

from .storage import (
    ActionProposalRecord,
    AgentProfileRecord,
    AgentRunRecord,
    AgentTaskRecord,
    AgentVersionRecord,
    ApprovalChallengeRecord,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    CouncilDefinitionRecord,
    CouncilRunRecord,
    ModelRecord,
    PolicyDecisionRecord,
    ProviderRecord,
    SQLiteControlRepository,
    ToolRecord,
    sha256_hex,
)

TERMINAL_PROPOSAL_STATES = frozenset(
    {"policy_denied", "denied", "expired", "superseded", "revoked", "cancelled", "execution_unavailable"}
)


class SQLiteControlStore(SQLiteControlRepository):
    """CRUD + transitions on the durable control-plane schema."""

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    def upsert_provider(
        self,
        *,
        id: UUID,
        key: str,
        display_name: str,
        provider_type: str,
        location: str,
        transport: str = "http",
        endpoint_reference: str = "",
        auth_reference_type: str = "none",
        status: str = "active",
        health: str = "unknown",
        streaming: bool = False,
        tool_calling: bool = False,
        structured_output: bool = False,
        context_window: int = 0,
        privacy_class: str = "restricted",
        allowed_data_classes: str = "restricted",
        now: int,
    ) -> ProviderRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision FROM control_providers WHERE id = ?", (str(id),)
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO control_providers(
                            id, key, display_name, provider_type, location, transport,
                            endpoint_reference, auth_reference_type, status, health,
                            streaming, tool_calling, structured_output, context_window,
                            privacy_class, allowed_data_classes, created_at, updated_at, revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            str(id), key, display_name, provider_type, location, transport,
                            endpoint_reference, auth_reference_type, status, health,
                            1 if streaming else 0, 1 if tool_calling else 0,
                            1 if structured_output else 0, context_window,
                            privacy_class, allowed_data_classes, now, now,
                        ),
                    )
                else:
                    revision = int(row["revision"]) + 1
                    connection.execute(
                        """
                        UPDATE control_providers
                        SET key=?, display_name=?, provider_type=?, location=?, transport=?,
                            endpoint_reference=?, auth_reference_type=?, status=?, health=?,
                            streaming=?, tool_calling=?, structured_output=?, context_window=?,
                            privacy_class=?, allowed_data_classes=?, updated_at=?, revision=?
                        WHERE id=?
                        """,
                        (
                            key, display_name, provider_type, location, transport,
                            endpoint_reference, auth_reference_type, status, health,
                            1 if streaming else 0, 1 if tool_calling else 0,
                            1 if structured_output else 0, context_window,
                            privacy_class, allowed_data_classes, now, revision, str(id),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        provider = self.get_provider(id)
        assert provider is not None
        return provider

    def get_provider(self, provider_id: UUID) -> Optional[ProviderRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_providers WHERE id = ?", (str(provider_id),)
            ).fetchone()
        return _provider(row) if row is not None else None

    def get_provider_by_key(self, key: str) -> Optional[ProviderRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_providers WHERE key = ?", (key,)
            ).fetchone()
        return _provider(row) if row is not None else None

    def list_providers(self) -> List[ProviderRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_providers ORDER BY key"
            ).fetchall()
        return [_provider(row) for row in rows]

    def set_provider_state(self, provider_id: UUID, status: str, health: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_providers
                SET status=?, health=?, updated_at=?, revision=revision+1
                WHERE id=? AND status != 'deleted'
                """,
                (status, health, now, str(provider_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------

    def upsert_model(
        self,
        *,
        id: UUID,
        provider_id: UUID,
        key: str,
        display_name: str,
        model_identifier: str,
        status: str = "active",
        capabilities: str = "",
        context_limit: int = 0,
        output_limit: int = 0,
        streaming: bool = False,
        structured_output: bool = False,
        tool_calling: bool = False,
        vision: bool = False,
        reasoning: bool = False,
        privacy_class: str = "restricted",
        allowed_data_classes: str = "restricted",
        cost_note: str = "",
        now: int,
    ) -> ModelRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision FROM control_models WHERE id = ?", (str(id),)
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO control_models(
                            id, provider_id, key, display_name, model_identifier, status,
                            capabilities, context_limit, output_limit, streaming,
                            structured_output, tool_calling, vision, reasoning,
                            privacy_class, allowed_data_classes, cost_note,
                            created_at, updated_at, revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            str(id), str(provider_id), key, display_name, model_identifier, status,
                            capabilities, context_limit, output_limit,
                            1 if streaming else 0, 1 if structured_output else 0,
                            1 if tool_calling else 0, 1 if vision else 0, 1 if reasoning else 0,
                            privacy_class, allowed_data_classes, cost_note, now, now,
                        ),
                    )
                else:
                    revision = int(row["revision"]) + 1
                    connection.execute(
                        """
                        UPDATE control_models
                        SET provider_id=?, key=?, display_name=?, model_identifier=?, status=?,
                            capabilities=?, context_limit=?, output_limit=?, streaming=?,
                            structured_output=?, tool_calling=?, vision=?, reasoning=?,
                            privacy_class=?, allowed_data_classes=?, cost_note=?,
                            updated_at=?, revision=?
                        WHERE id=?
                        """,
                        (
                            str(provider_id), key, display_name, model_identifier, status,
                            capabilities, context_limit, output_limit,
                            1 if streaming else 0, 1 if structured_output else 0,
                            1 if tool_calling else 0, 1 if vision else 0, 1 if reasoning else 0,
                            privacy_class, allowed_data_classes, cost_note, now, revision, str(id),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        model = self.get_model(id)
        assert model is not None
        return model

    def get_model(self, model_id: UUID) -> Optional[ModelRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_models WHERE id = ?", (str(model_id),)
            ).fetchone()
        return _model(row) if row is not None else None

    def list_models(self, provider_id: Optional[UUID] = None) -> List[ModelRecord]:
        with self._connection_factory() as connection:
            if provider_id is None:
                rows = connection.execute(
                    "SELECT * FROM control_models ORDER BY key"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM control_models WHERE provider_id = ? ORDER BY key",
                    (str(provider_id),),
                ).fetchall()
        return [_model(row) for row in rows]

    def set_model_state(self, model_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_models SET status=?, updated_at=?, revision=revision+1 WHERE id=?
                """,
                (status, now, str(model_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Agents (immutable versions)
    # ------------------------------------------------------------------

    def create_agent(
        self,
        *,
        id: UUID,
        organization_id: UUID,
        workspace_id: Optional[UUID],
        key: str,
        display_name: str,
        description: str,
        purpose: str,
        system_instructions: str,
        allowed_tools: str,
        denied_tools: str,
        required_capabilities: str,
        max_delegation_depth: int,
        max_parallel_tasks: int,
        max_runtime_ms: int,
        max_token_budget: int,
        data_boundary: str,
        approval_policy: str,
        default_provider_policy: str = "backend",
        default_model_policy: str = "backend",
        created_by: UUID,
        now: int,
    ) -> tuple:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_agents(
                        id, organization_id, workspace_id, key, display_name, description,
                        purpose, status, system_instructions, allowed_tools, denied_tools,
                        required_capabilities, max_delegation_depth, max_parallel_tasks,
                        max_runtime_ms, max_token_budget, data_boundary, approval_policy,
                        default_provider_policy, default_model_policy,
                        created_by, created_at, updated_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        str(id), str(organization_id), str(workspace_id) if workspace_id else None,
                        key, display_name, description, purpose, system_instructions,
                        allowed_tools, denied_tools, required_capabilities,
                        max_delegation_depth, max_parallel_tasks, max_runtime_ms,
                        max_token_budget, data_boundary, approval_policy,
                        default_provider_policy, default_model_policy,
                        str(created_by), now, now,
                    ),
                )
                version_id = UUID(int=id.int + 1) if False else _next_uuid()
                digest = sha256_hex(json.dumps(
                    {
                        "key": key, "display_name": display_name, "description": description,
                        "system_instructions": system_instructions, "allowed_tools": allowed_tools,
                        "denied_tools": denied_tools, "required_capabilities": required_capabilities,
                        "max_delegation_depth": max_delegation_depth,
                        "max_parallel_tasks": max_parallel_tasks, "max_runtime_ms": max_runtime_ms,
                        "max_token_budget": max_token_budget, "data_boundary": data_boundary,
                        "approval_policy": approval_policy,
                        "default_provider_policy": default_provider_policy,
                        "default_model_policy": default_model_policy,
                    },
                    sort_keys=True, separators=(",", ":"),
                ))
                connection.execute(
                    """
                    INSERT INTO control_agent_versions(
                        version_id, agent_id, configuration_digest, created_by, created_at, superseded
                    ) VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (str(version_id), str(id), digest, str(created_by), now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_agent(id), version_id  # type: ignore[return-value]

    def get_agent(self, agent_id: UUID) -> Optional[AgentProfileRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_agents WHERE id = ?", (str(agent_id),)
            ).fetchone()
        return _agent(row) if row is not None else None

    def get_agent_by_key(self, organization_id: UUID, workspace_id: Optional[UUID], key: str) -> Optional[AgentProfileRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM control_agents
                WHERE organization_id = ? AND workspace_id IS ? AND key = ?
                """,
                (str(organization_id), str(workspace_id) if workspace_id else None, key),
            ).fetchone()
        return _agent(row) if row is not None else None

    def list_agents(self, organization_id: UUID, workspace_id: Optional[UUID]) -> List[AgentProfileRecord]:
        with self._connection_factory() as connection:
            if workspace_id is None:
                rows = connection.execute(
                    "SELECT * FROM control_agents WHERE organization_id = ? AND workspace_id IS NULL ORDER BY key",
                    (str(organization_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM control_agents WHERE organization_id = ? AND workspace_id = ? ORDER BY key",
                    (str(organization_id), str(workspace_id)),
                ).fetchall()
        return [_agent(row) for row in rows]

    def set_agent_state(self, agent_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_agents SET status=?, updated_at=?, revision=revision+1 WHERE id=?
                """,
                (status, now, str(agent_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def list_agent_versions(self, agent_id: UUID) -> List[AgentVersionRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_agent_versions WHERE agent_id = ? ORDER BY created_at",
                (str(agent_id),),
            ).fetchall()
        return [_agent_version(row) for row in rows]

    def get_agent_version(self, version_id: UUID) -> Optional[AgentVersionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_agent_versions WHERE version_id = ?", (str(version_id),)
            ).fetchone()
        return _agent_version(row) if row is not None else None

    # ------------------------------------------------------------------
    # Runs + tasks
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        agent_id: UUID,
        agent_version_id: UUID,
        requested_by: UUID,
        parent_run_id: Optional[UUID] = None,
        delegation_depth: int = 0,
        objective: str = "",
        trace_id: str = "",
        now: int,
    ) -> AgentRunRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_agent_runs(
                        id, conversation_id, message_id, agent_id, agent_version_id,
                        status, parent_run_id, delegation_depth, requested_by, objective,
                        trace_id, revision
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        str(id), str(conversation_id), str(message_id),
                        str(agent_id), str(agent_version_id),
                        str(parent_run_id) if parent_run_id else None,
                        delegation_depth, str(requested_by), objective, trace_id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        run = self.get_run(id)
        assert run is not None
        return run

    def get_run(self, run_id: UUID) -> Optional[AgentRunRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_agent_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return _run(row) if row is not None else None

    def list_runs_for_agent(self, agent_id: UUID) -> List[AgentRunRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_agent_runs WHERE agent_id = ? ORDER BY started_at DESC",
                (str(agent_id),),
            ).fetchall()
        return [_run(row) for row in rows]

    def list_runs_for_conversation(self, conversation_id: UUID) -> List[AgentRunRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_agent_runs WHERE conversation_id = ? ORDER BY created_at",
                (str(conversation_id),),
            ).fetchall()
        return [_run(row) for row in rows]

    def update_run_state(
        self,
        run_id: UUID,
        *,
        status: str,
        now: int,
        provider_id: Optional[UUID] = None,
        model_id: Optional[UUID] = None,
        failure: str = "",
        token_usage: int = 0,
        cancellation: str = "",
    ) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_agent_runs
                SET status=?, provider_id=COALESCE(?, provider_id),
                    model_id=COALESCE(?, model_id), failure=CASE WHEN ? = '' THEN failure ELSE ? END,
                    token_usage=?, cancellation=CASE WHEN ? = '' THEN cancellation ELSE ? END,
                    started_at=COALESCE(started_at, ?),
                    completed_at=CASE WHEN ? IN ('succeeded','failed','cancelled','interrupted') THEN ? ELSE completed_at END,
                    revision=revision+1
                WHERE id=? AND status NOT IN ('succeeded','failed','cancelled','interrupted')
                """,
                (
                    status,
                    str(provider_id) if provider_id else None,
                    str(model_id) if model_id else None,
                    failure, failure,
                    token_usage,
                    cancellation, cancellation,
                    now,
                    1, now,
                    str(run_id),
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def set_run_result(
        self,
        run_id: UUID,
        *,
        status: str,
        result: str,
        now: int,
        token_usage: int = 0,
        failure: str = "",
    ) -> bool:
        """Persist a bounded run output and a terminal transition. Authoritative
        runs never fabricate output: the result is the actual model response."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE control_agent_runs
                    SET status=?, result=?, token_usage=?, failure=CASE WHEN ? = '' THEN failure ELSE ? END,
                        completed_at=COALESCE(completed_at, ?), revision=revision+1
                    WHERE id=? AND status NOT IN ('succeeded','failed','cancelled','interrupted')
                    """,
                    (status, result[:32000], token_usage, failure, failure, now, str(run_id)),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO control_run_outputs(run_id, output, created_at, revision)
                    VALUES (?, ?, ?, 1)
                    """,
                    (str(run_id), result[:64000], now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return True

    def get_run_output(self, run_id: UUID) -> str:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT output FROM control_run_outputs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return str(row["output"]) if row is not None else ""

    def list_child_runs(self, parent_run_id: UUID) -> List[AgentRunRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_agent_runs WHERE parent_run_id = ? ORDER BY started_at",
                (str(parent_run_id),),
            ).fetchall()
        return [_run(row) for row in rows]

    def create_council_member_run(
        self,
        *,
        id: UUID,
        council_run_id: UUID,
        agent_id: UUID,
        objective: str,
        now: int,
    ) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO control_council_member_runs(
                    id, council_run_id, agent_id, objective, status, created_at, revision
                ) VALUES (?, ?, ?, ?, 'queued', ?, 1)
                """,
                (str(id), str(council_run_id), str(agent_id), objective, now),
            )
            connection.commit()

    def update_council_member_run(
        self,
        member_run_id: UUID,
        *,
        status: str,
        result: str = "",
        failure: str = "",
        now: int,
        agent_version_id: Optional[UUID] = None,
        provider_id: Optional[UUID] = None,
        model_id: Optional[UUID] = None,
    ) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE control_council_member_runs
                SET status=?, result=?, failure=CASE WHEN ? = '' THEN failure ELSE ? END,
                    agent_version_id=COALESCE(?, agent_version_id),
                    provider_id=COALESCE(?, provider_id), model_id=COALESCE(?, model_id),
                    started_at=COALESCE(started_at, ?),
                    completed_at=CASE WHEN ? IN ('succeeded','failed','cancelled') THEN ? ELSE completed_at END,
                    revision=revision+1
                WHERE id=?
                """,
                (
                    status, result[:32000], failure, failure,
                    str(agent_version_id) if agent_version_id else None,
                    str(provider_id) if provider_id else None,
                    str(model_id) if model_id else None,
                    now, 1, now, str(member_run_id),
                ),
            )
            connection.commit()

    def list_council_member_runs(self, council_run_id: UUID) -> List[Dict]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_council_member_runs WHERE council_run_id = ? ORDER BY created_at",
                (str(council_run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale_runs(self, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_agent_runs
                SET status='interrupted', failure='interrupted by restart',
                    completed_at=COALESCE(completed_at, ?), revision=revision+1
                WHERE status IN ('queued','running','waiting_for_tool','waiting_for_approval')
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE control_agent_tasks
                SET state='interrupted', completed_at=COALESCE(completed_at, ?)
                WHERE state IN ('pending','ready','running','waiting_for_dependency','waiting_for_approval')
                """,
                (now,),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def create_task(
        self,
        *,
        id: UUID,
        run_id: UUID,
        parent_task_id: Optional[UUID],
        title: str,
        objective: str,
        assigned_agent_id: Optional[UUID],
        dependencies: str,
        now: int,
    ) -> AgentTaskRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_agent_tasks(
                        id, run_id, parent_task_id, title, objective, state,
                        assigned_agent_id, dependencies, created_at, revision
                    ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, 1)
                    """,
                    (
                        str(id), str(run_id), str(parent_task_id) if parent_task_id else None,
                        title, objective, str(assigned_agent_id) if assigned_agent_id else None,
                        dependencies, now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        task = self.get_task(id)
        assert task is not None
        return task

    def get_task(self, task_id: UUID) -> Optional[AgentTaskRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_agent_tasks WHERE id = ?", (str(task_id),)
            ).fetchone()
        return _task(row) if row is not None else None

    def list_tasks(self, run_id: UUID) -> List[AgentTaskRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_agent_tasks WHERE run_id = ? ORDER BY created_at",
                (str(run_id),),
            ).fetchall()
        return [_task(row) for row in rows]

    def update_task_state(
        self,
        task_id: UUID,
        *,
        state: str,
        now: int,
        output_reference: str = "",
        failure: str = "",
    ) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_agent_tasks
                SET state=?, output_reference=CASE WHEN ? = '' THEN output_reference ELSE ? END,
                    failure=CASE WHEN ? = '' THEN failure ELSE ? END,
                    started_at=COALESCE(started_at, ?),
                    completed_at=CASE WHEN ? IN ('succeeded','failed','cancelled','interrupted') THEN ? ELSE completed_at END,
                    revision=revision+1
                WHERE id=?
                """,
                (
                    state, output_reference, output_reference, failure, failure, now,
                    1, now, str(task_id),
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def register_tool(
        self,
        *,
        id: UUID,
        key: str,
        display_name: str,
        description: str,
        version: str,
        category: str,
        input_schema: str,
        output_schema: str,
        capability_requirements: str,
        risk: str,
        side_effect: str,
        approval_policy: str,
        execution_availability: str,
        executor_type: str,
        data_class_limits: str,
        target_constraints: str,
        now: int,
    ) -> ToolRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision FROM control_tools WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO control_tools(
                            id, key, display_name, description, version, category,
                            input_schema, output_schema, capability_requirements, risk,
                            side_effect, approval_policy, execution_availability,
                            executor_type, data_class_limits, target_constraints,
                            status, created_at, updated_at, revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1)
                        """,
                        (
                            str(id), key, display_name, description, version, category,
                            input_schema, output_schema, capability_requirements, risk,
                            side_effect, approval_policy, execution_availability,
                            executor_type, data_class_limits, target_constraints, now, now,
                        ),
                    )
                else:
                    revision = int(row["revision"]) + 1
                    connection.execute(
                        """
                        UPDATE control_tools
                        SET display_name=?, description=?, version=?, category=?,
                            input_schema=?, output_schema=?, capability_requirements=?,
                            risk=?, side_effect=?, approval_policy=?, execution_availability=?,
                            executor_type=?, data_class_limits=?, target_constraints=?,
                            updated_at=?, revision=?
                        WHERE key=?
                        """,
                        (
                            display_name, description, version, category, input_schema,
                            output_schema, capability_requirements, risk, side_effect,
                            approval_policy, execution_availability, executor_type,
                            data_class_limits, target_constraints, now, revision, key,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_tool_by_key(key)  # type: ignore[return-value]

    def get_tool_by_key(self, key: str) -> Optional[ToolRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_tools WHERE key = ?", (key,)
            ).fetchone()
        return _tool(row) if row is not None else None

    def get_tool_by_id(self, tool_id: UUID) -> Optional[ToolRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_tools WHERE id = ?", (str(tool_id),)
            ).fetchone()
        return _tool(row) if row is not None else None

    def list_tools(self) -> List[ToolRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_tools ORDER BY key"
            ).fetchall()
        return [_tool(row) for row in rows]

    def set_tool_state(self, tool_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_tools SET status=?, updated_at=?, revision=revision+1 WHERE id=?
                """,
                (status, now, str(tool_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def list_workspace_ids(self) -> List[UUID]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT DISTINCT workspace_id FROM control_action_proposals"
            ).fetchall()
        return [UUID(str(row["workspace_id"])) for row in rows]

    # ------------------------------------------------------------------
    # Action proposals
    # ------------------------------------------------------------------

    def create_proposal(self, record: ActionProposalRecord) -> ActionProposalRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_action_proposals(
                        id, organization_id, workspace_id, conversation_id, conversation_run_id,
                        agent_run_id, task_id, proposer_user_id, proposer_agent_id, tool_id,
                        tool_version, action_type, parameters, canonical_target, summary,
                        expected_effect, reversibility, risk, required_capabilities,
                        requested_at, expires_at, state, proposal_version, previous_proposal_id,
                        payload_digest, policy_snapshot_id, trace_id, revision, original_request
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        str(record.id), str(record.organization_id), str(record.workspace_id),
                        str(record.conversation_id) if record.conversation_id else None,
                        str(record.conversation_run_id) if record.conversation_run_id else None,
                        str(record.agent_run_id) if record.agent_run_id else None,
                        str(record.task_id) if record.task_id else None,
                        str(record.proposer_user_id) if record.proposer_user_id else None,
                        str(record.proposer_agent_id) if record.proposer_agent_id else None,
                        str(record.tool_id), record.tool_version, record.action_type,
                        record.parameters, record.canonical_target, record.summary,
                        record.expected_effect, record.reversibility, record.risk,
                        record.required_capabilities, record.requested_at, record.expires_at,
                        record.state, record.proposal_version,
                        str(record.previous_proposal_id) if record.previous_proposal_id else None,
                        record.payload_digest,
                        str(record.policy_snapshot_id) if record.policy_snapshot_id else None,
                        record.trace_id, record.original_request,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def get_proposal(self, proposal_id: UUID) -> Optional[ActionProposalRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_action_proposals WHERE id = ?", (str(proposal_id),)
            ).fetchone()
        return _proposal(row) if row is not None else None

    def list_proposals(self, workspace_id: UUID, state: Optional[str] = None) -> List[ActionProposalRecord]:
        with self._connection_factory() as connection:
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM control_action_proposals WHERE workspace_id = ? ORDER BY requested_at DESC",
                    (str(workspace_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM control_action_proposals WHERE workspace_id = ? AND state = ? ORDER BY requested_at DESC",
                    (str(workspace_id), state),
                ).fetchall()
        return [_proposal(row) for row in rows]

    def transition_proposal(
        self,
        proposal_id: UUID,
        state: str,
        now: int,
        *,
        policy_snapshot_id: Optional[UUID] = None,
    ) -> bool:
        """Centralized proposal state transition. Terminal states cannot be
        overwritten; a proposal cannot transition itself."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT state FROM control_action_proposals WHERE id = ?", (str(proposal_id),)
            ).fetchone()
            if row is None or str(row["state"]) in TERMINAL_PROPOSAL_STATES:
                return False
            cursor = connection.execute(
                """
                UPDATE control_action_proposals
                SET state=?, policy_snapshot_id=COALESCE(?, policy_snapshot_id),
                    revision=revision+1
                WHERE id=? AND state NOT IN (%s)
                """
                % ", ".join("'%s'" % s for s in TERMINAL_PROPOSAL_STATES),
                (state, str(policy_snapshot_id) if policy_snapshot_id else None, str(proposal_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def save_policy_decision(self, decision: PolicyDecisionRecord) -> PolicyDecisionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_policy_decisions(
                        id, proposal_id, result, reason_codes, explanation,
                        required_capabilities, required_approval_count, separation_of_duties,
                        step_up_required, expiration, policy_version, policy_snapshot,
                        policy_digest, evaluated_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        str(decision.id), str(decision.proposal_id), decision.result,
                        decision.reason_codes, decision.explanation,
                        decision.required_capabilities, decision.required_approval_count,
                        1 if decision.separation_of_duties else 0, decision.step_up_required,
                        decision.expiration, decision.policy_version, decision.policy_snapshot,
                        decision.policy_digest, decision.evaluated_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return decision

    def get_policy_decision(self, decision_id: UUID) -> Optional[PolicyDecisionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_policy_decisions WHERE id = ?", (str(decision_id),)
            ).fetchone()
        return _policy(row) if row is not None else None

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def create_approval_request(
        self,
        *,
        id: UUID,
        proposal_id: UUID,
        proposal_digest: str,
        policy_decision_id: UUID,
        required_capability: str,
        required_approval_count: int,
        separation_of_duties: bool,
        step_up_required: str,
        now: int,
        expires_at: int,
    ) -> ApprovalRequestRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_approval_requests(
                        id, proposal_id, proposal_digest, policy_decision_id,
                        required_capability, required_approval_count, separation_of_duties,
                        step_up_required, status, created_at, expires_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 1)
                    """,
                    (
                        str(id), str(proposal_id), proposal_digest, str(policy_decision_id),
                        required_capability, required_approval_count,
                        1 if separation_of_duties else 0, step_up_required, now, expires_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        request = self.get_approval_request(id)
        assert request is not None
        return request

    def get_approval_request(self, request_id: UUID) -> Optional[ApprovalRequestRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_approval_requests WHERE id = ?", (str(request_id),)
            ).fetchone()
        return _approval_request(row) if row is not None else None

    def list_approval_requests(
        self, workspace_id: UUID, status: Optional[str] = None
    ) -> List[ApprovalRequestRecord]:
        with self._connection_factory() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT request.* FROM control_approval_requests AS request
                    JOIN control_action_proposals AS proposal ON proposal.id = request.proposal_id
                    WHERE proposal.workspace_id = ?
                    ORDER BY request.created_at DESC
                    """,
                    (str(workspace_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT request.* FROM control_approval_requests AS request
                    JOIN control_action_proposals AS proposal ON proposal.id = request.proposal_id
                    WHERE proposal.workspace_id = ? AND request.status = ?
                    ORDER BY request.created_at DESC
                    """,
                    (str(workspace_id), status),
                ).fetchall()
        return [_approval_request(row) for row in rows]

    def update_approval_request_status(self, request_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_approval_requests SET status=?, revision=revision+1 WHERE id=? AND status='pending'
                """,
                (status, str(request_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def invalidate_approval_requests_for_proposal(self, proposal_id: UUID, status: str, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_approval_requests SET status=?, revision=revision+1
                WHERE proposal_id=? AND status='pending'
                """,
                (status, str(proposal_id)),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def record_approval_decision(self, decision: ApprovalDecisionRecord) -> ApprovalDecisionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_approval_decisions(
                        id, approval_request_id, proposal_id, proposal_digest, decision,
                        approver_user_id, approver_device_id, approver_session_id,
                        approver_organization_id, approver_workspace_id, auth_strength,
                        step_up_evidence, reason, decided_at, revocation_state,
                        decision_digest, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        str(decision.id), str(decision.approval_request_id),
                        str(decision.proposal_id), decision.proposal_digest, decision.decision,
                        str(decision.approver_user_id), str(decision.approver_device_id),
                        str(decision.approver_session_id), str(decision.approver_organization_id),
                        str(decision.approver_workspace_id), decision.auth_strength,
                        decision.step_up_evidence, decision.reason, decision.decided_at,
                        decision.revocation_state, decision.decision_digest,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return decision

    def approval_decisions_for_proposal(self, proposal_id: UUID) -> List[ApprovalDecisionRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_approval_decisions WHERE proposal_id = ? ORDER BY decided_at",
                (str(proposal_id),),
            ).fetchall()
        return [_approval_decision(row) for row in rows]

    def approval_decisions_for_request(self, request_id: UUID) -> List[ApprovalDecisionRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_approval_decisions WHERE approval_request_id = ? ORDER BY decided_at",
                (str(request_id),),
            ).fetchall()
        return [_approval_decision(row) for row in rows]

    # ------------------------------------------------------------------
    # Approval challenges
    # ------------------------------------------------------------------

    def create_approval_challenge(self, challenge: ApprovalChallengeRecord) -> ApprovalChallengeRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_approval_challenges(
                        id, proposal_id, proposal_digest, policy_decision_id, approval_request_id,
                        approver_user_id, approver_device_id, organization_id, workspace_id,
                        requested_decision, risk, nonce, issued_at, expires_at, state, signed_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    """,
                    (
                        str(challenge.id), str(challenge.proposal_id), challenge.proposal_digest,
                        str(challenge.policy_decision_id), str(challenge.approval_request_id),
                        str(challenge.approver_user_id), str(challenge.approver_device_id),
                        str(challenge.organization_id), str(challenge.workspace_id),
                        challenge.requested_decision, challenge.risk, challenge.nonce,
                        challenge.issued_at, challenge.expires_at, challenge.signed_message,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return challenge

    def get_approval_challenge(self, challenge_id: UUID) -> Optional[ApprovalChallengeRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_approval_challenges WHERE id = ?", (str(challenge_id),)
            ).fetchone()
        return _approval_challenge(row) if row is not None else None

    def update_approval_challenge_state(self, challenge_id: UUID, state: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_approval_challenges SET state=? WHERE id=? AND state='open'
                """,
                (state, str(challenge_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Council
    # ------------------------------------------------------------------

    def create_council_definition(
        self,
        *,
        id: UUID,
        organization_id: UUID,
        workspace_id: Optional[UUID],
        name: str,
        purpose: str,
        member_agents: str,
        chair_agent: Optional[UUID],
        quorum_rule: str,
        maximum_rounds: int,
        disagreement_policy: str,
        output_schema: str,
        now: int,
    ) -> CouncilDefinitionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_councils(
                        id, organization_id, workspace_id, name, purpose, member_agents,
                        chair_agent, quorum_rule, maximum_rounds, disagreement_policy,
                        output_schema, status, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1)
                    """,
                    (
                        str(id), str(organization_id),
                        str(workspace_id) if workspace_id else None,
                        name, purpose, member_agents,
                        str(chair_agent) if chair_agent else None,
                        quorum_rule, maximum_rounds, disagreement_policy, output_schema,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        definition = self.get_council_definition(id)
        assert definition is not None
        return definition

    def get_council_definition(self, council_id: UUID) -> Optional[CouncilDefinitionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_councils WHERE id = ?", (str(council_id),)
            ).fetchone()
        return _council(row) if row is not None else None

    def list_councils(self, organization_id: UUID) -> List[CouncilDefinitionRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM control_councils WHERE organization_id = ? ORDER BY name",
                (str(organization_id),),
            ).fetchall()
        return [_council(row) for row in rows]

    def create_council_run(
        self,
        *,
        id: UUID,
        council_definition_id: UUID,
        conversation_id: Optional[UUID],
        message_id: Optional[UUID],
        council_snapshot: str,
        trace_id: str,
        now: int,
    ) -> CouncilRunRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO control_council_runs(
                        id, conversation_id, message_id, council_definition_id,
                        council_snapshot, state, created_at, trace_id, revision
                    ) VALUES (?, ?, ?, ?, ?, 'started', ?, ?, 1)
                    """,
                    (
                        str(id), str(conversation_id) if conversation_id else None,
                        str(message_id) if message_id else None,
                        str(council_definition_id), council_snapshot, now, trace_id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        run = self.get_council_run(id)
        assert run is not None
        return run

    def get_council_run(self, run_id: UUID) -> Optional[CouncilRunRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM control_council_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return _council_run(row) if row is not None else None

    def update_council_run(
        self,
        run_id: UUID,
        *,
        state: str,
        member_run_ids: str = "",
        rounds: int = 0,
        final_recommendation: str = "",
        dissents: str = "",
        proposed_action_ids: str = "",
        now: int,
    ) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE control_council_runs
                SET state=?, member_run_ids=CASE WHEN ? = '' THEN member_run_ids ELSE ? END,
                    rounds=?, final_recommendation=CASE WHEN ? = '' THEN final_recommendation ELSE ? END,
                    dissents=CASE WHEN ? = '' THEN dissents ELSE ? END,
                    proposed_action_ids=CASE WHEN ? = '' THEN proposed_action_ids ELSE ? END,
                    completed_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN ? ELSE completed_at END,
                    revision=revision+1
                WHERE id=?
                """,
                (
                    state, member_run_ids, member_run_ids, rounds,
                    final_recommendation, final_recommendation,
                    dissents, dissents, proposed_action_ids, proposed_action_ids,
                    1, now, str(run_id),
                ),
            )
            connection.commit()
            return cursor.rowcount == 1


def _next_uuid() -> UUID:
    import uuid
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _provider(row: sqlite3.Row) -> ProviderRecord:
    return ProviderRecord(
        id=UUID(str(row["id"])), key=str(row["key"]), display_name=str(row["display_name"]),
        provider_type=str(row["provider_type"]), location=str(row["location"]),
        transport=str(row["transport"]), endpoint_reference=str(row["endpoint_reference"]),
        auth_reference_type=str(row["auth_reference_type"]), status=str(row["status"]),
        health=str(row["health"]), streaming=bool(row["streaming"]),
        tool_calling=bool(row["tool_calling"]), structured_output=bool(row["structured_output"]),
        context_window=int(row["context_window"]), privacy_class=str(row["privacy_class"]),
        allowed_data_classes=str(row["allowed_data_classes"]),
        created_at=int(row["created_at"]), updated_at=int(row["updated_at"]),
        revision=int(row["revision"]),
    )


def _model(row: sqlite3.Row) -> ModelRecord:
    return ModelRecord(
        id=UUID(str(row["id"])), provider_id=UUID(str(row["provider_id"])),
        key=str(row["key"]), display_name=str(row["display_name"]),
        model_identifier=str(row["model_identifier"]), status=str(row["status"]),
        capabilities=str(row["capabilities"]), context_limit=int(row["context_limit"]),
        output_limit=int(row["output_limit"]), streaming=bool(row["streaming"]),
        structured_output=bool(row["structured_output"]), tool_calling=bool(row["tool_calling"]),
        vision=bool(row["vision"]), reasoning=bool(row["reasoning"]),
        privacy_class=str(row["privacy_class"]), allowed_data_classes=str(row["allowed_data_classes"]),
        cost_note=str(row["cost_note"]), created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]), revision=int(row["revision"]),
    )


def _agent(row: sqlite3.Row) -> AgentProfileRecord:
    return AgentProfileRecord(
        id=UUID(str(row["id"])), organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])) if row["workspace_id"] else None,
        key=str(row["key"]), display_name=str(row["display_name"]),
        description=str(row["description"]), purpose=str(row["purpose"]),
        status=str(row["status"]), system_instructions=str(row["system_instructions"]),
        instruction_version=int(row["instruction_version"]),
        default_provider_policy=str(row["default_provider_policy"]),
        default_model_policy=str(row["default_model_policy"]),
        allowed_tools=str(row["allowed_tools"]), denied_tools=str(row["denied_tools"]),
        required_capabilities=str(row["required_capabilities"]),
        max_delegation_depth=int(row["max_delegation_depth"]),
        max_parallel_tasks=int(row["max_parallel_tasks"]),
        max_runtime_ms=int(row["max_runtime_ms"]),
        max_token_budget=int(row["max_token_budget"]),
        data_boundary=str(row["data_boundary"]), memory_policy=str(row["memory_policy"]),
        approval_policy=str(row["approval_policy"]),
        created_by=UUID(str(row["created_by"])), created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]), revision=int(row["revision"]),
    )


def _agent_version(row: sqlite3.Row) -> AgentVersionRecord:
    return AgentVersionRecord(
        version_id=UUID(str(row["version_id"])), agent_id=UUID(str(row["agent_id"])),
        configuration_digest=str(row["configuration_digest"]),
        created_by=UUID(str(row["created_by"])), created_at=int(row["created_at"]),
        superseded=bool(row["superseded"]),
    )


def _run(row: sqlite3.Row) -> AgentRunRecord:
    return AgentRunRecord(
        id=UUID(str(row["id"])), conversation_id=UUID(str(row["conversation_id"])),
        message_id=UUID(str(row["message_id"])), agent_id=UUID(str(row["agent_id"])),
        agent_version_id=UUID(str(row["agent_version_id"])),
        provider_id=UUID(str(row["provider_id"])) if row["provider_id"] else None,
        model_id=UUID(str(row["model_id"])) if row["model_id"] else None,
        status=str(row["status"]),
        parent_run_id=UUID(str(row["parent_run_id"])) if row["parent_run_id"] else None,
        delegation_depth=int(row["delegation_depth"]),
        requested_by=UUID(str(row["requested_by"])),
        objective=str(row["objective"]) if "objective" in row.keys() else "",
        started_at=int(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
        cancellation=str(row["cancellation"]), failure=str(row["failure"]),
        result=str(row["result"]) if "result" in row.keys() else "",
        token_usage=int(row["token_usage"]), trace_id=str(row["trace_id"]),
        revision=int(row["revision"]),
    )


def _task(row: sqlite3.Row) -> AgentTaskRecord:
    return AgentTaskRecord(
        id=UUID(str(row["id"])), run_id=UUID(str(row["run_id"])),
        parent_task_id=UUID(str(row["parent_task_id"])) if row["parent_task_id"] else None,
        title=str(row["title"]), objective=str(row["objective"]), state=str(row["state"]),
        assigned_agent_id=UUID(str(row["assigned_agent_id"])) if row["assigned_agent_id"] else None,
        dependencies=str(row["dependencies"]), output_reference=str(row["output_reference"]),
        failure=str(row["failure"]), created_at=int(row["created_at"]),
        started_at=int(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
        revision=int(row["revision"]),
    )


def _tool(row: sqlite3.Row) -> ToolRecord:
    return ToolRecord(
        id=UUID(str(row["id"])), key=str(row["key"]), display_name=str(row["display_name"]),
        description=str(row["description"]), version=str(row["version"]),
        category=str(row["category"]), input_schema=str(row["input_schema"]),
        output_schema=str(row["output_schema"]),
        capability_requirements=str(row["capability_requirements"]),
        risk=str(row["risk"]), side_effect=str(row["side_effect"]),
        approval_policy=str(row["approval_policy"]),
        execution_availability=str(row["execution_availability"]),
        executor_type=str(row["executor_type"]),
        data_class_limits=str(row["data_class_limits"]),
        target_constraints=str(row["target_constraints"]),
        timeout_policy=str(row["timeout_policy"]),
        idempotency_policy=str(row["idempotency_policy"]),
        status=str(row["status"]), created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]), revision=int(row["revision"]),
    )


def _proposal(row: sqlite3.Row) -> ActionProposalRecord:
    return ActionProposalRecord(
        id=UUID(str(row["id"])), organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])),
        conversation_id=UUID(str(row["conversation_id"])) if row["conversation_id"] else None,
        conversation_run_id=UUID(str(row["conversation_run_id"])) if row["conversation_run_id"] else None,
        agent_run_id=UUID(str(row["agent_run_id"])) if row["agent_run_id"] else None,
        task_id=UUID(str(row["task_id"])) if row["task_id"] else None,
        proposer_user_id=UUID(str(row["proposer_user_id"])) if row["proposer_user_id"] else None,
        proposer_agent_id=UUID(str(row["proposer_agent_id"])) if row["proposer_agent_id"] else None,
        tool_id=UUID(str(row["tool_id"])), tool_version=str(row["tool_version"]),
        action_type=str(row["action_type"]), parameters=str(row["parameters"]),
        canonical_target=str(row["canonical_target"]), summary=str(row["summary"]),
        expected_effect=str(row["expected_effect"]), reversibility=str(row["reversibility"]),
        risk=str(row["risk"]), required_capabilities=str(row["required_capabilities"]),
        requested_at=int(row["requested_at"]), expires_at=int(row["expires_at"]),
        state=str(row["state"]), proposal_version=int(row["proposal_version"]),
        previous_proposal_id=UUID(str(row["previous_proposal_id"])) if row["previous_proposal_id"] else None,
        payload_digest=str(row["payload_digest"]),
        policy_snapshot_id=UUID(str(row["policy_snapshot_id"])) if row["policy_snapshot_id"] else None,
        trace_id=str(row["trace_id"]), revision=int(row["revision"]),
        original_request=str(row["original_request"]),
    )


def _policy(row: sqlite3.Row) -> PolicyDecisionRecord:
    return PolicyDecisionRecord(
        id=UUID(str(row["id"])), proposal_id=UUID(str(row["proposal_id"])),
        result=str(row["result"]), reason_codes=str(row["reason_codes"]),
        explanation=str(row["explanation"]),
        required_capabilities=str(row["required_capabilities"]),
        required_approval_count=int(row["required_approval_count"]),
        separation_of_duties=bool(row["separation_of_duties"]),
        step_up_required=str(row["step_up_required"]),
        expiration=int(row["expiration"]), policy_version=str(row["policy_version"]),
        policy_snapshot=str(row["policy_snapshot"]), policy_digest=str(row["policy_digest"]),
        evaluated_at=int(row["evaluated_at"]), revision=int(row["revision"]),
    )


def _approval_request(row: sqlite3.Row) -> ApprovalRequestRecord:
    return ApprovalRequestRecord(
        id=UUID(str(row["id"])), proposal_id=UUID(str(row["proposal_id"])),
        proposal_digest=str(row["proposal_digest"]),
        policy_decision_id=UUID(str(row["policy_decision_id"])),
        required_capability=str(row["required_capability"]),
        required_approval_count=int(row["required_approval_count"]),
        separation_of_duties=bool(row["separation_of_duties"]),
        step_up_required=str(row["step_up_required"]), status=str(row["status"]),
        created_at=int(row["created_at"]), expires_at=int(row["expires_at"]),
        revision=int(row["revision"]),
    )


def _approval_decision(row: sqlite3.Row) -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        id=UUID(str(row["id"])), approval_request_id=UUID(str(row["approval_request_id"])),
        proposal_id=UUID(str(row["proposal_id"])), proposal_digest=str(row["proposal_digest"]),
        decision=str(row["decision"]), approver_user_id=UUID(str(row["approver_user_id"])),
        approver_device_id=UUID(str(row["approver_device_id"])),
        approver_session_id=UUID(str(row["approver_session_id"])),
        approver_organization_id=UUID(str(row["approver_organization_id"])),
        approver_workspace_id=UUID(str(row["approver_workspace_id"])),
        auth_strength=str(row["auth_strength"]), step_up_evidence=str(row["step_up_evidence"]),
        reason=str(row["reason"]), decided_at=int(row["decided_at"]),
        revocation_state=str(row["revocation_state"]), decision_digest=str(row["decision_digest"]),
        revision=int(row["revision"]),
    )


def _approval_challenge(row: sqlite3.Row) -> ApprovalChallengeRecord:
    return ApprovalChallengeRecord(
        id=UUID(str(row["id"])), proposal_id=UUID(str(row["proposal_id"])),
        proposal_digest=str(row["proposal_digest"]),
        policy_decision_id=UUID(str(row["policy_decision_id"])),
        approval_request_id=UUID(str(row["approval_request_id"])),
        approver_user_id=UUID(str(row["approver_user_id"])),
        approver_device_id=UUID(str(row["approver_device_id"])),
        organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])),
        requested_decision=str(row["requested_decision"]), risk=str(row["risk"]),
        nonce=str(row["nonce"]), issued_at=int(row["issued_at"]),
        expires_at=int(row["expires_at"]), state=str(row["state"]),
        signed_message=str(row["signed_message"]),
    )


def _council(row: sqlite3.Row) -> CouncilDefinitionRecord:
    return CouncilDefinitionRecord(
        id=UUID(str(row["id"])), organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])) if row["workspace_id"] else None,
        name=str(row["name"]), purpose=str(row["purpose"]),
        member_agents=str(row["member_agents"]),
        chair_agent=UUID(str(row["chair_agent"])) if row["chair_agent"] else None,
        quorum_rule=str(row["quorum_rule"]), maximum_rounds=int(row["maximum_rounds"]),
        disagreement_policy=str(row["disagreement_policy"]),
        output_schema=str(row["output_schema"]), status=str(row["status"]),
        revision=int(row["revision"]),
    )


def _council_run(row: sqlite3.Row) -> CouncilRunRecord:
    return CouncilRunRecord(
        id=UUID(str(row["id"])),
        conversation_id=UUID(str(row["conversation_id"])) if row["conversation_id"] else None,
        message_id=UUID(str(row["message_id"])) if row["message_id"] else None,
        council_definition_id=UUID(str(row["council_definition_id"])),
        council_snapshot=str(row["council_snapshot"]), state=str(row["state"]),
        member_run_ids=str(row["member_run_ids"]), rounds=int(row["rounds"]),
        final_recommendation=str(row["final_recommendation"]),
        dissents=str(row["dissents"]), proposed_action_ids=str(row["proposed_action_ids"]),
        created_at=int(row["created_at"]),
        completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
        trace_id=str(row["trace_id"]), revision=int(row["revision"]),
    )
