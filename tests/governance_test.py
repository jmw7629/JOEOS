"""Tests for Lockdown and Emergency Stop propagation into the platforms.

When the Security Platform's governance is active, new privileged work in the
automation, mobile, wearable, communications, and plugin platforms must be
denied, not just reflected in a dashboard.
"""

import tempfile
import unittest
from pathlib import Path

from server.security import SecurityService


class GovernancePropagationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.probe = self.security.governance_blocked

    def tearDown(self):
        self.tempdir.cleanup()

    def test_probe_allows_by_default(self):
        blocked, reason = self.probe()
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_lockdown_blocks(self):
        self.security.activate_lockdown(reason="test")
        blocked, reason = self.probe()
        self.assertTrue(blocked)
        self.assertIn("lockdown", reason)
        self.security.deactivate_lockdown(reauthenticated=True)
        blocked, _ = self.probe()
        self.assertFalse(blocked)

    def test_emergency_stop_blocks(self):
        self.security.emergency_stop()
        blocked, reason = self.probe()
        self.assertTrue(blocked)
        self.assertIn("emergency stop", reason)
        self.security.release_emergency_stop()
        blocked, _ = self.probe()
        self.assertFalse(blocked)


class AutomationGovernanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.automation import AutomationService
        from server.automation.models import (
            WorkflowDefinition, NodeConfig, EdgeConfig, TriggerConfig, ResourcePolicy,
        )
        from tests.automation_test import _notify_definition
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.service = AutomationService(
            str(self.root / "automation"), master_key=bytes(range(32)),
            governance_blocked=self.security.governance_blocked,
        )
        definition = _notify_definition("acme.gov")
        self.service.create_workflow(definition)
        self.service.grant_permission("acme.gov", "notification.publish")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_run_blocked_during_lockdown(self):
        self.service.enable_workflow("acme.gov")
        self.security.activate_lockdown(reason="test")
        from server.automation.workflows import WorkflowError
        with self.assertRaises(WorkflowError):
            self.service.run_workflow("acme.gov")
        self.security.deactivate_lockdown(reauthenticated=True)

    def test_enable_blocked_during_emergency_stop(self):
        self.security.emergency_stop()
        from server.automation.workflows import WorkflowError
        with self.assertRaises(WorkflowError):
            self.service.enable_workflow("acme.gov")
        self.security.release_emergency_stop()

    def test_run_allowed_after_release(self):
        self.service.enable_workflow("acme.gov")
        self.security.emergency_stop()
        try:
            self.service.run_workflow("acme.gov")
            self.fail("expected governance denial")
        except Exception:
            pass
        self.security.release_emergency_stop()
        run = self.service.run_workflow("acme.gov")
        self.assertEqual(run.state, "succeeded")


class MobileGovernanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.mobile import MobileService
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.service = MobileService(
            str(self.root / "mobile"), governance_blocked=self.security.governance_blocked
        )
        self.service.pairing._generate = lambda: "123456"
        self.host = self.service.prepare_defaults()
        self.client = self.service.register_client(client_id="client.iphone", platform="ios", installation_identity="i1")
        pair = self.service.begin_pairing(host_id=self.host.host_id)
        self.service.confirm_pairing_host(session_id=pair.session_id)
        self.service.confirm_pairing_client(session_id=pair.session_id, client_id=self.client.client_id, code="123456")
        self.service.grant_permission(client_id=self.client.client_id, permission="action.acknowledge_notification", scope="session")
        refresh = self.service.issue_refresh(client_id=self.client.client_id)
        self.session = self.service.authenticate(
            client_id=self.client.client_id, host_id=self.host.host_id, refresh_token=refresh
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_remote_command_blocked_during_lockdown(self):
        from server.mobile import MobileError
        self.security.activate_lockdown(reason="test")
        with self.assertRaises(MobileError):
            self.service.execute_command(
                client_id=self.client.client_id, session_id=self.session.session_id, command="acknowledge_notification"
            )
        self.security.deactivate_lockdown(reauthenticated=True)

    def test_remote_command_allowed_after_release(self):
        self.security.emergency_stop()
        try:
            self.service.execute_command(
                client_id=self.client.client_id, session_id=self.session.session_id, command="acknowledge_notification"
            )
            self.fail("expected governance denial")
        except Exception:
            pass
        self.security.release_emergency_stop()
        result = self.service.execute_command(
            client_id=self.client.client_id, session_id=self.session.session_id, command="acknowledge_notification"
        )
        self.assertEqual(result["command"], "acknowledge_notification")


class CommunicationsGovernanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.communications import CommunicationsService
        from server.communications.models import Origin, MessageRecord
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.service = CommunicationsService(
            str(self.root / "comms"), governance_blocked=self.security.governance_blocked
        )
        self.service.prepare_defaults()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_external_send_blocked_during_emergency_stop(self):
        from server.communications.service import ExternalSendError
        from server.communications.models import MessageRecord, Origin
        message = self.service.send_internal(
            communication_type="external_email", recipients=("identity.user",),
            subject="Hi", body="External", origin=Origin(origin_type="user", label="You"),
        )
        external = MessageRecord(**{**message.model_dump(), "external": True, "provider": "test.isolated", "account": "a"})
        self.security.emergency_stop()
        with self.assertRaises(ExternalSendError):
            self.service.enqueue_and_deliver(message=external, approval_id="approved")
        self.security.release_emergency_stop()


class WearableGovernanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.wearables import WearableService
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.service = WearableService(
            str(self.root / "wearables"), governance_blocked=self.security.governance_blocked
        )
        self.device = self.service.create_simulator_device(profile="color_card_display")
        challenge = self.service.begin_pairing(device_id=self.device.device_id)
        self.service.confirm_pairing(challenge_id=challenge.challenge_id, code="123456")
        for p in ("input.button", "joeos.view_tasks"):
            self.service.grant_permission(device_id=self.device.device_id, permission=p, scope="session")
        self.service.trust_device(device_id=self.device.device_id, level="session_trusted", scope="session")
        self.session = self.service.connect_device(device_id=self.device.device_id)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_wearable_command_blocked_during_lockdown(self):
        from server.wearables.connections import PermissionError
        self.security.activate_lockdown(reason="test")
        with self.assertRaises(PermissionError):
            self.service.execute_command(
                device_id=self.device.device_id, session_id=self.session.session_id, command="mark_item_read"
            )
        self.security.deactivate_lockdown(reauthenticated=True)


class EmergencyStopCancellationTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        from server.security import SecurityService
        from server.automation import AutomationService
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.automation = AutomationService(
            str(self.root / "automation"), master_key=bytes(range(32)),
            governance_blocked=self.security.governance_blocked,
        )
        self.security.governance.register_cancellation_handler(
            lambda: {"workflows": self.automation.cancel_active_runs_all(), "incomplete": []}
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_emergency_stop_cancels_active_runs(self):
        from server.automation.models import (
            WorkflowDefinition, NodeConfig, EdgeConfig, TriggerConfig, ResourcePolicy,
        )
        definition = WorkflowDefinition(
            workflow_id="acme.estop", name="EStop", description="d", owner="user", creator="user",
            source="user", version="1.0.0", risk="low",
            triggers=(TriggerConfig(trigger_id="manual_es", type="manual"),),
            nodes=(
                NodeConfig(id="start", type="start"),
                NodeConfig(id="wait", type="delay", params={"seconds": 30}),
                NodeConfig(id="end", type="end"),
            ),
            edges=(EdgeConfig(source="start", target="wait"), EdgeConfig(source="wait", target="end")),
            resource=ResourcePolicy(max_active_runs=2, max_parallel_branches=1, max_loop_iterations=10, max_duration_seconds=3600, max_model_calls=0, max_tool_calls=10),
        )
        self.automation.create_workflow(definition)
        self.automation.enable_workflow("acme.estop")
        with self.automation._connection_factory() as connection:
            connection.execute(
                "INSERT INTO workflow_runs (run_id, workflow_id, workflow_version, state, current_node, started_at, created_at) VALUES ('run_inflight', 'acme.estop', '1.0.0', 'running', 'wait', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
        result = self.security.emergency_stop()
        self.assertEqual(result["cancelled"]["workflows"], 1)
        self.assertEqual(result["incomplete"], [])
        cancelled = self.automation.list_runs(state="cancelled")
        self.assertEqual(len(cancelled), 1)
        self.security.release_emergency_stop()


class PluginGovernanceTests(unittest.TestCase):
    def setUp(self):
        import tempfile as _tempfile
        import json as _json
        from server.security import SecurityService
        from server.plugins import PluginService
        from tests.plugins_test import _write_plugin, _dev_manifest
        self.tempdir = _tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.security = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.security.prepare_defaults()
        self.service = PluginService(
            str(self.root / "plugins"), master_key=bytes(range(32)),
            first_party_publishers=["acme"], governance_blocked=self.security.governance_blocked,
        )
        self.plugin_dir = _write_plugin(self.root, manifest=_dev_manifest())
        self.record = self.service.install_directory(str(self.plugin_dir), source="local_development")
        for p in ("notification.publish", "storage.extension_data"):
            self.service.grant_permission(self.record.plugin_id, p)

    def tearDown(self):
        self.service.shutdown()
        self.tempdir.cleanup()

    def test_plugin_enable_blocked_during_lockdown(self):
        from server.plugins.lifecycle import PluginLifecycleError
        self.security.activate_lockdown(reason="test")
        with self.assertRaises(PluginLifecycleError):
            self.service.enable(self.record.plugin_id)
        self.security.deactivate_lockdown(reauthenticated=True)


if __name__ == "__main__":
    unittest.main()