"""Versioned SQLite storage for the Multi-Agent Collaboration platform.

The organizational database lives under the JoeOS data directory and stores
identity, policy, mission, collaboration, and audit metadata. Secrets and
hidden reasoning are never stored. Storage is versioned and refuses to load a
newer schema rather than silently corrupting.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("agents_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS org_organization (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT 'user',
    mode TEXT NOT NULL DEFAULT 'personal',
    state TEXT NOT NULL DEFAULT 'enabled',
    default_mission_leader TEXT,
    default_escalation_path TEXT NOT NULL DEFAULT 'user',
    default_review_policy TEXT NOT NULL DEFAULT 'independent_when_high_risk',
    policy_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_units (
    unit_id TEXT PRIMARY KEY,
    unit_type TEXT NOT NULL,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    parent_unit TEXT,
    leader TEXT,
    capabilities TEXT NOT NULL DEFAULT '',
    escalation_target TEXT,
    supported_mission_types TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_roles (
    role_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    responsibilities TEXT NOT NULL DEFAULT '',
    required_capabilities TEXT NOT NULL DEFAULT '',
    preferred_capabilities TEXT NOT NULL DEFAULT '',
    allowed_workload_classes TEXT NOT NULL DEFAULT '',
    preferred_model_profile TEXT NOT NULL DEFAULT '',
    allowed_tools TEXT NOT NULL DEFAULT '',
    prohibited_tools TEXT NOT NULL DEFAULT '',
    required_review_relationships TEXT NOT NULL DEFAULT '',
    escalation_path TEXT NOT NULL DEFAULT 'user',
    maximum_delegation_depth INTEGER NOT NULL DEFAULT 2,
    default_task_limits TEXT NOT NULL DEFAULT '',
    quality_criteria TEXT NOT NULL DEFAULT '',
    memory_access_policy TEXT NOT NULL DEFAULT 'scoped',
    privacy_restrictions TEXT NOT NULL DEFAULT '',
    role_version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_agents (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role_id TEXT NOT NULL,
    department TEXT,
    team TEXT,
    status TEXT NOT NULL DEFAULT 'configured',
    availability TEXT NOT NULL DEFAULT 'offline',
    capabilities TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    model_preferences TEXT NOT NULL DEFAULT '',
    runtime_restrictions TEXT NOT NULL DEFAULT '',
    tool_permissions TEXT NOT NULL DEFAULT '',
    project_restrictions TEXT NOT NULL DEFAULT '',
    privacy_restrictions TEXT NOT NULL DEFAULT '',
    memory_scope TEXT NOT NULL DEFAULT 'mission_scoped',
    maximum_workload INTEGER NOT NULL DEFAULT 4,
    current_mission TEXT,
    current_task TEXT,
    queue_depth INTEGER NOT NULL DEFAULT 0,
    reliability_state TEXT NOT NULL DEFAULT 'insufficient_history',
    config_version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_agents_role ON org_agents(role_id);
CREATE INDEX IF NOT EXISTS idx_org_agents_status ON org_agents(status);

CREATE TABLE IF NOT EXISTS org_missions (
    mission_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'user',
    mission_leader TEXT,
    sponsoring_user TEXT NOT NULL DEFAULT 'user',
    project TEXT,
    workspace TEXT NOT NULL DEFAULT 'personal',
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'draft',
    scope TEXT NOT NULL DEFAULT '',
    constraints TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    risk TEXT NOT NULL DEFAULT 'low',
    approved_tools TEXT NOT NULL DEFAULT '',
    prohibited_tools TEXT NOT NULL DEFAULT '',
    model_policy TEXT NOT NULL DEFAULT 'local_first',
    budget TEXT NOT NULL DEFAULT '{}',
    assigned_units TEXT NOT NULL DEFAULT '',
    assigned_agents TEXT NOT NULL DEFAULT '',
    required_reviewers TEXT NOT NULL DEFAULT '',
    progress TEXT NOT NULL DEFAULT 'not_started',
    health TEXT NOT NULL DEFAULT 'healthy',
    start_time TEXT,
    target_time TEXT,
    completion_time TEXT,
    outcome TEXT,
    final_outcome_summary TEXT NOT NULL DEFAULT '',
    scope_change_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_missions_status ON org_missions(status);

CREATE TABLE IF NOT EXISTS org_charters (
    charter_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    business_value TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    non_goals TEXT NOT NULL DEFAULT '',
    constraints TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    known_evidence TEXT NOT NULL DEFAULT '',
    unknowns TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT '',
    privacy_scope TEXT NOT NULL DEFAULT 'mission_scoped',
    risk TEXT NOT NULL DEFAULT 'low',
    required_capabilities TEXT NOT NULL DEFAULT '',
    proposed_agents TEXT NOT NULL DEFAULT '',
    proposed_tools TEXT NOT NULL DEFAULT '',
    proposed_model_policy TEXT NOT NULL DEFAULT 'local_first',
    expected_artifacts TEXT NOT NULL DEFAULT '',
    expected_validation TEXT NOT NULL DEFAULT '',
    expected_user_decisions TEXT NOT NULL DEFAULT '',
    budget TEXT NOT NULL DEFAULT '{}',
    cancellation_behavior TEXT NOT NULL DEFAULT 'preserve_state',
    rollback_approach TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_charters_mission ON org_charters(mission_id);

CREATE TABLE IF NOT EXISTS org_plans (
    plan_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    workstreams TEXT NOT NULL DEFAULT '',
    task_ids TEXT NOT NULL DEFAULT '',
    dependencies TEXT NOT NULL DEFAULT '',
    parallel_opportunities TEXT NOT NULL DEFAULT '',
    required_specialists TEXT NOT NULL DEFAULT '',
    required_reviews TEXT NOT NULL DEFAULT '',
    required_approvals TEXT NOT NULL DEFAULT '',
    validation_gates TEXT NOT NULL DEFAULT '',
    decision_points TEXT NOT NULL DEFAULT '',
    rollback_points TEXT NOT NULL DEFAULT '',
    uncertainty TEXT NOT NULL DEFAULT '',
    likely_blockers TEXT NOT NULL DEFAULT '',
    evidence_used TEXT NOT NULL DEFAULT '',
    informed_by_memory INTEGER NOT NULL DEFAULT 0,
    informed_by_repository INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_plans_mission ON org_plans(mission_id);

CREATE TABLE IF NOT EXISTS org_tasks (
    task_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    owner TEXT,
    assigned_agent TEXT,
    collaborators TEXT NOT NULL DEFAULT '',
    project TEXT,
    scope TEXT NOT NULL DEFAULT '',
    expected_inputs TEXT NOT NULL DEFAULT '',
    expected_outputs TEXT NOT NULL DEFAULT '',
    dependencies TEXT NOT NULL DEFAULT '',
    blocking_dependencies TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    risk TEXT NOT NULL DEFAULT 'low',
    tool_requirements TEXT NOT NULL DEFAULT '',
    model_requirements TEXT NOT NULL DEFAULT '',
    context_requirements TEXT NOT NULL DEFAULT '',
    budget TEXT NOT NULL DEFAULT '{}',
    iteration_limit INTEGER,
    timeout_minutes INTEGER,
    validation_requirements TEXT NOT NULL DEFAULT '',
    review_requirements TEXT NOT NULL DEFAULT '',
    approval_requirements TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'not_started',
    progress_note TEXT NOT NULL DEFAULT '',
    final_result TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_tasks_mission ON org_tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_org_tasks_status ON org_tasks(status);

CREATE TABLE IF NOT EXISTS org_task_dependencies (
    dependency_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    source_task_id TEXT NOT NULL,
    target_task_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    optional INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_deps_mission ON org_task_dependencies(mission_id);

CREATE TABLE IF NOT EXISTS org_assignments (
    assignment_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role_match REAL NOT NULL,
    capability_match REAL NOT NULL,
    model_match INTEGER NOT NULL DEFAULT 0,
    permission_match INTEGER NOT NULL DEFAULT 0,
    workload_state TEXT NOT NULL DEFAULT '',
    rejected_alternatives TEXT NOT NULL DEFAULT '',
    review_relationship TEXT,
    warnings TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_messages (
    message_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    thread_kind TEXT NOT NULL DEFAULT 'direct',
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '',
    related_evidence TEXT NOT NULL DEFAULT '',
    related_artifacts TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    requires_acknowledgement INTEGER NOT NULL DEFAULT 0,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    response_deadline TEXT,
    trace_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    redacted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_org_messages_mission ON org_messages(mission_id);
CREATE INDEX IF NOT EXISTS idx_org_messages_recipient ON org_messages(recipient);

CREATE TABLE IF NOT EXISTS org_handoffs (
    handoff_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    source_task_id TEXT,
    destination_task_id TEXT,
    sending_agent TEXT NOT NULL,
    receiving_agent TEXT NOT NULL,
    objective TEXT NOT NULL,
    completed_work TEXT NOT NULL DEFAULT '',
    incomplete_work TEXT NOT NULL DEFAULT '',
    artifacts TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    decisions TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    risks TEXT NOT NULL DEFAULT '',
    open_questions TEXT NOT NULL DEFAULT '',
    recommended_next_action TEXT NOT NULL DEFAULT '',
    required_validation TEXT NOT NULL DEFAULT '',
    scope_limitations TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    state TEXT NOT NULL DEFAULT 'sent',
    response_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_handoffs_mission ON org_handoffs(mission_id);

CREATE TABLE IF NOT EXISTS org_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    producer TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    project TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    storage_reference TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    authority TEXT NOT NULL DEFAULT 'proposed',
    review_state TEXT NOT NULL DEFAULT 'unreviewed',
    validation_state TEXT NOT NULL DEFAULT 'none',
    source_inputs TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    superseded_state TEXT NOT NULL DEFAULT 'current',
    deletion_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_artifacts_mission ON org_artifacts(mission_id);

CREATE TABLE IF NOT EXISTS org_reviews (
    review_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    task_id TEXT,
    gate_id TEXT,
    reviewer TEXT NOT NULL,
    implementer TEXT NOT NULL,
    artifacts TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    runtime TEXT NOT NULL DEFAULT '',
    findings TEXT NOT NULL DEFAULT '',
    independence INTEGER NOT NULL DEFAULT 1,
    disclosure TEXT NOT NULL DEFAULT '',
    conclusion TEXT NOT NULL DEFAULT 'fail',
    confidence TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'requested',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_gates (
    gate_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    task_id TEXT,
    gate_type TEXT NOT NULL,
    required_reviewer_role TEXT NOT NULL,
    independence_required INTEGER NOT NULL DEFAULT 1,
    required_evidence TEXT NOT NULL DEFAULT '',
    required_validation TEXT NOT NULL DEFAULT '',
    pass_criteria TEXT NOT NULL DEFAULT '',
    failure_criteria TEXT NOT NULL DEFAULT '',
    waiver_policy TEXT NOT NULL DEFAULT 'no_waiver',
    state TEXT NOT NULL DEFAULT 'not_ready',
    reviewer TEXT,
    findings TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    approval TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_disagreements (
    disagreement_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    task_id TEXT,
    participants TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    positions TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    affected_decision TEXT,
    urgency TEXT NOT NULL DEFAULT 'normal',
    state TEXT NOT NULL DEFAULT 'open',
    resolution_method TEXT,
    resolution_notes TEXT NOT NULL DEFAULT '',
    escalated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_consensus (
    consensus_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL,
    positions TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    conclusion TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'advisory',
    dissent TEXT NOT NULL DEFAULT '',
    abstentions TEXT NOT NULL DEFAULT '',
    unresolved_concerns TEXT NOT NULL DEFAULT '',
    final_decision_source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_debates (
    debate_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '',
    max_rounds INTEGER NOT NULL DEFAULT 4,
    max_tokens INTEGER NOT NULL DEFAULT 20000,
    time_limit_minutes INTEGER NOT NULL DEFAULT 60,
    round_count INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'open',
    synthesis TEXT NOT NULL DEFAULT '',
    escalation_path TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_consultations (
    consultation_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    requester TEXT NOT NULL,
    specialist TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    constraints TEXT NOT NULL DEFAULT '',
    required_expertise TEXT NOT NULL DEFAULT '',
    tool_use_allowed INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    budget TEXT NOT NULL DEFAULT '{}',
    response TEXT NOT NULL DEFAULT '',
    conclusion TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'medium',
    limitations TEXT NOT NULL DEFAULT '',
    recommended_action TEXT NOT NULL DEFAULT '',
    affected_risk TEXT NOT NULL DEFAULT 'low',
    state TEXT NOT NULL DEFAULT 'requested',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_escalations (
    escalation_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    evidence TEXT NOT NULL DEFAULT '',
    attempted_resolutions TEXT NOT NULL DEFAULT '',
    required_decision TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '',
    consequence_of_delay TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    responsible_recipient TEXT NOT NULL DEFAULT 'user',
    state TEXT NOT NULL DEFAULT 'open',
    response TEXT NOT NULL DEFAULT '',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_interventions (
    intervention_id TEXT PRIMARY KEY,
    need TEXT NOT NULL,
    rationale TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    options TEXT NOT NULL DEFAULT '',
    recommended_option TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT 'low',
    consequence TEXT NOT NULL DEFAULT '',
    deadline TEXT,
    work_can_continue INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending',
    response TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_approvals (
    approval_id TEXT PRIMARY KEY,
    requester TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    action TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT 'low',
    self_approval_blocked INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending',
    approver TEXT NOT NULL DEFAULT '',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_routes (
    route_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    mission_id TEXT,
    task_id TEXT,
    required_capabilities TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'local',
    rationale TEXT NOT NULL,
    tool_use_required INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'proposed',
    disclosure TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_detections (
    detection_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    mission_id TEXT,
    task_ids TEXT NOT NULL DEFAULT '',
    agent_ids TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_memory_proposals (
    proposal_id TEXT PRIMARY KEY,
    mission_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    proposer TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'proposed',
    reviewer TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    cancellations INTEGER NOT NULL DEFAULT 0,
    validation_pass_rate REAL NOT NULL DEFAULT 1.0,
    review_acceptance_rate REAL NOT NULL DEFAULT 1.0,
    rework_count INTEGER NOT NULL DEFAULT 0,
    average_task_minutes REAL NOT NULL DEFAULT 0.0,
    timeout_count INTEGER NOT NULL DEFAULT 0,
    budget_overrun_count INTEGER NOT NULL DEFAULT 0,
    tool_failure_count INTEGER NOT NULL DEFAULT 0,
    model_failure_count INTEGER NOT NULL DEFAULT 0,
    escalation_count INTEGER NOT NULL DEFAULT 0,
    handoff_rejection_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_org_perf_agent ON org_performance(agent_id);

CREATE TABLE IF NOT EXISTS org_activity (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    mission_id TEXT,
    refs TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_org_activity_mission ON org_activity(mission_id);

CREATE TABLE IF NOT EXISTS org_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class AgentsStorage:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "agents.db"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self._path))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            self._local.connection = connection
        return connection

    def prepare(self) -> None:
        with self._lock:
            connection = self.connect()
            self._verify_or_migrate(connection)
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO agents_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM agents_meta WHERE key = 'agents_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "agents storage version %d is newer than supported version %d" % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "agents storage version %d predates supported version %d; migration required" % (current, STORAGE_VERSION)
                )

    def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def path(self) -> str:
        return str(self._path)

    def backup_to(self, target_dir: str) -> Optional[str]:
        target = Path(target_dir) / ("agents-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8])
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)
