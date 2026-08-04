"""Mission Control: charter, planning, task decomposition, task graph, and
assignment. Task dependencies are authoritative; no monitored task starts
before its required dependencies are complete. Decomposition depth and task
count are bounded by role policy and mission budget."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .models import (
    AssignmentExplanation,
    MissionCharter,
    MissionPlan,
    MissionRecord,
    MissionTask,
    ResourceBudget,
    TaskDependency,
    TaskGraph,
)
from .organization import OrganizationService


class MissionError(RuntimeError):
    pass


MISSION_DEFAULT_BUDGET = ResourceBudget(
    agent_count=8, delegation_depth=3, retry_count=2, review_rounds=8
)
MAX_TASKS_PER_MISSION = 200
MAX_DEPTH = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


class MissionService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], organization: OrganizationService, governance_blocked=None) -> None:
        self._connection_factory = connection_factory
        self._organization = organization
        self._governance_blocked = governance_blocked or (lambda: (False, ""))

    # ---- missions ----

    def create_mission(self, title: str, objective: str, project: Optional[str] = None, challenge: str = "", priority: str = "normal", risk: str = "low") -> MissionRecord:
        now = _now()
        mission_id = "mission_" + _id("mission", title, now)[:22]
        record = MissionRecord(
            mission_id=mission_id,
            title=title,
            objective=objective,
            project=project,
            priority=priority,
            risk=risk,
            status="draft",
            budget=default_budget(),
            created_at=now,
            updated_at=now,
        )
        self._upsert_mission(record)
        self._activity("mission_created", "Mission created: %s" % title, mission_id, now)
        return record

    def new_charter(self, mission_id: str, objective: str, success_criteria: Tuple[str, ...], *, business_value: str = "", non_goals: Tuple[str, ...] = (), risk: str = "low") -> MissionCharter:
        now = _now()
        charter = MissionCharter(
            charter_id="charter_" + _id("charter", mission_id)[:22],
            mission_id=mission_id,
            objective=objective,
            business_value=business_value,
            success_criteria=success_criteria,
            non_goals=non_goals,
            risk=risk,
            budget=default_budget(),
            created_at=now,
            updated_at=now,
        )
        self._upsert_charter(charter)
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_missions SET status = 'awaiting_approval', updated_at = ? WHERE mission_id = ?",
                (now, mission_id),
            )
        return charter

    def charter(self, mission_id: str) -> Optional[MissionCharter]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_charters WHERE mission_id = ?", (mission_id,)).fetchone()
        return _charter_from_row(row) if row else None

    def approve_charter(self, mission_id: str, *, approved_by: str = "user") -> bool:
        charter = self.charter(mission_id)
        if charter is None:
            return False
        now = _now()
        updated = charter.model_copy(update={"approved": True, "approved_by": approved_by, "updated_at": now})
        self._upsert_charter(updated)
        with self._connection_factory() as connection:
            connection.execute("UPDATE org_missions SET status = 'ready', updated_at = ? WHERE mission_id = ?", (now, mission_id))
        return True

    # ---- planning & tasks -------------------------------------------------

    def plan(self, mission_id: str, task_ids: Tuple[str, ...] = ()) -> MissionPlan:
        now = _now()
        plan = MissionPlan(
            plan_id="plan_" + _id("plan", mission_id)[:22],
            mission_id=mission_id,
            task_ids=task_ids,
            dependencies=(),
            created_at=now,
        )
        self._upsert_plan(plan)
        return plan

    def plan_for(self, mission_id: str) -> Optional[MissionPlan]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM org_plans WHERE mission_id = ? ORDER BY created_at DESC LIMIT 1",
                (mission_id,),
            ).fetchone()
        return _plan_from_row(row) if row else None

    def create_task(self, mission_id: str, title: str, objective: str, *, risk: str = "low", dependencies: Tuple[str, ...] = (), blocking: Tuple[str, ...] = (), depth: int = 0, scope: Tuple[str, ...] = ()) -> Optional[MissionTask]:
        mission = self.get_mission(mission_id)
        if mission is None:
            return None
        count = self.task_count(mission_id)
        budget = mission.budget
        max_count = budget.agent_count * 50 if budget.agent_count else MAX_TASKS_PER_MISSION
        if count >= max_count:
            return None
        if depth > min(MAX_DEPTH, budget.delegation_depth if budget.delegation_depth else MAX_DEPTH):
            return None
        now = _now()
        task = MissionTask(
            task_id="task_" + _id("task", mission_id, title)[:22],
            mission_id=mission_id,
            title=title,
            objective=objective,
            risk=risk,
            dependencies=dependencies,
            blocking_dependencies=blocking,
            depth=depth,
            scope=scope if scope else (str(mission.project or ""),),
            created_at=now,
            updated_at=now,
        )
        self._upsert_task(task)
        for dep in dependencies:
            self.add_dependency(mission_id, task.task_id, dep, optional=True)
        for dep in blocking:
            self.add_dependency(mission_id, task.task_id, dep, relationship="blocks")
        return task

    def add_dependency(self, mission_id: str, source_task_id: str, target_task_id: str, *, relationship: str = "depends_on", optional: bool = False) -> TaskDependency:
        now = _now()
        dependency = TaskDependency(
            dependency_id="dep_" + _id("dep", source_task_id, target_task_id)[:22],
            mission_id=mission_id,
            source_task_id=source_task_id,
            target_task_id=target_task_id,
            relationship=relationship,
            optional=optional,
            created_at=now,
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_task_dependencies (
                    dependency_id, mission_id, source_task_id, target_task_id,
                    relationship, optional, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (dependency.dependency_id, mission_id, source_task_id, target_task_id, relationship, int(optional), now),
            )
        return dependency

    def graph(self, mission_id: str) -> TaskGraph:
        with self._connection_factory() as connection:
            task_rows = connection.execute(
                "SELECT * FROM org_tasks WHERE mission_id = ? ORDER BY created_at", (mission_id,)
            ).fetchall()
            dep_rows = connection.execute(
                "SELECT * FROM org_task_dependencies WHERE mission_id = ?", (mission_id,)
            ).fetchall()
        tasks = tuple(_task_from_row(r) for r in task_rows)
        deps = tuple(
            TaskDependency(
                dependency_id=r["dependency_id"], mission_id=r["mission_id"],
                source_task_id=r["source_task_id"], target_task_id=r["target_task_id"],
                relationship=r["relationship"], optional=bool(r["optional"]), created_at=r["created_at"],
            )
            for r in dep_rows
        )
        cycles = self._find_cycles(deps)
        critical = self._critical_path(tasks, deps)
        parallel = self._parallel_groups(tasks, deps)
        return TaskGraph(
            mission_id=mission_id,
            tasks=tasks,
            dependencies=deps,
            cycles=cycles,
            critical_path=critical,
            parallel_groups=parallel,
            generated_at=_now(),
        )

    def task(self, task_id: str) -> Optional[MissionTask]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def tasks(self, mission_id: str) -> Tuple[MissionTask, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_tasks WHERE mission_id = ? ORDER BY created_at", (mission_id,)).fetchall()
        return tuple(_task_from_row(r) for r in rows)

    def task_count(self, mission_id: str) -> int:
        with self._connection_factory() as connection:
            return connection.execute("SELECT COUNT(*) FROM org_tasks WHERE mission_id = ?", (mission_id,)).fetchone()[0]

    def assign(self, task_id: str, agent_id: Optional[str], explanation: AssignmentExplanation) -> Optional[MissionTask]:
        task = self.task(task_id)
        if task is None:
            return None
        now = _now()
        updated = task.model_copy(update={"assigned_agent": agent_id, "owner": agent_id, "status": "staffed", "updated_at": now})
        self._upsert_task(updated)
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_assignments (
                    assignment_id, task_id, mission_id, agent_id, role_match,
                    capability_match, model_match, permission_match, workload_state,
                    rejected_alternatives, review_relationship, warnings, confidence,
                    reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "asg_" + _id("asg", task_id, agent_id or "none")[:22], task_id, task.mission_id,
                    agent_id or "", explanation.role_match, explanation.capability_match,
                    int(explanation.model_match), int(explanation.permission_match),
                    explanation.workload_state, "|".join(explanation.rejected_alternatives),
                    explanation.review_relationship, "|".join(explanation.warnings),
                    explanation.confidence, explanation.reason[:400], now,
                ),
            )
        if agent_id:
            with self._connection_factory() as connection:
                connection.execute(
                    "UPDATE org_agents SET current_mission = ?, current_task = ?, availability = 'busy', status = 'active', updated_at = ? WHERE agent_id = ?",
                    (task.mission_id, task_id, now, agent_id),
                )
                connection.execute(
                    "UPDATE org_agents SET queue_depth = queue_depth + 1 WHERE agent_id = ?",
                    (agent_id,),
                )
        return updated

    def update_task_state(self, task_id: str, status: str, *, note: str = "", final_result: str = "") -> Optional[MissionTask]:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise MissionError("governance: %s" % reason)
        task = self.task(task_id)
        if task is None:
            return None
        now = _now()
        updated = task.model_copy(
            update={"status": status, "progress_note": note or task.progress_note, "final_result": final_result or task.final_result, "updated_at": now}
        )
        self._upsert_task(updated)
        self._propagate_blocked(updated.mission_id)
        return updated

    def _propagate_blocked(self, mission_id: str) -> None:
        graph = self.graph(mission_id)
        blocked = set()
        # any task whose blocking dependency target is not complete is blocked
        status_by_id = {t.task_id: t.status for t in graph.tasks}
        for dep in graph.dependencies:
            if dep.relationship == "blocks":
                target = status_by_id.get(dep.target_task_id)
                if target != "complete":
                    blocked.add(dep.source_task_id)
        with self._connection_factory() as connection:
            for task_id in blocked:
                connection.execute(
                    "UPDATE org_tasks SET status = 'blocked', updated_at = ? WHERE task_id = ? AND status = 'not_started'",
                    (_now(), task_id),
                )

    def begin(self, mission_id: str) -> bool:
        mission = self.get_mission(mission_id)
        if mission is None or mission.status not in {"ready", "draft"}:
            return False
        now = _now()
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_missions SET status = 'planning', start_time = ?, health = 'healthy', updated_at = ? WHERE mission_id = ?",
                (now, now, mission_id),
            )
        self._activity(mission_id, "mission_started", "Mission started: %s" % mission.title, now)
        return True

    def start(self, mission_id: str) -> bool:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise MissionError("governance: %s" % reason)
        return self.begin(mission_id)

    def get_mission(self, mission_id: str) -> Optional[MissionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return _mission_from_row(row) if row else None

    def missions(self, *, status: Optional[str] = None, limit: int = 100) -> Tuple[MissionRecord, ...]:
        with self._connection_factory() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM org_missions WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_missions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_mission_from_row(r) for r in rows)

    # ---- helpers ----

    def _upsert_mission(self, record: MissionRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_missions (
                    mission_id, title, objective, owner, mission_leader, sponsoring_user,
                    project, workspace, priority, status, scope, constraints,
                    privacy_classification, risk, approved_tools, prohibited_tools,
                    model_policy, budget, assigned_units, assigned_agents,
                    required_reviewers, progress, health, start_time, target_time,
                    completion_time, outcome, final_outcome_summary, scope_change_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.mission_id, record.title, record.objective, record.owner,
                    record.mission_leader, record.sponsoring_user, record.project,
                    record.workspace, record.priority, record.status,
                    "|".join(record.scope), "|".join(record.constraints),
                    record.privacy_classification, record.risk,
                    "|".join(record.approved_tools), "|".join(record.prohibited_tools),
                    record.model_policy, record.budget.model_dump_json(),
                    "|".join(record.assigned_units), "|".join(record.assigned_agents),
                    "|".join(record.required_reviewers), record.progress, record.health,
                    record.start_time, record.target_time, record.completion_time,
                    record.outcome, record.final_outcome_summary, record.scope_change_count,
                    record.created_at, record.updated_at,
                ),
            )

    def _upsert_charter(self, record: MissionCharter) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_charters (
                    charter_id, mission_id, objective, business_value, success_criteria,
                    non_goals, constraints, assumptions, known_evidence, unknowns,
                    project_scope, privacy_scope, risk, required_capabilities,
                    proposed_agents, proposed_tools, proposed_model_policy,
                    expected_artifacts, expected_validation, expected_user_decisions,
                    budget, cancellation_behavior, rollback_approach, approved,
                    approved_by, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.charter_id, record.mission_id, record.objective,
                    record.business_value, "|".join(record.success_criteria),
                    "|".join(record.non_goals), "|".join(record.constraints),
                    "|".join(record.assumptions), "|".join(record.known_evidence),
                    "|".join(record.unknowns), "|".join(record.project_scope),
                    record.privacy_scope, record.risk,
                    "|".join(record.required_capabilities),
                    "|".join(record.proposed_agents), "|".join(record.proposed_tools),
                    record.proposed_model_policy, "|".join(record.expected_artifacts),
                    "|".join(record.expected_validation),
                    "|".join(record.expected_user_decisions),
                    record.budget.model_dump_json(), record.cancellation_behavior,
                    record.rollback_approach, int(record.approved), record.approved_by,
                    record.version, record.created_at, record.updated_at,
                ),
            )

    def _upsert_plan(self, record: MissionPlan) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_plans (
                    plan_id, mission_id, workstreams, task_ids, dependencies,
                    parallel_opportunities, required_specialists, required_reviews,
                    required_approvals, validation_gates, decision_points,
                    rollback_points, uncertainty, likely_blockers, evidence_used,
                    informed_by_memory, informed_by_repository, version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.plan_id, record.mission_id, "|".join(record.workstreams),
                    "|".join(record.task_ids), "|".join(record.dependencies),
                    "|".join(record.parallel_opportunities),
                    "|".join(record.required_specialists),
                    "|".join(record.required_reviews), "|".join(record.required_approvals),
                    "|".join(record.validation_gates), "|".join(record.decision_points),
                    "|".join(record.rollback_points), "|".join(record.uncertainty),
                    "|".join(record.likely_blockers), "|".join(record.evidence_used),
                    int(record.informed_by_memory), int(record.informed_by_repository),
                    record.version, record.created_at,
                ),
            )

    def _upsert_task(self, record: MissionTask) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_tasks (
                    task_id, mission_id, title, objective, owner, assigned_agent,
                    collaborators, project, scope, expected_inputs, expected_outputs,
                    dependencies, blocking_dependencies, privacy_classification, risk,
                    tool_requirements, model_requirements, context_requirements, budget,
                    iteration_limit, timeout_minutes, validation_requirements,
                    review_requirements, approval_requirements, status, progress_note,
                    final_result, failure_reason, retry_count, depth, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id, record.mission_id, record.title, record.objective,
                    record.owner, record.assigned_agent, "|".join(record.collaborators),
                    record.project, "|".join(record.scope), "|".join(record.expected_inputs),
                    "|".join(record.expected_outputs), "|".join(record.dependencies),
                    "|".join(record.blocking_dependencies), record.privacy_classification,
                    record.risk, "|".join(record.tool_requirements),
                    "|".join(record.model_requirements), "|".join(record.context_requirements),
                    record.budget.model_dump_json(), record.iteration_limit,
                    record.timeout_minutes, "|".join(record.validation_requirements),
                    "|".join(record.review_requirements), "|".join(record.approval_requirements),
                    record.status, record.progress_note, record.final_result,
                    record.failure_reason, record.retry_count, record.depth,
                    record.created_at, record.updated_at,
                ),
            )

    def _activity(self, mission_id: str, kind: str, summary: str, now: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_activity (
                    event_id, kind, summary, mission_id, refs, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("evt_" + _id("activity", mission_id, kind, summary)[:22], kind, summary, mission_id, mission_id or "", now),
            )

    # ---- graph algorithms ----

    def _find_cycles(self, deps: Tuple[TaskDependency, ...]) -> Tuple[Tuple[str, ...], ...]:
        adj: Dict[str, List[str]] = defaultdict(list)
        for d in deps:
            adj[d.source_task_id].append(d.target_task_id)
        visiting: set = set()
        visited: set = set()
        stack: List[str] = []
        cycles: List[Tuple[str, ...]] = []

        def dfs(node: str):
            visiting.add(node)
            stack.append(node)
            for nxt in adj.get(node, []):
                if nxt in visiting:
                    idx = stack.index(nxt)
                    cycle = tuple(stack[idx:] + [nxt])
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif nxt not in visited:
                    dfs(nxt)
            stack.pop()
            visiting.discard(node)
            visited.add(node)

        for node in list(adj.keys()):
            if node not in visited:
                dfs(node)
        return tuple(cycles)

    def _critical_path(self, tasks: Tuple[MissionTask, ...], deps: Tuple[TaskDependency, ...] = (), only_tasks=True) -> Tuple[str, ...]:
        dep_graph: Dict[str, List[str]] = defaultdict(list)
        for d in deps:
            dep_graph[d.target_task_id].append(d.source_task_id)
        # longest chain, cycle-safe
        memo: Dict[str, int] = {}
        visiting: set = set()
        order: Dict[str, int] = {}

        def length(node: str) -> int:
            if node in memo:
                return memo[node]
            if node in visiting:
                return 0
            visiting.add(node)
            best = 1
            for p in dep_graph.get(node, []):
                best = max(best, 1 + length(p))
            visiting.discard(node)
            memo[node] = best
            return best

        for t in tasks:
            order[t.task_id] = length(t.task_id)
        return tuple(sorted(order.keys(), key=lambda k: order[k], reverse=True))

    def _parallel_groups(self, tasks: Tuple[MissionTask, ...], deps: Tuple[TaskDependency, ...]) -> Tuple[Tuple[str, ...], ...]:
        # Group tasks by the set of unmet blocking parents; tasks with no
        # unsatisfied required dependency form the first parallel batch.
        parents: Dict[str, set] = defaultdict(set)
        satisfied: Dict[str, set] = defaultdict(set)
        for d in deps:
            parents[d.source_task_id].add(d.target_task_id)
        by_id = {t.task_id: t for t in tasks}
        groups: List[Tuple[str, ...]] = []
        pending = set(by_id.keys())
        while pending:
            batch = {
                tid for tid in pending
                if not parents.get(tid, set()) - satisfied.get(tid, set())
            }
            if not batch:
                break
            groups.append(tuple(sorted(batch)))
            pending -= batch
            for tid in batch:
                for child in by_id:
                    if tid in parents.get(child, set()):
                        satisfied.setdefault(child, set()).add(tid)
        return tuple(groups[:32])


def default_budget() -> ResourceBudget:
    return ResourceBudget(agent_count=8, delegation_depth=3, retry_count=2, review_rounds=8)


def _budget_from_json(raw: str) -> ResourceBudget:
    try:
        return ResourceBudget.model_validate_json(raw or "{}")
    except Exception:
        return default_budget()


# ---- row mappers ----

def _mission_from_row(row) -> MissionRecord:
    import json
    budget = ResourceBudget()
    try:
        budget = ResourceBudget.model_validate_json(row["budget"] or "{}")
    except Exception:
        pass
    return MissionRecord(
        mission_id=row["mission_id"], title=row["title"], objective=row["objective"],
        owner=row["owner"], mission_leader=row["mission_leader"],
        sponsoring_user=row["sponsoring_user"], project=row["project"],
        workspace=row["workspace"], priority=row["priority"], status=row["status"],
        scope=tuple(x for x in row["scope"].split("|") if x),
        constraints=tuple(x for x in row["constraints"].split("|") if x),
        privacy_classification=row["privacy_classification"], risk=row["risk"],
        approved_tools=tuple(x for x in row["approved_tools"].split("|") if x),
        prohibited_tools=tuple(x for x in row["prohibited_tools"].split("|") if x),
        model_policy=row["model_policy"], budget=budget,
        assigned_units=tuple(x for x in row["assigned_units"].split("|") if x),
        assigned_agents=tuple(x for x in row["assigned_agents"].split("|") if x),
        required_reviewers=tuple(x for x in row["required_reviewers"].split("|") if x),
        progress=row["progress"], health=row["health"], start_time=row["start_time"],
        target_time=row["target_time"], completion_time=row["completion_time"],
        outcome=row["outcome"], final_outcome_summary=row["final_outcome_summary"],
        scope_change_count=row["scope_change_count"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _task_from_row(row) -> MissionTask:
    budget = _budget_from_json(row["budget"])
    return MissionTask(
        task_id=row["task_id"], mission_id=row["mission_id"], title=row["title"],
        objective=row["objective"], owner=row["owner"], assigned_agent=row["assigned_agent"],
        collaborators=tuple(x for x in row["collaborators"].split("|") if x),
        project=row["project"], scope=tuple(x for x in row["scope"].split("|") if x),
        expected_inputs=tuple(x for x in row["expected_inputs"].split("|") if x),
        expected_outputs=tuple(x for x in row["expected_outputs"].split("|") if x),
        dependencies=tuple(x for x in row["dependencies"].split("|") if x),
        blocking_dependencies=tuple(x for x in row["blocking_dependencies"].split("|") if x),
        privacy_classification=row["privacy_classification"], risk=row["risk"],
        tool_requirements=tuple(x for x in row["tool_requirements"].split("|") if x),
        model_requirements=tuple(x for x in row["model_requirements"].split("|") if x),
        context_requirements=tuple(x for x in row["context_requirements"].split("|") if x),
        budget=budget,
        iteration_limit=row["iteration_limit"], timeout_minutes=row["timeout_minutes"],
        validation_requirements=tuple(x for x in row["validation_requirements"].split("|") if x),
        review_requirements=tuple(x for x in row["review_requirements"].split("|") if x),
        approval_requirements=tuple(x for x in row["approval_requirements"].split("|") if x),
        status=row["status"], progress_note=row["progress_note"],
        final_result=row["final_result"], failure_reason=row["failure_reason"],
        retry_count=row["retry_count"], depth=row["depth"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _charter_from_row(row) -> MissionCharter:
    return MissionCharter(
        charter_id=row["charter_id"], mission_id=row["mission_id"],
        objective=row["objective"], business_value=row["business_value"],
        success_criteria=tuple(x for x in row["success_criteria"].split("|") if x),
        non_goals=tuple(x for x in row["non_goals"].split("|") if x),
        constraints=tuple(x for x in row["constraints"].split("|") if x),
        assumptions=tuple(x for x in row["assumptions"].split("|") if x),
        known_evidence=tuple(x for x in row["known_evidence"].split("|") if x),
        unknowns=tuple(x for x in row["unknowns"].split("|") if x),
        project_scope=tuple(x for x in row["project_scope"].split("|") if x),
        privacy_scope=row["privacy_scope"], risk=row["risk"],
        required_capabilities=tuple(x for x in row["required_capabilities"].split("|") if x),
        proposed_agents=tuple(x for x in row["proposed_agents"].split("|") if x),
        proposed_tools=tuple(x for x in row["proposed_tools"].split("|") if x),
        proposed_model_policy=row["proposed_model_policy"],
        expected_artifacts=tuple(x for x in row["expected_artifacts"].split("|") if x),
        expected_validation=tuple(x for x in row["expected_validation"].split("|") if x),
        expected_user_decisions=tuple(x for x in row["expected_user_decisions"].split("|") if x),
        budget=_budget_from_json(row["budget"]),
        cancellation_behavior=row["cancellation_behavior"],
        rollback_approach=row["rollback_approach"], approved=bool(row["approved"]),
        approved_by=row["approved_by"], version=row["version"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _plan_from_row(row) -> MissionPlan:
    return MissionPlan(
        plan_id=row["plan_id"], mission_id=row["mission_id"],
        workstreams=tuple(x for x in row["workstreams"].split("|") if x),
        task_ids=tuple(x for x in row["task_ids"].split("|") if x),
        dependencies=tuple(x for x in row["dependencies"].split("|") if x),
        parallel_opportunities=tuple(x for x in row["parallel_opportunities"].split("|") if x),
        required_specialists=tuple(x for x in row["required_specialists"].split("|") if x),
        required_reviews=tuple(x for x in row["required_reviews"].split("|") if x),
        required_approvals=tuple(x for x in row["required_approvals"].split("|") if x),
        validation_gates=tuple(x for x in row["validation_gates"].split("|") if x),
        decision_points=tuple(x for x in row["decision_points"].split("|") if x),
        rollback_points=tuple(x for x in row["rollback_points"].split("|") if x),
        uncertainty=tuple(x for x in row["uncertainty"].split("|") if x),
        likely_blockers=tuple(x for x in row["likely_blockers"].split("|") if x),
        evidence_used=tuple(x for x in row["evidence_used"].split("|") if x),
        informed_by_memory=bool(row["informed_by_memory"]),
        informed_by_repository=bool(row["informed_by_repository"]),
        version=row["version"], created_at=row["created_at"],
    )


def _budget_from_json(raw: str) -> ResourceBudget:
    try:
        return ResourceBudget.model_validate_json(raw or "{}")
    except Exception:
        return default_budget()