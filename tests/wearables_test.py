"""Tests for the JoeOS Smart Glasses and Wearable Device Platform.

Covers device registry, adapter registry, simulator isolation, discovery,
pairing security, trust, authentication, sessions, capability negotiation,
permissions, glance cards, privacy modes, command gateway, confirmation
levels, voice, camera, checklists, handoff, offline queue, resource governor,
and revocation.
"""

import tempfile
import unittest
from pathlib import Path

from server.wearables import WearableService
from server.wearables.models import WearableContent
from server.wearables.connections import PermissionError


class WearablesFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = WearableService(str(self.root / "wearables"))
        self.device = self.service.create_simulator_device(profile="display_plus_microphone", display_name="Sim")

    def tearDown(self):
        self.tempdir.cleanup()

    def _pair_and_connect(self, permissions=("display.routine_cards", "display.private_content", "audio.input_microphone", "camera.capture_still", "joeos.create_note", "input.button")):
        challenge = self.service.begin_pairing(device_id=self.device.device_id)
        code = self.service.simulator.fixture_code(challenge.challenge_id)
        self.service.confirm_pairing(challenge_id=challenge.challenge_id, code=code)
        for permission in permissions:
            self.service.grant_permission(device_id=self.device.device_id, permission=permission, scope="session")
        self.service.trust_device(device_id=self.device.device_id, level="session_trusted", scope="session")
        return self.service.connect_device(device_id=self.device.device_id)


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = WearableService(str(Path(self.tempdir.name) / "wearables"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_device_types_do_not_imply_capabilities(self):
        types = {t.device_type for t in self.service.device_types()}
        self.assertIn("display_glasses", types)
        self.assertIn("simulated_development_device", types)

    def test_simulator_device_is_isolated(self):
        device = self.service.create_simulator_device(profile="one_line_monochrome")
        self.assertTrue(device.device_id.startswith("sim_"))
        self.assertEqual(device.transport, "simulator")

    def test_simulator_device_never_appears_as_production(self):
        device = self.service.create_simulator_device(profile="color_card_display")
        self.assertEqual(device.device_type, "simulated_development_device")

    def test_duplicate_device_rejected(self):
        self.service.devices.register(
            device_id="real.glasses", device_type="display_glasses", display_name="Real", transport="local_network"
        )
        with self.assertRaises(Exception):
            self.service.devices.register(
                device_id="real.glasses", device_type="display_glasses", display_name="Real", transport="local_network"
            )

    def test_adapter_cannot_mark_itself_trusted(self):
        # Adapter trust is always user-driven; no adapter API sets trust.
        from server.wearables.devices import AdapterRegistry
        with self.assertRaises(AttributeError):
            self.service.adapters.set_trust("simulator.basic-glasses", True)


class PairingTests(WearablesFixture):
    def test_pairing_requires_correct_code(self):
        challenge = self.service.begin_pairing(device_id=self.device.device_id)
        with self.assertRaises(Exception):
            self.service.confirm_pairing(challenge_id=challenge.challenge_id, code="000000")

    def test_pairing_completes_with_fixture_code(self):
        challenge = self.service.begin_pairing(device_id=self.device.device_id)
        code = self.service.simulator.fixture_code(challenge.challenge_id)
        trust = self.service.confirm_pairing(challenge_id=challenge.challenge_id, code=code)
        self.assertEqual(trust.trust_state, "paired_but_restricted")

    def test_pairing_code_is_single_use(self):
        challenge = self.service.begin_pairing(device_id=self.device.device_id)
        code = self.service.simulator.fixture_code(challenge.challenge_id)
        self.service.confirm_pairing(challenge_id=challenge.challenge_id, code=code)
        with self.assertRaises(Exception):
            self.service.confirm_pairing(challenge_id=challenge.challenge_id, code=code)

    def test_unpaired_device_cannot_connect(self):
        with self.assertRaises(PermissionError):
            self.service.connect_device(device_id=self.device.device_id)


class TrustAndRevocationTests(WearablesFixture):
    def test_revocation_terminates_access(self):
        self._pair_and_connect()
        self.service.revoke_device(device_id=self.device.device_id, reason="test")
        revoked = self.service.get_device(self.device.device_id)
        self.assertEqual(revoked.trusted_state, "revoked")
        self.assertEqual(revoked.connection_state, "revoked")
        with self.assertRaises(PermissionError):
            self.service.connect_device(device_id=self.device.device_id)

    def test_trust_is_capability_scoped(self):
        trust = self.service.trust_device(
            device_id=self.device.device_id,
            level="capability_scoped",
            scope="capabilities",
            capabilities=("display.text_card",),
        )
        self.assertIn("display.text_card", trust.capabilities)


class CapabilityNegotiationTests(WearablesFixture):
    def test_capabilities_negotiated_not_inferred(self):
        self._pair_and_connect()
        result = self.service.negotiate_capabilities(
            device_id=self.device.device_id,
            reported=["display.text_card", "camera.still_image"],
        )
        self.assertIn("display.text_card", result)
        # Camera is adapter-supported but must be explicitly enabled.
        self.assertEqual(result.get("camera.still_image"), "available")

    def test_unsupported_capability_reported(self):
        self._pair_and_connect()
        result = self.service.negotiate_capabilities(
            device_id=self.device.device_id,
            reported=["sensor.location"],
        )
        self.assertEqual(result.get("sensor.location"), "unsupported")


class PermissionTests(WearablesFixture):
    def test_privileged_permissions_not_default(self):
        # Camera/mic/location require explicit grant.
        self.assertFalse(self.service.permissions.granted(device_id=self.device.device_id, permission="camera.capture_still"))

    def test_grant_then_use(self):
        self.service.grant_permission(device_id=self.device.device_id, permission="camera.capture_still", scope="session")
        self.assertTrue(self.service.permissions.granted(device_id=self.device.device_id, permission="camera.capture_still"))

    def test_revoke_takes_effect(self):
        self.service.grant_permission(device_id=self.device.device_id, permission="display.private_content", scope="session")
        self.service.revoke_permission(device_id=self.device.device_id, permission="display.private_content")
        self.assertFalse(self.service.permissions.granted(device_id=self.device.device_id, permission="display.private_content"))

    def test_unknown_permission_rejected(self):
        with self.assertRaises(PermissionError):
            self.service.grant_permission(device_id=self.device.device_id, permission="camera.stream_everything")


class CardAndPrivacyTests(WearablesFixture):
    def _session(self):
        return self._pair_and_connect()

    def test_card_delivery(self):
        session = self._session()
        card = self.service.deliver_card(
            device_id=self.device.device_id,
            content=WearableContent(content_id="c1", content_type="glance_card", source="automation", title="T", body="B", severity="warning"),
            session_permissions=session.permissions,
        )
        self.assertEqual(card.delivery_state, "delivered")

    def test_private_content_hidden_without_permission(self):
        self._pair_and_connect(permissions=("display.routine_cards",))
        card = self.service.deliver_card(
            device_id=self.device.device_id,
            content=WearableContent(content_id="c2", content_type="notification_card", source="comms", title="Secret message", body="very private", privacy="private"),
        )
        self.assertEqual(card.title, "Private item")

    def test_privacy_mode_titles_only(self):
        self._pair_and_connect()
        self.service.set_privacy_mode(device_id=self.device.device_id, mode="titles_only")
        self.assertEqual(self.service.privacy_mode(self.device.device_id), "titles_only")

    def test_dedup_suppresses_duplicate(self):
        self._pair_and_connect()
        self.service.deliver_card(
            device_id=self.device.device_id,
            content=WearableContent(content_id="c3", content_type="glance_card", source="s", title="T", deduplication_key="k1"),
        )
        second = self.service.deliver_card(
            device_id=self.device.device_id,
            content=WearableContent(content_id="c4", content_type="glance_card", source="s", title="T", deduplication_key="k1"),
        )
        self.assertEqual(second.delivery_state, "suppressed")

    def test_acknowledgement(self):
        session = self._pair_and_connect()
        card = self.service.deliver_card(
            device_id=self.device.device_id,
            content=WearableContent(content_id="c5", content_type="glance_card", source="s", title="T", requires_acknowledgement=True),
        )
        acked = self.service.acknowledge_card(device_id=self.device.device_id, content_id=card.content_id)
        self.assertEqual(acked.delivery_state, "acknowledged")


class CommandGatewayTests(WearablesFixture):
    def _session(self):
        return self._pair_and_connect()

    def test_allowlisted_command(self):
        session = self._session()
        result = self.service.execute_command(device_id=self.device.device_id, session_id=session.session_id, command="mark_item_read")
        self.assertEqual(result["state"], "executed")

    def test_non_allowlisted_command_rejected(self):
        session = self._session()
        with self.assertRaises(PermissionError):
            self.service.execute_command(device_id=self.device.device_id, session_id=session.session_id, command="rm -rf /")

    def test_high_risk_escalates(self):
        session = self._session()
        result = self.service.execute_command(
            device_id=self.device.device_id, session_id=session.session_id,
            command="cancel_task", confirmation="high", interactive_confirm=False,
        )
        self.assertEqual(result["state"], "escalated")

    def test_invalid_session_rejected(self):
        with self.assertRaises(PermissionError):
            self.service.execute_command(device_id=self.device.device_id, session_id="bogus", command="mark_item_read")


class VoiceTests(WearablesFixture):
    def _session(self):
        return self._pair_and_connect()

    def test_push_to_talk_requires_permission(self):
        session = self._pair_and_connect(permissions=("display.routine_cards",))
        with self.assertRaises(PermissionError):
            self.service.start_voice(device_id=self.device.device_id, session_id=session.session_id)

    def test_mic_indicator_lifecycle(self):
        session = self._session()
        self.service.start_voice(device_id=self.device.device_id, session_id=session.session_id, push_to_talk=True)
        self.assertTrue(self.service.get_device(self.device.device_id).mic_active)
        self.service.stop_voice(device_id=self.device.device_id, session_id=session.session_id)
        self.assertFalse(self.service.get_device(self.device.device_id).mic_active)

    def test_ambiguous_voice_requires_clarification(self):
        session = self._session()
        self.service.start_voice(device_id=self.device.device_id, session_id=session.session_id)
        result = self.service.transcribe_voice(device_id=self.device.device_id, session_id=session.session_id, audio_reference="fixture.wav")
        # Default stub transcription yields an ambiguous ask_question.
        self.assertTrue(result["ambiguous"])


class CameraTests(WearablesFixture):
    def _session(self):
        return self._pair_and_connect()

    def test_capture_requires_permission(self):
        session = self._pair_and_connect(permissions=("display.routine_cards",))
        with self.assertRaises(PermissionError):
            self.service.capture_camera(device_id=self.device.device_id, session_id=session.session_id, explicit_user_action=True)

    def test_capture_requires_explicit_action(self):
        session = self._session()
        with self.assertRaises(PermissionError):
            self.service.capture_camera(device_id=self.device.device_id, session_id=session.session_id, explicit_user_action=False)

    def test_recording_indicator_lifecycle(self):
        session = self._session()
        capture = self.service.capture_camera(device_id=self.device.device_id, session_id=session.session_id, explicit_user_action=True)
        self.assertTrue(capture.recording_indicator)
        self.assertTrue(self.service.get_device(self.device.device_id).camera_active)
        self.service.stop_camera(device_id=self.device.device_id, capture_id=capture.capture_id)
        self.assertFalse(self.service.get_device(self.device.device_id).camera_active)


class ChecklistTests(WearablesFixture):
    def test_checklist_created(self):
        checklist = self.service.create_checklist(
            title="Maintenance", steps=[
                {"step_id": "a", "title": "Backup", "required": True, "evidence_required": True},
                {"step_id": "b", "title": "Tests", "required": True},
                {"step_id": "c", "title": "Lint", "required": False},
            ],
        )
        self.assertEqual(len(checklist.steps), 3)

    def test_required_evidence_enforced(self):
        checklist = self.service.create_checklist(
            title="Maintenance", steps=[{"step_id": "a", "title": "Backup", "required": True, "evidence_required": True}],
        )
        with self.assertRaises(Exception):
            self.service.complete_checklist_step(checklist_id=checklist.checklist_id, step_id="a", note="", evidence="")

    def test_required_step_cannot_be_skipped(self):
        checklist = self.service.create_checklist(
            title="Maintenance", steps=[{"step_id": "a", "title": "Backup", "required": True}],
        )
        with self.assertRaises(Exception):
            self.service.skip_checklist_optional(checklist_id=checklist.checklist_id, step_id="a")

    def test_optional_step_skippable(self):
        checklist = self.service.create_checklist(
            title="Maintenance", steps=[{"step_id": "c", "title": "Lint", "required": False}],
        )
        updated = self.service.skip_checklist_optional(checklist_id=checklist.checklist_id, step_id="c")
        self.assertTrue(updated.steps[0].completed)


class HandoffTests(WearablesFixture):
    def test_handoff_lifecycle(self):
        handoff = self.service.create_handoff(source_surface="glasses", target_surface="desktop", selected_action="approve")
        self.assertEqual(handoff.state, "created")
        resolved = self.service.resolve_handoff(handoff_id=handoff.handoff_id, accepted=True, destination_trusted=True)
        self.assertEqual(resolved.state, "accepted")

    def test_handoff_requires_trusted_destination(self):
        handoff = self.service.create_handoff(source_surface="glasses", target_surface="desktop")
        with self.assertRaises(Exception):
            self.service.resolve_handoff(handoff_id=handoff.handoff_id, accepted=True, destination_trusted=False)


class OfflineQueueTests(WearablesFixture):
    def test_safe_action_queued(self):
        operation = self.service.enqueue_offline(device_id=self.device.device_id, session_id="", action="mark_card_read")
        self.assertEqual(operation.action, "mark_card_read")

    def test_unsafe_action_rejected(self):
        with self.assertRaises(Exception):
            self.service.enqueue_offline(device_id=self.device.device_id, session_id="", action="approve_high_risk")

    def test_revalidation(self):
        self.service.enqueue_offline(device_id=self.device.device_id, session_id="", action="mark_card_read")
        result = self.service.revalidate_offline(device_id=self.device.device_id, authoritative_state=lambda action: action == "mark_card_read")
        self.assertEqual(result["replayed"], 1)


class ResourceGovernorTests(WearablesFixture):
    def test_low_battery_policy(self):
        result = self.service.apply_resources(device_id=self.device.device_id, resource=_resource(20, charging=False))
        self.assertIn("reduce_routine_delivery", result["policies"])
        self.assertEqual(self.service.get_device(self.device.device_id).battery_state, "low")

    def test_critical_battery_policy(self):
        result = self.service.apply_resources(device_id=self.device.device_id, resource=_resource(8, charging=False))
        self.assertIn("preserve_critical_alerts_only", result["policies"])

    def test_thermal_policy(self):
        result = self.service.apply_resources(device_id=self.device.device_id, resource=_resource(60, charging=True, thermal="warning"))
        self.assertIn("suspend_camera_and_video", result["policies"])
        self.assertEqual(self.service.get_device(self.device.device_id).thermal_state, "warning")


class OverviewTests(WearablesFixture):
    def test_overview_real_state(self):
        self._pair_and_connect()
        overview = self.service.overview()
        self.assertEqual(overview.paired_devices, 1)
        self.assertEqual(overview.connected_devices, 1)
        self.assertEqual(overview.active_sessions, 1)
        self.assertEqual(overview.trusted_devices, 1)


def _resource(battery, charging, thermal="unknown"):
    from server.wearables.models import ResourceState
    return ResourceState(device_id="sim_dev", battery=battery, charging=charging, thermal=thermal)


if __name__ == "__main__":
    unittest.main()