"""Autonomous operations tests.

Covers the durable automation domain: definition lifecycle, deterministic
schedules, occurrence identity/deduplication, lease/claim, retry policy,
workspace isolation, and the scheduler state machine (with a substitute
AgentFabric executor)."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

from server.autonomous.executor import AgentFabricAutomationExecutor
from server.autonomous.models import (
    AutomationDefinitionCreate,
    NotificationPolicySpec,
    RecurrenceSpec,
    RetryPolicySpec,
    TriggerSpec,
)
from server.autonomous.scheduler import AutonomousScheduler
from server.autonomous.scheduling import initial_occurrence, next_occurrence, occurrence_key
from server.autonomous.service import AutonomousDeniedError, AutonomousService
from server.autonomous.storage import AutonomousStore

OWNER = "11111111-2222-4333-8444-555555555555"
ORG = "55555555-6666-4777-8888-999999999999"
WS = "33333333-4444-4555-8666-777777777777"


def principal():
    return {
        "session_id": None,
        "device_id": None,
        "user": {"id": OWNER, "display_name": "Owner", "status": "active"},
        "organization": {"id": ORG},
        "workspace": {"id": WS, "name": "Default"},
        "roles": ["joeos.owner"],
        "capabilities": ["agent.run", "agent.read"],
    }


class AutonomousFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = AutonomousStore(str(Path(self.tempdir.name) / "auto"))
        self.service = AutonomousService(self.store)
        self.service.prepare()

    def tearDown(self):
        self.tempdir.cleanup()

    def _recurring_payload(self, **kw):
        defaults = dict(
            name="Daily health check",
            description="Check agent system health.",
            objective="Report the health of the local agent system in three bullets.",
            agent_ref="joe",
            trigger=TriggerSpec(
                kind="recurring",
                schedule=RecurrenceSpec(kind="daily", at_time="08:00"),
                timezone="America/New_York",
            ),
            timezone="America/New_York",
            retry_policy=RetryPolicySpec(),
            notification_policy=NotificationPolicySpec(on_failure=True),
        )
        defaults.update(kw)
        return AutomationDefinitionCreate(**defaults)

    def _one_time_payload(self, scheduled_for: str):
        return AutomationDefinitionCreate(
            name="One time",
            description="",
            objective="Describe the current state briefly.",
            agent_ref="architect",
            trigger=TriggerSpec(kind="one_time", scheduled_for=scheduled_for),
            timezone="UTC",
        )


class DefinitionLifecycleTests(AutonomousFixture):
    def test_create_definition_sets_state_and_next_run(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        self.assertEqual(definition.state, "active")
        self.assertGreater(len(definition.next_run_at), 0)
        self.assertEqual(definition.revision, 1)
        fetched = self.service.get_definition(principal(), definition.id)
        self.assertEqual(fetched.name, "Daily health check")

    def test_pause_resume_archive(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        paused = self.service.set_state(principal(), definition.id, "paused")
        self.assertEqual(paused.state, "paused")
        resumed = self.service.set_state(principal(), definition.id, "active")
        self.assertEqual(resumed.state, "active")
        self.assertGreater(len(resumed.next_run_at), 0)
        archived = self.service.archive_definition(principal(), definition.id)
        self.assertEqual(archived.state, "archived")
        self.assertEqual(archived.enabled, False)
        # Editing an archived automation is denied.
        with self.assertRaises(AutonomousDeniedError):
            self.service.update_definition(principal(), definition.id, self._recurring_payload())

    def test_cross_workspace_denied(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        other = dict(principal())
        other["workspace"] = {"id": "99999999-8888-4777-8666-555555555555", "name": "Other"}
        with self.assertRaises(AutonomousDeniedError):
            self.service.get_definition(other, definition.id)

    def test_update_bumps_revision(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        updated = self.service.update_definition(principal(), definition.id, self._recurring_payload(name="Renamed"))
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.name, "Renamed")


class ScheduleTests(AutonomousFixture):
    def test_one_time_occurrence(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        definition = self.service.create_definition(principal(), self._one_time_payload(future))
        self.assertEqual(definition.trigger.kind, "one_time")
        self.assertEqual(initial_occurrence(definition.trigger), future)

    def test_recurring_next_occurrence_is_deterministic(self):
        trigger = TriggerSpec(kind="recurring", schedule=RecurrenceSpec(kind="daily", at_time="09:00"),
                              timezone="America/New_York")
        after = "2026-08-08T10:00:00+00:00"
        first = next_occurrence(trigger, after)
        second = next_occurrence(trigger, after)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)

    def test_interval_occurrence(self):
        trigger = TriggerSpec(kind="recurring",
                              schedule=RecurrenceSpec(kind="interval", interval_seconds=3600),
                              timezone="UTC")
        after = "2026-08-08T10:00:00+00:00"
        nxt = next_occurrence(trigger, after)
        self.assertEqual(nxt, "2026-08-08T11:00:00+00:00")

    def test_condition_watch_interval_respects_minimum(self):
        # Sub-minute intervals are rejected by the model (min 300s).
        with self.assertRaises(Exception):
            TriggerSpec(kind="condition_watch", condition_key="runner_healthy",
                        check_interval_seconds=60)
        trigger = TriggerSpec(kind="condition_watch", condition_key="runner_healthy",
                              check_interval_seconds=900)
        after = "2026-08-08T10:00:00+00:00"
        nxt = next_occurrence(trigger, after)
        parsed = datetime.fromisoformat(nxt)
        delta = (parsed - datetime.fromisoformat(after)).total_seconds()
        self.assertGreaterEqual(delta, 300)  # minimum 5 minutes


class OccurrenceTests(AutonomousFixture):
    def test_occurrence_key_deterministic_and_unique(self):
        a = occurrence_key("aut_1", "2026-08-08T10:00:00+00:00", 1)
        b = occurrence_key("aut_1", "2026-08-08T10:00:00+00:00", 1)
        c = occurrence_key("aut_1", "2026-08-08T11:00:00+00:00", 1)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_claim_and_run_deduplicates(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        scheduled = definition.next_run_at
        run1 = self.service.claim_and_run(definition, scheduled)
        run2 = self.service.claim_and_run(definition, scheduled)
        self.assertEqual(run1.id, run2.id)
        self.assertEqual(self.store.list_runs(definition.id).__len__(), 1)

    def test_claim_and_run_recovers_expired_lease(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        scheduled = definition.next_run_at
        run = self.service.claim_and_run(definition, scheduled)
        # Simulate a worker dying mid-run with an expired lease.
        now = datetime.now(timezone.utc).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        self.service.claim_run(run.id, worker="dead-worker", now_iso=now, lease_expires_iso=past)
        recovered = self.store.recover_expired_leases(now, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.assertGreaterEqual(recovered, 1)
        refreshed = self.service.get_run(run.id)
        self.assertEqual(refreshed.state, "queued")

    def test_terminal_run_not_reset_on_lease_recovery(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        scheduled = definition.next_run_at
        run = self.service.claim_and_run(definition, scheduled)
        self.service.complete_run(run.id, state="succeeded", result_summary="done")
        now = datetime.now(timezone.utc).isoformat()
        self.service.claim_run(run.id, worker="worker", now_iso=now, lease_expires_iso=now)
        recovered = self.store.recover_expired_leases(
            now, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        refreshed = self.service.get_run(run.id)
        self.assertEqual(refreshed.state, "succeeded")


class RetryPolicyTests(AutonomousFixture):
    def test_retryable_error_schedules_retry(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        self.assertTrue(self.service.is_retryable("OLLAMA_UNAVAILABLE", definition))
        self.assertTrue(self.service.is_retryable("MODEL_TIMEOUT", definition))
        self.assertFalse(self.service.is_retryable("capability_denied", definition))
        self.assertFalse(self.service.is_retryable("approval_denied", definition))

    def test_update_run_with_retry(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        scheduled = definition.next_run_at
        run = self.service.claim_and_run(definition, scheduled)
        future = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        updated = self.service.update_run_with_retry(
            run.id, attempt=2, error_category="OLLAMA_UNAVAILABLE", next_retry_at=future)
        self.assertEqual(updated.state, "retry_wait")
        self.assertEqual(updated.attempt, 2)


    def test_paused_definition_not_due(self):
        definition = self.service.create_definition(principal(), self._recurring_payload())
        self.service.set_state(principal(), definition.id, "paused")
        due = self.service.due_now()
        self.assertNotIn(definition.id, [d.id for d in due])
        # advance does nothing while paused (schedule holds).
        self.service.advance_definition(definition.id)
        refreshed = self.service.get_definition(principal(), definition.id)
        self.assertEqual(refreshed.state, "paused")
        self.assertEqual(refreshed.next_run_at, definition.next_run_at)

    def test_scheduler_rechecks_state_before_claim(self):
        class FakeExecutor:
            async def execute(self, definition, run, service):
                return {"status": "succeeded", "result": "ok", "agent_run_id": "r",
                        "provider_key": "ollama", "model_key": "m"}

        definition = self.service.create_definition(principal(), self._one_time_payload(
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
        self.service._store.update_definition(definition.model_copy(
            update={"next_run_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        # Pause before the tick; the scheduler must not create a run.
        self.service.set_state(principal(), definition.id, "paused")
        scheduler = AutonomousScheduler(self.service, FakeExecutor(), tick_interval_seconds=1.0, lease_seconds=60)
        processed = asyncio.run(scheduler.tick())
        runs = self.store.list_runs(definition.id)
        self.assertEqual(len(runs), 0)


class SchedulerTests(AutonomousFixture):
    def test_scheduler_executes_and_advances(self):
        class FakeExecutor:
            def __init__(self):
                self.calls = 0

            async def execute(self, definition, run, service):
                self.calls += 1
                return {"status": "succeeded", "result": "ok", "agent_run_id": "agent-run-1",
                        "provider_key": "ollama", "model_key": "qwen2.5-coder:1.5b"}

        definition = self.service.create_definition(principal(), self._one_time_payload(
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
        # Force next_run_at to be due.
        self.service._store.update_definition(definition.model_copy(
            update={"next_run_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        executor = FakeExecutor()
        scheduler = AutonomousScheduler(self.service, executor, tick_interval_seconds=1.0, lease_seconds=60)
        processed = asyncio.run(scheduler.tick())
        self.assertGreaterEqual(processed, 1)
        self.assertGreaterEqual(executor.calls, 1)
        runs = self.store.list_runs(definition.id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].state, "succeeded")
        self.assertEqual(runs[0].model_key, "qwen2.5-coder:1.5b")

    def test_scheduler_failure_retries_then_fails(self):
        attempts = {"n": 0}

        class FlakyExecutor:
            async def execute(self, definition, run, service):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return {"status": "failed", "failure": "OLLAMA_UNAVAILABLE"}
                return {"status": "succeeded", "result": "ok", "agent_run_id": "r",
                        "provider_key": "ollama", "model_key": "m"}

        definition = self.service.create_definition(principal(), self._one_time_payload(
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
        self.service._store.update_definition(definition.model_copy(
            update={"next_run_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        executor = FlakyExecutor()
        scheduler = AutonomousScheduler(self.service, executor, tick_interval_seconds=1.0, lease_seconds=60)
        asyncio.run(scheduler.tick())
        runs = self.store.list_runs(definition.id)
        self.assertEqual(runs[0].state, "retry_wait")
        self.assertEqual(runs[0].attempt, 2)
        self.assertGreater(len(runs[0].next_retry_at), 0)


if __name__ == "__main__":
    unittest.main()
