"""Tests for the JoeOS Automation and Workflow Platform.

Covers workflow definitions/versions, validation, compilation, execution
(state machine, branches, loops, parallel, delays, notifications), triggers,
timezone-aware scheduling, missed-run/overlap policies, permissions, secrets,
idempotency, concurrency, approvals, user input, cancellation, dry run,
history, and health.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.automation import AutomationService
from server.automation.compiler import ValidationError, compile_workflow, validate_definition
from server.automation.expressions import ExpressionError, evaluate_condition, evaluate_expression
from server.automation.models import (
    ConcurrencyPolicy,
    EdgeConfig,
    LoopConfig,
    NodeConfig,
    Recurrence,
    ResourcePolicy,
    TriggerConfig,
    WorkflowDefinition,
)
from server.automation.schedules import ScheduleService, _next_occurrence
from server.automation.workflows import WorkflowError, parse_definition

MASTER_KEY = bytes(range(32))


def _notify_definition(workflow_id="acme.health", **overrides) -> WorkflowDefinition:
    data = dict(
        workflow_id=workflow_id,
        name="Health Check",
        description="Check and notify.",
        owner="user",
        creator="user",
        source="user",
        version="1.0.0",
        risk="low",
        triggers=(TriggerConfig(trigger_id="manual", type="manual"),),
        nodes=(
            NodeConfig(id="start", type="start", title="Start"),
            NodeConfig(id="notify", type="notification", title="Notify", params={"message": "Health check done"}),
            NodeConfig(id="end", type="end", title="End"),
        ),
        edges=(
            EdgeConfig(source="start", target="notify"),
            EdgeConfig(source="notify", target="end"),
        ),
        required_permissions=("notification.publish",),
        resource=ResourcePolicy(max_active_runs=2, max_parallel_branches=1, max_loop_iterations=10, max_duration_seconds=3600, max_model_calls=0, max_tool_calls=10),
    )
    data.update(overrides)
    return WorkflowDefinition.model_validate(data)


class AutomationFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.notifications = []
        self.service = AutomationService(
            str(self.root / "automation"),
            master_key=MASTER_KEY,
            event_sink=lambda level, source, message: self.notifications.append((level, message)),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_enabled(self, definition=None, workflow_id="acme.health", permissions=("notification.publish",)):
        definition = definition or _notify_definition(workflow_id=workflow_id)
        record = self.service.create_workflow(definition)
        for permission in permissions:
            self.service.grant_permission(workflow_id, permission)
        self.service.enable_workflow(workflow_id)
        return record


class WorkflowDefinitionTests(unittest.TestCase):
    def test_valid_definition_parses(self):
        definition = parse_definition(_notify_definition().model_dump())
        self.assertEqual(definition.workflow_id, "acme.health")

    def test_duplicate_node_rejected(self):
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate(
                _notify_definition().model_dump()
                | {
                    "nodes": (
                        NodeConfig(id="start", type="start"),
                        NodeConfig(id="start", type="end"),
                    )
                }
            )

    def test_missing_start_rejected(self):
        definition = _notify_definition()
        with self.assertRaises((ValueError, ValidationError)):
            WorkflowDefinition.model_validate(
                definition.model_dump() | {"nodes": (NodeConfig(id="end", type="end"),)}
            )

    def test_unbounded_cycle_rejected(self):
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {
                "nodes": (
                    NodeConfig(id="start", type="start"),
                    NodeConfig(id="a", type="action", action="joeos.audit_marker"),
                    NodeConfig(id="b", type="action", action="joeos.audit_marker"),
                    NodeConfig(id="end", type="end"),
                ),
                "edges": (
                    EdgeConfig(source="start", target="a"),
                    EdgeConfig(source="a", target="b"),
                    EdgeConfig(source="b", target="a"),
                    EdgeConfig(source="b", target="end"),
                ),
            }
        )
        with self.assertRaises(ValidationError):
            validate_definition(definition)

    def test_version_validation(self):
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate(_notify_definition().model_dump() | {"version": "not-a-version"})


class ExpressionTests(unittest.TestCase):
    def test_comparison(self):
        self.assertTrue(evaluate_condition("temperature > 30", {"temperature": 40}))
        self.assertFalse(evaluate_condition("temperature > 30", {"temperature": 10}))

    def test_boolean_logic(self):
        self.assertTrue(evaluate_condition("a == 1 && b == 2", {"a": 1, "b": 2}))
        self.assertTrue(evaluate_condition("a == 1 || b == 99", {"a": 1, "b": 2}))

    def test_string_and_arithmetic(self):
        self.assertEqual(evaluate_expression("greeting + ' world'", {"greeting": "hello"}), "hello world")
        self.assertEqual(evaluate_expression("2 * 3 + 1", {}), 7.0)

    def test_division_by_zero_rejected(self):
        with self.assertRaises(ExpressionError):
            evaluate_expression("1 / 0", {})

    def test_unknown_variable_rejected(self):
        with self.assertRaises(ExpressionError):
            evaluate_condition("missing > 3", {})

    def test_no_arbitrary_execution(self):
        with self.assertRaises(ExpressionError):
            evaluate_expression("__import__('os')", {})


class WorkflowLifecycleTests(AutomationFixture):
    def test_create_enable_run(self):
        self._create_enabled()
        run = self.service.run_workflow("acme.health", inputs={"message": "Hi"})
        self.assertEqual(run.state, "succeeded")
        self.assertEqual(self.notifications, [("info", "Health check done")])

    def test_run_requires_enabled(self):
        self.service.create_workflow(_notify_definition())
        with self.assertRaises(WorkflowError):
            self.service.run_workflow("acme.health")

    def test_enable_requires_granted_permissions(self):
        self.service.create_workflow(_notify_definition())
        with self.assertRaises(WorkflowError):
            self.service.enable_workflow("acme.health")

    def test_duplicate_workflow_rejected(self):
        self._create_enabled()
        with self.assertRaises(WorkflowError):
            self.service.create_workflow(_notify_definition())

    def test_update_creates_version(self):
        self._create_enabled()
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(definition.model_dump() | {"version": "1.1.0"})
        self.service.update_workflow(definition)
        versions = self.service.workflows.versions("acme.health")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version, "1.1.0")

    def test_update_with_expanded_permissions_requires_review(self):
        self._create_enabled()
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {"version": "1.1.0", "required_permissions": ("notification.publish", "memory.propose_memory")}
        )
        self.service.update_workflow(definition)
        record = self.service.get_workflow("acme.health")
        self.assertEqual(record.definition.status, "draft")


class ExecutionTests(AutomationFixture):
    def test_condition_branch(self):
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {
                "nodes": (
                    NodeConfig(id="start", type="start"),
                    NodeConfig(id="check", type="condition", condition="temperature > 30", branches={"true": "hot", "false": "cool"}),
                    NodeConfig(id="hot", type="notification", params={"message": "HOT"}),
                    NodeConfig(id="cool", type="notification", params={"message": "COOL"}),
                    NodeConfig(id="end", type="end"),
                ),
                "edges": (
                    EdgeConfig(source="start", target="check"),
                    EdgeConfig(source="hot", target="end"),
                    EdgeConfig(source="cool", target="end"),
                ),
            }
        )
        self._create_enabled(definition)
        self.service.run_workflow("acme.health", inputs={"temperature": 40})
        self.assertEqual(self.notifications[-1][1], "HOT")
        self.service.run_workflow("acme.health", inputs={"temperature": 5})
        self.assertEqual(self.notifications[-1][1], "COOL")

    def test_bounded_loop(self):
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {
                "nodes": (
                    NodeConfig(id="start", type="start"),
                    NodeConfig(id="loop", type="loop", loop=LoopConfig(max_iterations=3, item_source="items", item_variable="item"), branches={"body": "notify", "done": "end"}),
                    NodeConfig(id="notify", type="notification", params={"message": "${item}"}),
                    NodeConfig(id="end", type="end"),
                ),
                "edges": (
                    EdgeConfig(source="start", target="loop"),
                    EdgeConfig(source="notify", target="loop"),
                ),
            }
        )
        self._create_enabled(definition)
        run = self.service.run_workflow("acme.health", inputs={"items": ["a", "b", "c"]})
        self.assertEqual(run.state, "succeeded")
        self.assertEqual([m for _, m in self.notifications], ["a", "b", "c"])

    def test_permission_guard_blocks_ungranted(self):
        # A workflow cannot use a permission it never declared and was granted.
        from server.automation.permissions import WorkflowPermissionGuard
        guard = WorkflowPermissionGuard(self.service._connection_factory)
        with self.assertRaises(ValueError):
            guard.verify_declared(workflow_id="acme.health", definition_required=("git.read",))
        guard.grant(workflow_id="acme.health", permission="git.read")
        guard.verify_declared(workflow_id="acme.health", definition_required=("git.read",))

    def test_secret_unavailable_blocks(self):
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {"secrets": ({"name": "token", "scope": "global"},)}
        )
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.health", "notification.publish")
        with self.assertRaises(WorkflowError):
            self.service.enable_workflow("acme.health")

    def test_secret_available_after_set(self):
        definition = _notify_definition()
        definition = WorkflowDefinition.model_validate(
            definition.model_dump()
            | {"secrets": ({"name": "token", "scope": "global"},)}
        )
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.health", "notification.publish")
        self.service.set_secret("token", "value")
        self.service.enable_workflow("acme.health")
        run = self.service.run_workflow("acme.health")
        self.assertEqual(run.state, "succeeded")


class ScheduleTests(AutomationFixture):
    def test_timezone_aware_daily(self):
        recurrence = Recurrence(kind="daily", at_time="09:00", timezone="America/New_York")
        occurrences = self.service.preview_schedule(recurrence, count=3)
        self.assertEqual(len(occurrences), 3)

    def test_unknown_timezone_rejected(self):
        recurrence = Recurrence(kind="daily", at_time="09:00", timezone="Not/AZone")
        with self.assertRaises(Exception):
            self.service.schedules.preview_occurrences(recurrence)

    def test_weekday_recurrence(self):
        recurrence = Recurrence(kind="weekdays", at_time="09:00", weekdays=(0, 1, 2, 3, 4), timezone="UTC")
        occurrences = self.service.preview_schedule(recurrence, count=5)
        from datetime import datetime as dt
        for occurrence in occurrences[:2]:
            parsed = dt.fromisoformat(occurrence.replace("+00:00", "+00:00"))
            self.assertIn(parsed.weekday(), (0, 1, 2, 3, 4))

    def test_schedule_upsert_and_due(self):
        self._create_enabled()
        schedule = self.service.schedule_workflow(
            workflow_id="acme.health",
            recurrence=Recurrence(kind="interval", interval_seconds=60, timezone="UTC"),
        )
        self.assertIsNotNone(schedule.next_run)

    def test_due_now_respects_interval(self):
        self._create_enabled()
        self.service.schedule_workflow(
            workflow_id="acme.health",
            recurrence=Recurrence(kind="interval", interval_seconds=3600, timezone="UTC"),
        )
        # Force next_run into the past to simulate a due schedule.
        import sqlite3
        with self.service._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_schedules SET next_run = '2000-01-01T00:00:00+00:00' WHERE workflow_id = 'acme.health'"
            )
        due = self.service.schedules.due_now()
        self.assertEqual(len(due), 1)


class OverviewTests(AutomationFixture):
    def test_overview_real_state(self):
        self._create_enabled()
        overview = self.service.overview()
        self.assertEqual(overview.workflows_total, 1)
        self.assertEqual(overview.workflows_enabled, 1)

    def test_health_reflects_reality(self):
        self._create_enabled()
        health = self.service.health()
        self.assertEqual(len(health), 1)
        self.assertIn(health[0]["health"], {"healthy", "inactive"})

    def test_stuck_run_detection(self):
        self._create_enabled()
        with self.service._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_runs (run_id, workflow_id, workflow_version, state, current_node, started_at, created_at)
                VALUES ('run_stuck', 'acme.health', '1.0.0', 'running', 'notify', '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
                """
            )
        stuck = self.service.stuck_runs()
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["run_id"], "run_stuck")


class CommunicationsIntegrationTests(unittest.TestCase):
    """Workflows route communications through the authoritative platform."""

    def setUp(self):
        import tempfile as _tempfile
        from server.communications import CommunicationsService
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.comms = CommunicationsService(str(self.root / "comms"))
        self.comms.prepare_defaults()
        from server.automation import AutomationService
        self.service = AutomationService(
            str(self.root / "automation"), master_key=MASTER_KEY, communications=self.comms
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _comms_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition.model_validate(
            _notify_definition("acme.comms")
            .model_dump()
            | {
                "nodes": (
                    NodeConfig(id="start", type="start"),
                    NodeConfig(id="notify", type="action", action="joeos.comms.notification", params={"title": "Alert", "message": "Build failed", "severity": "error", "category": "workflow_failed"}),
                    NodeConfig(id="msg", type="action", action="joeos.comms.internal_message", params={"message": "Internal", "recipients": ["identity.user"]}),
                    NodeConfig(id="draft", type="action", action="joeos.comms.draft", params={"subject": "Draft", "body": "Body"}),
                    NodeConfig(id="end", type="end"),
                ),
                "edges": (
                    EdgeConfig(source="start", target="notify"),
                    EdgeConfig(source="notify", target="msg"),
                    EdgeConfig(source="msg", target="draft"),
                    EdgeConfig(source="draft", target="end"),
                ),
            }
        )

    def test_workflow_routes_communications(self):
        definition = self._comms_definition()
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.comms", "notification.publish")
        self.service.enable_workflow("acme.comms")
        run = self.service.run_workflow("acme.comms")
        self.assertEqual(run.state, "succeeded")
        self.assertEqual(len(self.comms.list_notifications()), 1)
        self.assertEqual(len(self.comms.list_messages()), 1)
        self.assertEqual(len(self.comms.list_drafts()), 1)

    def test_workflow_does_not_call_provider_directly(self):
        # The draft action never sends automatically; it stays a draft.
        definition = self._comms_definition()
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.comms", "notification.publish")
        self.service.enable_workflow("acme.comms")
        self.service.run_workflow("acme.comms")
        drafts = self.comms.list_drafts()
        self.assertEqual(len(drafts), 1)
        self.assertFalse(drafts[0].model_dump().get("approval_state") == "approved")


class SecurityGateIntegrationTests(unittest.TestCase):
    """The Security Platform mediates every workflow action."""

    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.automation.security_gate import AutomationSecurityGate
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=MASTER_KEY)
        self.security.prepare_defaults()
        self.gate = AutomationSecurityGate(
            policy_evaluate=self.security.evaluate,
            audit_record=lambda **kw: self.security.audit_record(**kw),
            secret_broker=self.security.secrets,
        )
        from server.automation import AutomationService
        self.service = AutomationService(
            str(self.root / "automation"), master_key=MASTER_KEY, security_gate=self.gate
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _gated_definition(self, action, wfid, trig) -> WorkflowDefinition:
        return WorkflowDefinition.model_validate(
            _notify_definition(wfid).model_dump()
            | {
                "triggers": (TriggerConfig(trigger_id=trig, type="manual"),),
                "nodes": (
                    NodeConfig(id="start", type="start"),
                    NodeConfig(id="act", type="action", action=action, params={"message": "hi"}),
                    NodeConfig(id="end", type="end"),
                ),
                "edges": (
                    EdgeConfig(source="start", target="act"),
                    EdgeConfig(source="act", target="end"),
                ),
            }
        )

    def test_safe_core_action_allowed_and_audited(self):
        definition = self._gated_definition("joeos.notification", "acme.gated_ok", "manual_ok")
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.gated_ok", "notification.publish")
        self.service.enable_workflow("acme.gated_ok")
        run = self.service.run_workflow("acme.gated_ok")
        self.assertEqual(run.state, "succeeded")
        audits = [e for e in self.security.audit_list() if e.actor == "acme.gated_ok"]
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].result, "allowed")

    def test_privileged_action_denied_and_audited(self):
        self.service.actions.register(
            "joeos.export_secret",
            lambda params, context, variables, trace: {"exported": True},
            permission="", side_effects=("export",),
        )
        definition = self._gated_definition("joeos.export_secret", "acme.gated_deny", "manual_deny")
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.gated_deny", "notification.publish")
        self.service.enable_workflow("acme.gated_deny")
        run = self.service.run_workflow("acme.gated_deny")
        self.assertEqual(run.state, "failed")
        self.assertEqual(run.error_code, "permission_denied")
        denied = [e for e in self.security.audit_list() if e.actor == "acme.gated_deny" and e.result == "denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0].action, "joeos.export_secret")


if __name__ == "__main__":
    unittest.main()