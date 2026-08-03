"""Tests for the JoeOS Security Platform.

Covers deny-by-default policy, policy precedence, approval exact binding and
strength, separation of duties, secret broker (create/retrieve/rotate/revoke/
scan/destination policy), audit integrity, lockdown, emergency stop,
quarantine, circuit breakers, identity impersonation, scope resolution
(traversal/symlink), data classification, privacy engine, and threat models.
"""

import tempfile
import unittest
from pathlib import Path

from server.security import SecurityService, SecurityError
from server.security.models import (
    IdentityRecord,
    PolicyRequestContext,
    ScopeGrant,
    SecurityPolicy,
)


class SecurityFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = SecurityService(str(self.root / "security"), master_key=bytes(range(32)))
        self.service.prepare_defaults()

    def tearDown(self):
        self.tempdir.cleanup()


class PolicyEngineTests(SecurityFixture):
    def test_deny_by_default(self):
        decision = self.service.evaluate(
            PolicyRequestContext(subject="agent.alpha", subject_type="agent", action="shell_execute")
        )
        self.assertEqual(decision.effect, "deny")

    def test_explicit_deny(self):
        decision = self.service.evaluate(
            PolicyRequestContext(subject="user", subject_type="human_user", action="git_force_push")
        )
        self.assertEqual(decision.effect, "deny")

    def test_allow_after_policy(self):
        self.service.upsert_policy(
            SecurityPolicy(
                policy_id="policy.allow_note_read",
                title="Allow note read",
                scope="all",
                action="read_note",
                effect="allow",
                priority=60,
                authority="joeos_security",
            )
        )
        decision = self.service.evaluate(
            PolicyRequestContext(subject="user", subject_type="human_user", action="read_note")
        )
        self.assertEqual(decision.effect, "allow")

    def test_deny_wins_at_equal_priority(self):
        self.service.upsert_policy(
            SecurityPolicy(
                policy_id="policy.allow_test",
                title="Allow test action",
                scope="all",
                action="test_action",
                effect="allow",
                priority=50,
                authority="joeos_security",
            )
        )
        self.service.upsert_policy(
            SecurityPolicy(
                policy_id="policy.deny_test",
                title="Deny test action",
                scope="all",
                action="test_action",
                effect="deny",
                priority=50,
                authority="joeos_security",
            )
        )
        decision = self.service.evaluate(
            PolicyRequestContext(subject="user", subject_type="human_user", action="test_action")
        )
        self.assertEqual(decision.effect, "deny")

    def test_require_approval_policy(self):
        self.service.upsert_policy(
            SecurityPolicy(
                policy_id="policy.approve_export",
                title="Require approval for export",
                scope="all",
                action="export_secret",
                effect="require_approval",
                priority=80,
                authority="joeos_security",
            )
        )
        decision = self.service.evaluate(
            PolicyRequestContext(subject="user", subject_type="human_user", action="export_secret")
        )
        self.assertIsNotNone(decision.required_approval)

    def test_seeded_policies_present(self):
        policies = {p.policy_id for p in self.service.list_policies()}
        self.assertIn("policy.deny_arbitrary_shell", policies)
        self.assertIn("policy.deny_public_listener", policies)


class IdentityTests(SecurityFixture):
    def test_identity_registration_and_revocation(self):
        self.service.register_identity(
            IdentityRecord(identity_id="identity.user", identity_type="human_user", display_label="You")
        )
        self.service.revoke_identity("identity.user")
        self.assertEqual(self.service.list_identities()[0].status, "revoked")

    def test_no_cross_type_impersonation(self):
        self.assertFalse(self.service.can_impersonate("agent", "human_user"))
        self.assertFalse(self.service.can_impersonate("plugin", "human_user"))
        self.assertFalse(self.service.can_impersonate("workflow", "human_user"))
        self.assertFalse(self.service.can_impersonate("device", "human_user"))
        self.assertTrue(self.service.can_impersonate("human_user", "human_user"))


class ScopeTests(SecurityFixture):
    def test_path_traversal_rejected(self):
        allowed, _ = self.service.resolve_path(scope_root="/projects/demo", candidate="/projects/demo/../../etc/passwd")
        self.assertFalse(allowed)

    def test_within_scope_allowed(self):
        allowed, canonical = self.service.resolve_path(scope_root="/projects/demo", candidate="/projects/demo/src/main.py")
        self.assertTrue(allowed)
        self.assertEqual(canonical, "/projects/demo/src/main.py")

    def test_outside_scope_rejected(self):
        allowed, _ = self.service.resolve_path(scope_root="/projects/demo", candidate="/other/secret.txt")
        self.assertFalse(allowed)

    def test_nul_byte_rejected(self):
        allowed, _ = self.service.resolve_path(scope_root="/projects/demo", candidate="/projects/demo/x\x00y")
        self.assertFalse(allowed)

    def test_scope_grant_and_check(self):
        self.service.grant_scope(
            ScopeGrant(
                grant_id="grant_1", subject="agent.alpha", capability="read_project_file",
                action="read", scope="project", project="demo",
            )
        )
        self.assertTrue(self.service.scope_granted(subject="agent.alpha", capability="read_project_file", project="demo"))
        self.assertFalse(self.service.scope_granted(subject="agent.alpha", capability="read_project_file", project="other"))

    def test_scope_revocation(self):
        self.service.grant_scope(
            ScopeGrant(
                grant_id="grant_1", subject="agent.alpha", capability="read_project_file",
                action="read", scope="project", project="demo",
            )
        )
        self.service.revoke_scope("grant_1")
        self.assertFalse(self.service.scope_granted(subject="agent.alpha", capability="read_project_file", project="demo"))


class ApprovalTests(SecurityFixture):
    def test_exact_binding(self):
        approval = self.service.request_approval(
            requester_identity="agent.alpha", action_id="external_send",
            target_id="msg-1", arguments={"to": "bob@x.com", "body": "hi"}, risk="medium",
        )
        # external_send is high-risk by policy, so it requires level4.
        self.assertEqual(approval.strength_required, "level4")

    def test_high_risk_requires_strong_strength(self):
        approval = self.service.request_approval(
            requester_identity="agent.alpha", action_id="external_send",
            target_id="msg-1", arguments={"to": "bob@x.com", "body": "hi"}, risk="high",
        )
        self.assertEqual(approval.strength_required, "level4")
        with self.assertRaises(SecurityError):
            self.service.approve(approval_id=approval.approval_id, approver_identity="user", confirmation_strength="level1")
        approved = self.service.approve(
            approval_id=approval.approval_id, approver_identity="user", confirmation_strength="level4"
        )
        self.assertEqual(approved.state, "approved")

    def test_changed_arguments_invalidate(self):
        approval = self.service.request_approval(
            requester_identity="agent.alpha", action_id="send_internal_note",
            arguments={"to": "bob@x.com"}, risk="low",
        )
        self.service.approve(approval_id=approval.approval_id, approver_identity="user", confirmation_strength="level1")
        valid = self.service.verify_approval_exact(
            approval_id=approval.approval_id, action_id="send_internal_note", arguments={"to": "alice@x.com"}
        )
        self.assertFalse(valid)

    def test_unchanged_arguments_remain_valid(self):
        approval = self.service.request_approval(
            requester_identity="agent.alpha", action_id="send_internal_note",
            arguments={"to": "bob@x.com"}, risk="low",
        )
        self.service.approve(approval_id=approval.approval_id, approver_identity="user", confirmation_strength="level1")
        valid = self.service.verify_approval_exact(
            approval_id=approval.approval_id, action_id="send_internal_note", arguments={"to": "bob@x.com"}
        )
        self.assertTrue(valid)

    def test_separation_of_duties(self):
        approval = self.service.request_approval(
            requester_identity="agent.beta", action_id="deployment",
            arguments={"env": "prod"}, risk="high",
        )
        with self.assertRaises(SecurityError):
            self.service.approve(approval_id=approval.approval_id, approver_identity="agent.beta", confirmation_strength="level4")

    def test_expired_approval_rejected(self):
        from datetime import datetime, timedelta, timezone
        approval = self.service.request_approval(
            requester_identity="agent.alpha", action_id="external_send",
            arguments={}, risk="low", ttl_hours=1,
        )
        # Force the approval to be expired in storage.
        with self.service._connection_factory() as connection:
            connection.execute(
                "UPDATE security_approvals SET expiration = ? WHERE approval_id = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), approval.approval_id),
            )
        with self.assertRaises(SecurityError):
            self.service.approve(approval_id=approval.approval_id, approver_identity="user", confirmation_strength="level1")

    def test_consent_lifecycle(self):
        consent = self.service.record_consent(identity="user", purpose="cloud_transcription", destination="speech.example")
        self.assertTrue(self.service.consent_active(identity="user", purpose="cloud_transcription", destination="speech.example"))
        self.service.withdraw_consent(consent.consent_id)
        self.assertFalse(self.service.consent_active(identity="user", purpose="cloud_transcription"))


class SecretBrokerTests(SecurityFixture):
    def test_create_and_retrieve(self):
        secret = self.service.create_secret(
            label="Provider token", secret_type="api_key",
            value="sk-live-abcdefghijklmnopqrstuvwxyz12",
            allowed_destinations=("api.provider.example",),
        )
        value = self.service.retrieve_secret(
            secret_id=secret.secret_id, subject="user", purpose="provider-call",
            destination="api.provider.example",
        )
        self.assertEqual(value, "sk-live-abcdefghijklmnopqrstuvwxyz12")

    def test_destination_policy_enforced(self):
        secret = self.service.create_secret(
            label="Token", secret_type="api_key", value="secret-value",
            allowed_destinations=("api.provider.example",),
        )
        with self.assertRaises(SecurityError):
            self.service.retrieve_secret(
                secret_id=secret.secret_id, subject="user", purpose="x", destination="evil.example"
            )

    def test_revocation_blocks_use(self):
        secret = self.service.create_secret(label="Token", secret_type="api_key", value="secret-value")
        self.service.revoke_secret(secret_id=secret.secret_id)
        with self.assertRaises(SecurityError):
            self.service.retrieve_secret(secret_id=secret.secret_id, subject="user", purpose="x")

    def test_rotation(self):
        secret = self.service.create_secret(label="Token", secret_type="api_key", value="old-value")
        self.service.rotate_secret(secret_id=secret.secret_id, new_value="new-value")
        value = self.service.retrieve_secret(secret_id=secret.secret_id, subject="user", purpose="x")
        self.assertEqual(value, "new-value")

    def test_metadata_never_contains_value(self):
        secret = self.service.create_secret(label="Token", secret_type="api_key", value="sk-live-topsecret")
        metadata = self.service.secret_metadata(secret.secret_id)
        self.assertNotIn("sk-live-topsecret", metadata.model_dump_json())

    def test_secret_scanning_masked(self):
        detections = self.service.scan_secret_text(
            text="key: ghp_abcdefghijklmnopqrstuvwxyz123", source="commit"
        )
        self.assertGreaterEqual(len(detections), 1)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123", detections[0].masked_fingerprint)


class AuditTests(SecurityFixture):
    def test_integrity_valid(self):
        self.service.audit_record(actor="user", action="permission_grant", result="allowed")
        self.service.audit_record(actor="agent.alpha", action="secret_access", result="denied", risk="high")
        valid, count = self.service.audit_verify_integrity()
        self.assertTrue(valid)
        self.assertEqual(count, 2)

    def test_tamper_detected(self):
        self.service.audit_record(actor="user", action="permission_grant", result="allowed")
        self.service.audit_record(actor="agent.alpha", action="secret_access", result="denied", risk="high")
        # Tamper with the audit row directly.
        with self.service._connection_factory() as connection:
            connection.execute(
                "UPDATE security_audit SET result = 'allowed' WHERE action = 'secret_access'"
            )
        valid, _ = self.service.audit_verify_integrity()
        self.assertFalse(valid)

    def test_audit_has_no_secrets(self):
        self.service.create_secret(label="T", secret_type="api_key", value="sk-live-abc")
        self.service.audit_record(actor="user", action="secret_access", result="allowed")
        for event in self.service.audit_list():
            self.assertNotIn("sk-live-abc", event.model_dump_json())


class GovernanceTests(SecurityFixture):
    def test_lockdown_requires_authentication_to_exit(self):
        self.service.activate_lockdown(reason="test")
        self.assertTrue(self.service.lockdown_active())
        with self.assertRaises(SecurityError):
            self.service.deactivate_lockdown(reauthenticated=False)
        self.service.deactivate_lockdown(reauthenticated=True)
        self.assertFalse(self.service.lockdown_active())

    def test_emergency_stop(self):
        result = self.service.emergency_stop()
        self.assertTrue(result["stopped"])
        self.assertFalse(result["automatic_restart"])

    def test_quarantine(self):
        result = self.service.quarantine(kind="plugin", subject="plugin.bad", reason="integrity failure")
        self.assertTrue(result["quarantined"])


class CircuitBreakerTests(SecurityFixture):
    def test_opens_after_threshold(self):
        for _ in range(6):
            self.service.breaker_failure(target="provider.ollama", error="timeout")
        self.assertTrue(self.service.breaker_is_open(target="provider.ollama"))

    def test_resets_on_success(self):
        for _ in range(6):
            self.service.breaker_failure(target="provider.ollama", error="timeout")
        self.service.breaker_success(target="provider.ollama")
        self.assertFalse(self.service.breaker_is_open(target="provider.ollama"))


class ClassificationAndPrivacyTests(SecurityFixture):
    def test_env_file_classified_credential(self):
        cls = self.service.classify_data(path="/repo/.env", content_hint="TOKEN=abc")
        self.assertEqual(cls, "credential")

    def test_model_cannot_lower_classification(self):
        cls = self.service.classify_data(path="notes.md", proposed_by_model="public", user_label="confidential")
        self.assertEqual(cls, "confidential")

    def test_unknown_defaults_conservatively(self):
        cls = self.service.classify_data(path="mystery.bin", source="external_provider")
        self.assertEqual(cls, "unknown")

    def test_privacy_blocks_cloud_for_restricted(self):
        decision, _ = self.service.privacy_evaluate(data_class="restricted", destination="cloud", consent_active=False)
        self.assertIn(decision, {"require_explicit_consent", "block_cloud_ai"})

    def test_privacy_blocks_secret_from_external(self):
        decision, _ = self.service.privacy_evaluate(data_class="credential", destination="cloud")
        self.assertEqual(decision, "block_external_provider")

    def test_notification_preview_hidden_for_restricted(self):
        self.assertFalse(self.service.privacy_notification_preview(data_class="restricted", device="mobile"))


class ThreatModelTests(SecurityFixture):
    def test_seeded_threat_models(self):
        models = self.service.threat_models_list()
        self.assertGreaterEqual(len(models), 3)
        ids = {m.threat_model_id for m in models}
        self.assertIn("threat.plugin_host", ids)
        self.assertIn("threat.ai_runtime", ids)


class OverviewTests(SecurityFixture):
    def test_overview_real_state(self):
        self.service.create_secret(label="Old", secret_type="api_key", value="v")
        overview = self.service.overview()
        self.assertTrue(overview.audit_integrity_verified)
        self.assertFalse(overview.lockdown_active)


if __name__ == "__main__":
    unittest.main()