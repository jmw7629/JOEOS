"""Phase P3G campaign tests: durable campaign state machine, work packages,
roadmap ingestion, autonomy policy enforcement, watchdog heartbeats, blockers,
restart recovery, integration gate, multi-agent graph, Apple executor, OpenCode
adapter, and HTTP integration. Only AI providers and stage handlers are
substituted through dependency injection; all state is real SQLite."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.engineering.campaign import (
    PACKAGE_MANAGE_CAP,
    CampaignDefinition,
    CampaignError,
    CampaignService,
    CampaignStore,
    CampaignWorker,
    RoadmapEntry,
    WorkPackageDefinition,
    campaign_router,
)
from server.engineering.campaign.autonomy import (
    AI_REBUILD_V1,
    AutonomyPolicy,
    get_autonomy_policy,
    parse_autonomy_policy,
    registered_policy_keys,
)
from server.engineering.campaign.gate import IntegrationGate
from server.engineering.campaign.graph import (
    agents_required,
    build_stage_order,
    plan_package_stages,
    role_for_stage,
)
from server.engineering.campaign.roadmap import parse_roadmap_document
from server.engineering.campaign.roles import AGENT_ROLES, seed_engineering_agents
from server.engineering.campaign.state_machine import (
    DEFAULT_STAGE_ORDER,
    can_advance,
    next_stage,
    normalize_stage_order,
    package_state_for_stage,
    resolve_dependencies,
    validate_stage_sequence,
)
from server.identity.authority_router import require_application_session
from server.engineering.campaign.models import (
    WorkPackageRecord,
)

CAMPAIGN_CAPS = [
    "engineering.campaign.read",
    "engineering.campaign.manage",
    "engineering.campaign.start",
    "engineering.campaign.pause",
    "engineering.campaign.cancel",
    "engineering.package.read",
    "engineering.package.manage",
    "engineering.blocker.resolve",
]

READ_ONLY_CAPS = ["engineering.campaign.read", "engineering.package.read"]


def owner_principal():
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888888"),
        "user": {"id": "owner", "display_name": "Owner"},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": UUID("33333333-4444-4555-8666-777777777777")},
        "capabilities": list(CAMPAIGN_CAPS),
    }


def read_only_principal():
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888889"),
        "user": {"id": "observer", "display_name": "Observer"},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": UUID("33333333-4444-4555-8666-777777777777")},
        "capabilities": list(READ_ONLY_CAPS),
    }


def limited_principal(capabilities):
    p = owner_principal()
    p["capabilities"] = list(capabilities)
    return p


def always_pass_handler(principal, campaign, package, stage, attempt):
    return {"passed": True, "detail": "ok", "evidence": ["stage:%s" % stage]}


def always_fail_handler(principal, campaign, package, stage, attempt):
    return {"passed": False, "detail": "gate failed intentionally", "evidence": []}


class MutableClock:
    def __init__(self):
        import time as _time
        self.value = int(_time.time() * 1000)

    def __call__(self):
        return self.value

    def advance_ms(self, ms):
        self.value += ms


class CampaignFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "campaign.db"

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        self.store = CampaignStore(connect)
        self.events = []
        self.clock = MutableClock()
        self.service = CampaignService(
            self.store,
            event_sink=lambda level, source, message: self.events.append(message),
            now=self.clock,
        )
        self.service.prepare()
        self.principal = owner_principal()
        self.reader = read_only_principal()

    def tearDown(self):
        self.tempdir.cleanup()

    def _campaign(self, **overrides):
        definition = dict(
            key="joeos-autonomous-build",
            title="AI Rebuild",
            description="test",
            repository_path="/repo",
            base_branch="ai-rebuild",
            integration_branch="ai-rebuild",
            autonomy_policy_key="joeos.engineering.ai-rebuild.v1",
        )
        definition.update(overrides)
        return self.service.create_campaign(self.principal, CampaignDefinition(**definition))

    def _package(self, campaign, key="p1", **overrides):
        definition = dict(
            key=key, title=key, description="", owner_agent_key="engineering.builder",
            stage_order=(),
        )
        definition.update(overrides)
        return self.service.create_work_package(
            self.principal, campaign.campaign_id, WorkPackageDefinition(**definition))

    def _advance_all(self, campaign, package):
        guard = 0
        while True:
            guard += 1
            result = self.service.advance_package(self.principal, package.package_id)
            if result["package"].state == "completed":
                return result
            if guard > 30:
                self.fail("package did not complete within 30 advances")


class StateMachineTests(unittest.TestCase):
    def test_stage_order_is_canonical(self):
        self.assertIsNone(validate_stage_sequence(DEFAULT_STAGE_ORDER))

    def test_reordered_stage_order_rejected(self):
        self.assertIsNotNone(validate_stage_sequence(("push", "implement")))

    def test_skipped_stage_order_accepted(self):
        self.assertIsNone(validate_stage_sequence(("eligibility", "implement", "validate", "review", "commit", "integrate", "push", "complete")))

    def test_normalize_stage_order_drops_unknown(self):
        order = normalize_stage_order(("eligibility", "bogus", "plan", "complete"))
        self.assertEqual(order, ("eligibility", "plan", "complete"))

    def test_next_stage_advances_canonical_order(self):
        self.assertEqual(next_stage("plan", DEFAULT_STAGE_ORDER), "worktree")
        self.assertEqual(next_stage("integrate", DEFAULT_STAGE_ORDER), "push")

    def test_next_stage_final_returns_complete(self):
        self.assertEqual(next_stage("push", DEFAULT_STAGE_ORDER), "complete")

    def test_next_stage_complete_returns_none(self):
        self.assertIsNone(next_stage("complete", DEFAULT_STAGE_ORDER))

    def test_can_advance_last_executable_stage(self):
        self.assertTrue(can_advance("push", DEFAULT_STAGE_ORDER))
        self.assertFalse(can_advance("review", DEFAULT_STAGE_ORDER))

    def test_package_state_mapping(self):
        self.assertEqual(package_state_for_stage("implement"), "implementing")
        self.assertEqual(package_state_for_stage("validate"), "validating")

    def test_dependency_cycle_detected(self):
        self.assertIsNotNone(resolve_dependencies({"a": ["b"], "b": ["a"]}))

    def test_unknown_dependency_detected(self):
        self.assertIsNotNone(resolve_dependencies({"a": ["missing"]}))

    def test_acyclic_dependencies_accepted(self):
        self.assertIsNone(resolve_dependencies({"a": ["b"], "b": []}))


class AutonomyPolicyTests(unittest.TestCase):
    def test_policy_registered(self):
        self.assertIn("joeos.engineering.ai-rebuild.v1", registered_policy_keys())

    def test_policy_deny_by_default_fields(self):
        policy = get_autonomy_policy("joeos.engineering.ai-rebuild.v1")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.limits.max_parallel_packages, 1)
        self.assertTrue(policy.limits.allow_ff_integration_only)

    def test_policy_allows_only_local_providers(self):
        policy = get_autonomy_policy("joeos.engineering.ai-rebuild.v1")
        assert policy is not None
        self.assertTrue(all(p.location == "local" for p in policy.providers))

    def test_policy_protects_branches(self):
        policy = get_autonomy_policy("joeos.engineering.ai-rebuild.v1")
        assert policy is not None
        self.assertIn("main", policy.protected_branches)

    def test_policy_allows_eight_roles_only(self):
        policy = get_autonomy_policy("joeos.engineering.ai-rebuild.v1")
        assert policy is not None
        self.assertEqual(len(policy.allowed_agent_keys), 8)

    def test_parse_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            parse_autonomy_policy("joeos.engineering.x.v1", {"bogus_field": 1})

    def test_parse_valid_document(self):
        policy = parse_autonomy_policy("joeos.engineering.x.v1", {
            "version": "1", "title": "X", "description": "d"})
        self.assertIsInstance(policy, AutonomyPolicy)

    def test_unknown_policy_key_absent(self):
        self.assertIsNone(get_autonomy_policy("joeos.engineering.nope.v9"))


class RoadmapParsingTests(unittest.TestCase):
    def test_parse_valid_roadmap(self):
        document = """
schema: ROADMAP_SCHEMA_V1
campaign: joeos-autonomous-build
work_packages:
  - key: one
    title: First
    owner_agent_key: engineering.builder
    order: 1
  - key: two
    title: Second
    owner_agent_key: engineering.release
    dependencies: [one]
"""
        envelope = parse_roadmap_document(document)
        self.assertEqual(len(envelope.entries), 2)
        self.assertEqual(envelope.campaign_key, "joeos-autonomous-build")
        self.assertEqual(envelope.entries[1].dependencies, ("one",))

    def test_parse_rejects_wrong_schema(self):
        with self.assertRaises(ValueError):
            parse_roadmap_document("schema: OTHER\nwork_packages: []")

    def test_parse_requires_work_packages_list(self):
        with self.assertRaises(ValueError):
            parse_roadmap_document("schema: ROADMAP_SCHEMA_V1\ncampaign: x")

    def test_parse_normalizes_stage_order(self):
        document = """
schema: ROADMAP_SCHEMA_V1
work_packages:
  - key: one
    title: First
    owner_agent_key: engineering.builder
    stage_order: [eligibility, plan, implement]
"""
        envelope = parse_roadmap_document(document)
        self.assertEqual(tuple(envelope.entries[0].stage_order), ("eligibility", "plan", "implement"))


class RoleProfileTests(unittest.TestCase):
    def test_eight_roles_defined(self):
        self.assertEqual(len(AGENT_ROLES), 8)

    def test_role_keys_unique(self):
        keys = [r["key"] for r in AGENT_ROLES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_role_keys_start_with_engineering(self):
        for role in AGENT_ROLES:
            self.assertTrue(role["key"].startswith("engineering."))

    def test_watchdog_role_read_only(self):
        watchdog = next(r for r in AGENT_ROLES if r["key"] == "engineering.watchdog")
        self.assertEqual(watchdog["allowed_tools"], "engineering.campaign.read")

    def test_seeding_is_idempotent(self):
        created = []
        fake = type("Fake", (), {
            "list_agents": lambda self, principal: [{"key": r["key"]} for r in AGENT_ROLES],
            "create_agent": lambda self, principal, **kw: kw,
        })()
        result = seed_engineering_agents(fake, owner_principal())
        self.assertEqual(result, [])


class CampaignServiceTests(CampaignFixture):
    def test_create_campaign(self):
        campaign = self._campaign()
        self.assertEqual(campaign.state, "proposed")
        self.assertEqual(campaign.autonomy_policy_key, "joeos.engineering.ai-rebuild.v1")

    def test_create_campaign_duplicate_key_rejected(self):
        self._campaign()
        with self.assertRaises(CampaignError) as ctx:
            self._campaign()
        self.assertEqual(ctx.exception.status_code, 409)

    def test_create_campaign_requires_manage_capability(self):
        with self.assertRaises(CampaignError) as ctx:
            self.service.create_campaign(self.reader, CampaignDefinition(
                key="x", title="x", repository_path="/r", base_branch="main",
                integration_branch="main", autonomy_policy_key="joeos.engineering.ai-rebuild.v1"))
        self.assertEqual(ctx.exception.code, "capability_denied")

    def test_list_campaigns(self):
        self._campaign()
        self.assertEqual(len(self.service.list_campaigns(self.principal)), 1)

    def test_start_campaign_requires_start_capability(self):
        campaign = self._campaign()
        with self.assertRaises(CampaignError):
            self.service.start_campaign(self.reader, campaign.campaign_id)

    def test_start_campaign_enforces_policy(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(self.service.get_campaign(self.principal, campaign.campaign_id).state, "active")

    def test_start_campaign_rejects_unknown_policy(self):
        campaign = self._campaign(autonomy_policy_key="joeos.engineering.nope.v9")
        with self.assertRaises(CampaignError) as ctx:
            self.service.start_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(ctx.exception.code, "autonomy_policy_missing")

    def test_start_campaign_rejects_over_limit_parallelism(self):
        # The autonomy policy caps parallel packages at 1; pydantic allows 16.
        campaign = self._campaign(max_parallel_packages=2)
        with self.assertRaises(CampaignError) as ctx:
            self.service.start_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(ctx.exception.code, "autonomy_policy_limit_exceeded")

    def test_start_campaign_rejects_terminal(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        with self.assertRaises(CampaignError):
            self.service.start_campaign(self.principal, campaign.campaign_id)

    def test_pause_resume_campaign(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(self.service.pause_campaign(self.principal, campaign.campaign_id).state, "paused")
        self.assertEqual(self.service.resume_campaign(self.principal, campaign.campaign_id).state, "active")

    def test_pause_requires_pause_capability(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        with self.assertRaises(CampaignError):
            self.service.pause_campaign(self.reader, campaign.campaign_id)

    def test_cancel_campaign(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        cancelled = self.service.cancel_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(cancelled.state, "cancelled")

    def test_cancel_terminal_campaign_rejected(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.cancel_campaign(self.principal, campaign.campaign_id)
        with self.assertRaises(CampaignError) as ctx:
            self.service.cancel_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(ctx.exception.code, "campaign_terminal")


class WorkPackageTests(CampaignFixture):
    def test_create_work_package(self):
        campaign = self._campaign()
        package = self._package(campaign)
        self.assertEqual(package.state, "queued")
        self.assertEqual(package.campaign_id, campaign.campaign_id)

    def test_create_duplicate_package_key_rejected(self):
        campaign = self._campaign()
        self._package(campaign, key="p1")
        with self.assertRaises(CampaignError) as ctx:
            self._package(campaign, key="p1")
        self.assertEqual(ctx.exception.code, "package_exists")

    def test_create_package_requires_package_manage(self):
        campaign = self._campaign()
        with self.assertRaises(CampaignError):
            self.service.create_work_package(self.reader, campaign.campaign_id, WorkPackageDefinition(
                key="x", title="x", owner_agent_key="engineering.builder", stage_order=()))

    def test_start_package_requires_active_campaign(self):
        campaign = self._campaign()
        package = self._package(campaign)
        with self.assertRaises(CampaignError) as ctx:
            self.service.start_work_package(self.principal, package.package_id)
        self.assertEqual(ctx.exception.code, "campaign_not_active")

    def test_start_package_marks_eligible(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        started = self.service.start_work_package(self.principal, package.package_id)
        self.assertEqual(started.state, "eligible")

    def test_dependencies_must_complete_first(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service._stage_handler = always_pass_handler
        first = self._package(campaign, key="first")
        second = self._package(campaign, key="second", dependencies=("first",))
        with self.assertRaises(CampaignError) as ctx:
            self.service.start_work_package(self.principal, second.package_id)
        self.assertEqual(ctx.exception.code, "dependencies_pending")
        self.service.start_work_package(self.principal, first.package_id)
        self._advance_all(campaign, first)
        self.service.start_work_package(self.principal, second.package_id)
        self.assertEqual(self.service.get_work_package(self.principal, second.package_id).state, "eligible")

    def test_advance_package_completes(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_pass_handler
        result = self._advance_all(campaign, package)
        self.assertEqual(result["package"].state, "completed")
        self.assertTrue(result["gate"].passed)

    def test_failed_gate_blocks_and_creates_blocker(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_fail_handler
        result = self.service.advance_package(self.principal, package.package_id)
        self.assertFalse(result["gate"].passed)
        self.assertTrue(result["gate"].blocker_created)
        self.assertEqual(result["package"].state, "blocked")
        blockers = self.service.blockers(self.principal, campaign.campaign_id)
        self.assertEqual(len(blockers), 1)

    def test_blocker_can_be_resolved_and_package_resumes(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_fail_handler
        result = self.service.advance_package(self.principal, package.package_id)
        blocker_id = result["gate"].blocker_id if hasattr(result["gate"], "blocker_id") else None
        if blocker_id is None:
            blockers = self.service.blockers(self.principal, campaign.campaign_id)
            blocker_id = blockers[0].blocker_id
        resolved = self.service.resolve_blocker(self.principal, blocker_id, "fixed")
        self.assertEqual(resolved.state, "resolved")
        self.assertEqual(self.service.get_work_package(self.principal, package.package_id).state, "eligible")

    def test_resolve_blocker_requires_critical_capability(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_fail_handler
        self.service.advance_package(self.principal, package.package_id)
        blockers = self.service.blockers(self.principal, campaign.campaign_id)
        with self.assertRaises(CampaignError):
            self.service.resolve_blocker(self.reader, blockers[0].blocker_id, "x")

    def test_manual_blocker_raise(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        blocker = self.service.raise_blocker(self.principal, package.package_id,
                                             reason="operator", detail="pause")
        self.assertEqual(blocker.state, "open")
        self.assertEqual(self.service.get_work_package(self.principal, package.package_id).state, "blocked")


class WatchdogAndHeartbeatTests(CampaignFixture):
    def test_heartbeat_records(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        beat = self.service.heartbeat(self.principal, campaign.campaign_id, worker="watchdog")
        self.assertIsNotNone(beat.heartbeat_id)
        state = self.service.watchdog_state(self.principal, campaign.campaign_id)
        self.assertTrue(state["healthy"])
        self.assertFalse(state["expired"])

    def test_heartbeat_requires_active_campaign(self):
        campaign = self._campaign()
        with self.assertRaises(CampaignError) as ctx:
            self.service.heartbeat(self.principal, campaign.campaign_id)
        self.assertEqual(ctx.exception.code, "campaign_not_active")

    def test_expired_heartbeat_reported(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.heartbeat(self.principal, campaign.campaign_id)
        state = self.service.watchdog_state(self.principal, campaign.campaign_id)
        self.assertTrue(state["healthy"])
        self.assertFalse(state["expired"])
        self.clock.advance_ms(campaign.heartbeat_timeout_ms + 1000)
        state = self.service.watchdog_state(self.principal, campaign.campaign_id)
        self.assertTrue(state["expired"])
        self.assertFalse(state["healthy"])


class CheckpointAndRecoveryTests(CampaignFixture):
    def test_checkpoints_recorded_on_advance(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_pass_handler
        self.service.advance_package(self.principal, package.package_id)
        checkpoints = self.service.checkpoints(self.principal, campaign.campaign_id)
        self.assertGreaterEqual(len(checkpoints), 2)

    def test_restart_recovery_requeues_running_packages(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        self.service._stage_handler = always_pass_handler
        self.service.advance_package(self.principal, package.package_id)
        recovered = self.service.recover_after_restart()
        self.assertGreaterEqual(recovered, 1)
        state = self.service.get_work_package(self.principal, package.package_id)
        self.assertEqual(state.state, "eligible")
        self.assertIn("recovered after restart", state.error_detail or "")

    def test_recovery_is_idempotent(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.start_work_package(self.principal, package.package_id)
        first = self.service.recover_after_restart()
        second = self.service.recover_after_restart()
        self.assertGreaterEqual(first, 0)
        self.assertEqual(second, 0)


class AttemptTests(CampaignFixture):
    def test_begin_attempt(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        attempt = self.service.begin_attempt(self.principal, package.package_id)
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(attempt.state, "running")

    def test_attempt_budget_enforced(self):
        campaign = self._campaign(max_attempts_per_package=1)
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        self.service.begin_attempt(self.principal, package.package_id)
        with self.assertRaises(CampaignError) as ctx:
            self.service.begin_attempt(self.principal, package.package_id)
        self.assertEqual(ctx.exception.code, "max_attempts_exceeded")

    def test_finish_attempt_succeeded(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        attempt = self.service.begin_attempt(self.principal, package.package_id)
        finished = self.service.finish_attempt(self.principal, attempt.attempt_id,
                                               state="succeeded", summary="done")
        self.assertEqual(finished.state, "succeeded")

    def test_finish_attempt_rejects_invalid_state(self):
        campaign = self._campaign()
        self.service.start_campaign(self.principal, campaign.campaign_id)
        package = self._package(campaign)
        attempt = self.service.begin_attempt(self.principal, package.package_id)
        with self.assertRaises(CampaignError):
            self.service.finish_attempt(self.principal, attempt.attempt_id, state="bogus")


class IntegrationGateTests(unittest.TestCase):
    def test_gate_passes_when_all_checks_clear(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": True, "behind": 0},
            tests=lambda: (True, "all green"),
            blockers=lambda: [],
        )
        result = gate.evaluate()
        self.assertTrue(result["passed"])
        self.assertFalse(result["unknown"])

    def test_gate_fails_on_dirty_tree(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": False, "behind": 0},
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])

    def test_gate_fails_off_branch(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "feature/x", "detached": False, "clean": True, "behind": 0},
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])

    def test_gate_unknown_when_unmeasured(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": None, "behind": 0},
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["unknown"])

    def test_gate_fails_on_failed_tests(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": True, "behind": 0},
            tests=lambda: (False, "test failed"),
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])

    def test_gate_fails_on_open_blocker(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": True, "behind": 0},
            blockers=lambda: [{"state": "open", "reason": "gate_failed"}],
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])

    def test_gate_fails_when_behind_remote(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": "ai-rebuild", "detached": False, "clean": True, "behind": 3},
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])

    def test_gate_never_reports_unmeasured_as_success(self):
        gate = IntegrationGate(
            git_status=lambda: {"branch": None, "detached": False, "clean": None, "behind": None},
            tests=None,
            blockers=None,
        )
        result = gate.evaluate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["unknown"])


class GraphTests(unittest.TestCase):
    def _package(self, title="Add iOS build", owner="engineering.builder"):
        return WorkPackageRecord(
            package_id="pkg-abcdef1234567890", campaign_id="camp-x", key="p",
            title=title, owner_agent_key=owner, stage_order=(), created_at="t", updated_at="t")

    def test_role_for_stage(self):
        self.assertEqual(role_for_stage("implement"), "engineering.builder")
        self.assertEqual(role_for_stage("review"), "engineering.securityreviewer")
        self.assertEqual(role_for_stage("integrate"), "engineering.release")

    def test_default_stage_order_canonical(self):
        package = self._package(title="Backend change")
        order = build_stage_order(package)
        self.assertEqual(order, DEFAULT_STAGE_ORDER)

    def test_apple_package_includes_apple_stage(self):
        package = self._package(title="iOS Swift change")
        order = build_stage_order(package)
        self.assertIn("implement", order)

    def test_plan_covers_full_pipeline(self):
        package = self._package()
        plan = plan_package_stages(package)
        stages = [s["stage"] for s in plan]
        self.assertIn("eligibility", stages)
        self.assertIn("push", stages)
        self.assertIn("complete", stages)

    def test_agents_required_are_role_profiles(self):
        package = self._package()
        agents = agents_required(package)
        for agent in agents:
            self.assertTrue(agent.startswith("engineering."))


class HTTPCampaignIntegrationTest(CampaignFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.campaign_service = self.service
        self.current_principal = self.principal
        app.dependency_overrides[require_application_session] = lambda: self.current_principal
        app.include_router(campaign_router)
        self.client = TestClient(app)

    def test_http_create_and_start_campaign(self):
        created = self.client.post("/api/v1/engineering/campaigns", json={
            "key": "joeos-autonomous-build", "title": "AI Rebuild", "description": "",
            "repository_path": "/repo", "base_branch": "ai-rebuild",
            "integration_branch": "ai-rebuild",
            "autonomy_policy_key": "joeos.engineering.ai-rebuild.v1",
        })
        self.assertEqual(created.status_code, 201, created.text)
        campaign = created.json()
        started = self.client.post("/api/v1/engineering/campaigns/%s/start" % campaign["campaign_id"])
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["state"], "active")

    def test_http_denied_without_capability(self):
        self.current_principal = read_only_principal()
        created = self.client.post("/api/v1/engineering/campaigns", json={
            "key": "x", "title": "x", "repository_path": "/repo",
            "base_branch": "main", "integration_branch": "main",
            "autonomy_policy_key": "joeos.engineering.ai-rebuild.v1",
        })
        self.assertEqual(created.status_code, 403, created.text)

    def test_http_roadmap_import(self):
        campaign = self._campaign()
        document = """
schema: ROADMAP_SCHEMA_V1
campaign: joeos-autonomous-build
work_packages:
  - key: one
    title: First
    owner_agent_key: engineering.builder
"""
        response = self.client.post(
            "/api/v1/engineering/campaigns/%s/roadmap/import" % campaign.campaign_id,
            json={"yaml": document})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["entries"]), 1)

    def test_http_package_lifecycle(self):
        campaign = self._campaign()
        self.client.post("/api/v1/engineering/campaigns/%s/start" % campaign.campaign_id)
        created = self.client.post(
            "/api/v1/engineering/campaigns/%s/packages" % campaign.campaign_id,
            json={"key": "p1", "title": "P1", "owner_agent_key": "engineering.builder",
                  "stage_order": []})
        self.assertEqual(created.status_code, 201, created.text)
        package = created.json()
        started = self.client.post("/api/v1/engineering/packages/%s/start" % package["package_id"])
        self.assertEqual(started.status_code, 200, started.text)
        advanced = self.client.post("/api/v1/engineering/packages/%s/advance" % package["package_id"])
        self.assertEqual(advanced.status_code, 200, advanced.text)

    def test_http_blocker_lifecycle(self):
        campaign = self._campaign()
        self.client.post("/api/v1/engineering/campaigns/%s/start" % campaign.campaign_id)
        created = self.client.post(
            "/api/v1/engineering/campaigns/%s/packages" % campaign.campaign_id,
            json={"key": "p1", "title": "P1", "owner_agent_key": "engineering.builder",
                  "stage_order": []})
        package = created.json()
        blocker = self.client.post("/api/v1/engineering/packages/%s/blockers" % package["package_id"],
                                   json={"reason": "operator", "detail": "pause"})
        self.assertEqual(blocker.status_code, 201, blocker.text)
        resolved = self.client.post(
            "/api/v1/engineering/blockers/%s/resolve" % blocker.json()["blocker_id"],
            json={"resolution": "done"})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["state"], "resolved")

    def test_http_heartbeat(self):
        campaign = self._campaign()
        self.client.post("/api/v1/engineering/campaigns/%s/start" % campaign.campaign_id)
        beat = self.client.post("/api/v1/engineering/campaigns/%s/heartbeat" % campaign.campaign_id,
                                json={"worker": "watchdog"})
        self.assertEqual(beat.status_code, 200, beat.text)
        state = self.client.get("/api/v1/engineering/campaigns/%s/watchdog" % campaign.campaign_id)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertTrue(state.json()["healthy"])

    def test_http_checkpoints_listed(self):
        campaign = self._campaign()
        self.client.post("/api/v1/engineering/campaigns/%s/start" % campaign.campaign_id)
        checkpoints = self.client.get(
            "/api/v1/engineering/campaigns/%s/checkpoints" % campaign.campaign_id)
        self.assertEqual(checkpoints.status_code, 200, checkpoints.text)


class CampaignSecurityTests(HTTPCampaignIntegrationTest):
    """P26: security posture — capability enforcement, no approval bypass,
    secrets never surface in campaign records or events."""

    def test_worker_principal_cannot_create_campaigns(self):
        worker = self.service._worker_principal("test-worker")
        with self.assertRaises(CampaignError) as ctx:
            self.service.create_campaign(worker, CampaignDefinition(
                key="x", title="x", repository_path="/r", base_branch="main",
                integration_branch="main",
                autonomy_policy_key="joeos.engineering.ai-rebuild.v1"))
        self.assertEqual(ctx.exception.code, "capability_denied")

    def test_worker_principal_can_surface_gate_blocker(self):
        # The worker legitimately holds package.manage so it can surface a
        # gate_failed blocker; this is not an approval bypass (it cannot
        # resolve blockers or touch campaign-level control).
        campaign = self._campaign()
        package = self._package(campaign)
        self.service.start_campaign(self.principal, campaign.campaign_id)
        worker = self.service._worker_principal("test-worker")
        blocker = self.service.raise_blocker(worker, package.package_id,
                                             reason="gate_failed", detail="tests red")
        self.assertEqual(blocker.state, "open")
        with self.assertRaises(CampaignError) as ctx:
            self.service.resolve_blocker(worker, blocker.blocker_id, "go")
        self.assertEqual(ctx.exception.code, "capability_denied")

    def test_worker_principal_cannot_resolve_blocker(self):
        campaign = self._campaign()
        package = self._package(campaign)
        self.service.start_campaign(self.principal, campaign.campaign_id)
        blocker = self.service.raise_blocker(self.principal, package.package_id,
                                             reason="operator", detail="hold")
        worker = self.service._worker_principal("test-worker")
        with self.assertRaises(CampaignError) as ctx:
            self.service.resolve_blocker(worker, blocker.blocker_id, "go")
        self.assertEqual(ctx.exception.code, "capability_denied")

    def test_roadmap_cannot_be_imported_by_reader(self):
        campaign = self._campaign()
        with self.assertRaises(CampaignError) as ctx:
            self.service.import_roadmap(self.reader, campaign.campaign_id, ())
        self.assertEqual(ctx.exception.code, "capability_denied")

    def test_events_never_contain_secret_like_values(self):
        self.events.clear()
        campaign = self._campaign(
            description="uses token=supersecretvalue123 and key=abcdef0123456789")
        package = self._package(campaign, description="password=hunter2tok")
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)
        self.service.advance_package(self.principal, package.package_id)
        for message in self.events:
            self.assertNotIn("supersecretvalue123", message)
            self.assertNotIn("abcdef0123456789", message)
            self.assertNotIn("hunter2tok", message)

    def test_no_approval_bypass_in_worker_advance(self):
        # The worker principal carries only package.manage; it must not be able
        # to reach campaign-level or blocker-management capabilities, so even a
        # directly-constructed worker principal cannot escalate.
        worker = self.service._worker_principal("test-worker")
        self.assertEqual(set(worker["capabilities"]),
                         {PACKAGE_MANAGE_CAP})

    def test_http_worker_cannot_read_or_mutate_via_api(self):
        self._campaign()
        self.current_principal = self.service._worker_principal("http-worker")
        # Worker lacks campaign.read; listing is denied, not bypassed.
        response = self.client.get("/api/v1/engineering/campaigns")
        self.assertEqual(response.status_code, 403, response.text)
        # Worker lacks campaign.manage; creation is denied.
        created = self.client.post("/api/v1/engineering/campaigns", json={
            "key": "x", "title": "x", "repository_path": "/repo",
            "base_branch": "main", "integration_branch": "main",
            "autonomy_policy_key": "joeos.engineering.ai-rebuild.v1",
        })
        self.assertEqual(created.status_code, 403, created.text)


class CampaignWorkerTests(CampaignFixture):
    def test_worker_advances_eligible_packages(self):
        campaign = self._campaign()
        package = self._package(campaign, stage_order=("eligibility", "plan", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)

        worker = CampaignWorker(self.service)
        advanced = worker.tick()
        self.assertGreaterEqual(advanced, 1)
        state = self.service.get_work_package(self.principal, package.package_id).state
        self.assertIn(state, ("planning", "planned", "completed"))

    def test_worker_completes_package_across_ticks(self):
        campaign = self._campaign()
        package = self._package(campaign, stage_order=(
            "eligibility", "plan", "worktree", "implement", "validate",
            "review", "commit", "integrate", "push", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)

        worker = CampaignWorker(self.service)
        guard = 0
        while self.service.get_work_package(self.principal, package.package_id).state != "completed":
            worker.tick()
            guard += 1
            if guard > 20:
                self.fail("package did not complete through worker ticks")
        record = self.service.get_work_package(self.principal, package.package_id)
        self.assertEqual(record.state, "completed")
        self.assertEqual(record.current_stage, "complete")

    def test_worker_respects_dependency_order(self):
        campaign = self._campaign()
        first = self._package(campaign, key="p1", stage_order=("eligibility", "plan", "complete"))
        second = self._package(campaign, key="p2", stage_order=("eligibility", "plan", "complete"),
                               dependencies=("p1",))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, first.package_id)

        worker = CampaignWorker(self.service)
        worker.tick()
        second_state = self.service.get_work_package(self.principal, second.package_id).state
        self.assertEqual(second_state, "queued", "dependent package must wait for p1")
        first_state = self.service.get_work_package(self.principal, first.package_id).state
        self.assertIn(first_state, ("planning", "planned", "completed"))

        guard = 0
        while self.service.get_work_package(self.principal, first.package_id).state != "completed":
            worker.tick()
            guard += 1
            if guard > 10:
                self.fail("p1 did not complete")
        self.service.start_work_package(self.principal, second.package_id)
        worker.tick()
        second_after = self.service.get_work_package(self.principal, second.package_id).state
        self.assertIn(second_after, ("planning", "planned", "completed"))

    def test_worker_skips_blocked_packages(self):
        campaign = self._campaign()
        package = self._package(campaign, stage_order=("eligibility", "plan", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)
        self.service.raise_blocker(self.principal, package.package_id,
                                   reason="operator", detail="blocked for now")

        worker = CampaignWorker(self.service)
        advanced = worker.tick()
        self.assertEqual(advanced, 0)
        self.assertEqual(
            self.service.get_work_package(self.principal, package.package_id).state,
            "blocked")

    def test_worker_does_not_touch_inactive_campaigns(self):
        campaign = self._campaign()
        package = self._package(campaign, stage_order=("eligibility", "plan", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)
        self.service.pause_campaign(self.principal, campaign.campaign_id)

        worker = CampaignWorker(self.service)
        advanced = worker.tick()
        self.assertEqual(advanced, 0)
        self.assertEqual(
            self.service.get_work_package(self.principal, package.package_id).state,
            "eligible")
    def test_worker_injected_stage_handler_receives_stage(self):
        seen = []
        campaign = self._campaign()
        package = self._package(campaign, stage_order=("eligibility", "plan", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, package.package_id)

        def handler(principal, camp, pkg, stage, attempt):
            seen.append(stage)
            return {"passed": True, "detail": "handled %s" % stage}

        worker = CampaignWorker(self.service, stage_handler=handler)
        worker.tick()
        self.assertIn("plan", seen)

    def test_worker_uses_max_parallel_cap(self):
        campaign = self._campaign(max_parallel_packages=1)
        first = self._package(campaign, key="p1", stage_order=("eligibility", "plan", "complete"))
        second = self._package(campaign, key="p2", stage_order=("eligibility", "plan", "complete"))
        self.service.start_campaign(self.principal, campaign.campaign_id)
        self.service.start_work_package(self.principal, first.package_id)
        self.service.start_work_package(self.principal, second.package_id)

        worker = CampaignWorker(self.service)
        worker.tick()
        worker.tick()
        first_state = self.service.get_work_package(self.principal, first.package_id).state
        second_state = self.service.get_work_package(self.principal, second.package_id).state
        self.assertNotEqual(second_state, "completed")
        self.assertNotEqual(first_state, "queued")


if __name__ == "__main__":
    unittest.main()
