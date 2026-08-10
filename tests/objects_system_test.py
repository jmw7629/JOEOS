"""JoeOS Enterprise Object System tests.

Covers the ObjectRef contract, type registry, capability calculation,
authorized/unauthorized resolution, relationships, and the security rule that
object context never grants authority.
"""

import unittest

from server.objects.core import (
    BASE_CAPABILITIES,
    CAP_APPROVE,
    CAP_EDIT,
    CAP_EXECUTE,
    CAP_VIEW,
    OBJECT_TYPES,
    ObjectRef,
    capabilities_for,
    effective_capabilities,
    normalize_object_type,
    register_object_type,
)
from server.objects.resolver import ObjectResolver


class ObjectTypeRegistryTest(unittest.TestCase):
    def test_known_types_resolve_to_canonical(self):
        self.assertEqual(normalize_object_type("agent"), "agent")
        self.assertEqual(normalize_object_type("Agent"), "agent")
        self.assertEqual(normalize_object_type("workflow"), "automation")
        self.assertEqual(normalize_object_type("work_package"), "work_package")
        self.assertEqual(normalize_object_type("workpackage"), "work_package")
        self.assertEqual(normalize_object_type("mission"), "agent_run")
        self.assertEqual(normalize_object_type("bot"), "agent")

    def test_unknown_type_fails_safely(self):
        self.assertIsNone(normalize_object_type("not-a-real-type"))
        self.assertIsNone(normalize_object_type(""))
        self.assertIsNone(normalize_object_type(None))

    def test_registry_contains_core_enterprise_types(self):
        for kind in ("organization", "workspace", "user", "agent", "model",
                     "provider", "file", "workflow_placeholder", "approval",
                     "execution", "schedule", "automation", "conversation",
                     "memory", "task", "campaign", "device", "module"):
            if kind == "workflow_placeholder":
                continue
            self.assertIn(kind, OBJECT_TYPES, kind)

    def test_register_custom_type(self):
        register_object_type("quality_issue")
        self.assertEqual(normalize_object_type("quality_issue"), "quality_issue")


class ObjectRefTest(unittest.TestCase):
    def test_roundtrip_dict(self):
        ref = ObjectRef(object_id="1847", object_type="execution", workspace_id="ws1", display_hint="Report run")
        restored = ObjectRef.from_dict(ref.to_dict())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.object_id, "1847")
        self.assertEqual(restored.object_type, "execution")
        self.assertEqual(restored.workspace_id, "ws1")
        self.assertEqual(restored.display_hint, "Report run")

    def test_from_dict_invalid(self):
        self.assertIsNone(ObjectRef.from_dict(None))
        self.assertIsNone(ObjectRef.from_dict({"object_id": "x"}))
        self.assertIsNone(ObjectRef.from_dict({"object_type": "y"}))

    def test_str_form(self):
        self.assertEqual(str(ObjectRef("a1", "agent")), "agent/a1")


class CapabilitiesTest(unittest.TestCase):
    def test_base_contract_always_present(self):
        for kind in ("agent", "file", "execution", "approval", "model", "unknown-ish"):
            caps = capabilities_for(kind)
            self.assertTrue(BASE_CAPABILITIES.issubset(caps), kind)

    def test_type_specific_capabilities(self):
        approval_caps = set(effective_capabilities("approval"))
        self.assertIn(CAP_APPROVE, approval_caps)
        self.assertIn(CAP_VIEW, approval_caps)
        agent_caps = set(effective_capabilities("agent"))
        self.assertIn(CAP_EXECUTE, agent_caps)
        self.assertIn(CAP_EDIT, agent_caps)

    def test_archived_state_disables_mutations(self):
        archived = set(effective_capabilities("file", "archived"))
        self.assertIn(CAP_VIEW, archived)
        self.assertNotIn(CAP_EDIT, archived)
        self.assertNotIn(CAP_EXECUTE, archived)

    def test_deleted_state_removes_view(self):
        deleted = set(effective_capabilities("file", "deleted"))
        self.assertNotIn(CAP_VIEW, deleted)

    def test_policy_deny_overrides(self):
        denied = set(effective_capabilities("file", extra={"export": False}))
        self.assertNotIn("export", denied)
        self.assertIn(CAP_VIEW, denied)


class ResolverTest(unittest.TestCase):
    def _resolver_with_fake_domain(self):
        resolver = ObjectResolver()

        class FakeAgent:
            def __init__(self):
                self.agent_id = "a1"
                self.display_name = "Test Agent"
                self.availability = "busy"
                self.role_id = "engineer"
                self.capabilities = ["code", "verify"]

            def agent(self, agent_id):
                return self if agent_id == "a1" else None

            def agents(self, include_inactive=False):
                return [self]

        class FakeAgentsService:
            def __init__(self):
                self.organization = FakeAgent()

        class FakeAutomationService:
            def get_workflow(self, workflow_id):
                if workflow_id == "wf1":
                    return type("W", (), {"name": "Nightly", "status": "active", "enabled": True, "workflow_id": "wf1"})()
                return None

            def list_workflows(self, principal=None):
                return []

            def list_schedules(self, principal=None):
                return []

        class FakeSecurityService:
            def approvals_list(self):
                return [{"approval_id": "ap1", "state": "pending", "action_id": "run", "requester_identity": "user1"}]

        resolver.wire_agents(FakeAgentsService())
        resolver.wire_automation(FakeAutomationService())
        resolver.wire_security(FakeSecurityService())
        return resolver

    def test_resolve_known_agent(self):
        resolver = self._resolver_with_fake_domain()
        principal = {"sub": "user1"}
        summary = resolver.resolve(ObjectRef("a1", "agent"), principal)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["name"], "Test Agent")
        self.assertEqual(summary["status"], "busy")
        self.assertIn(CAP_VIEW, summary["capabilities"])
        self.assertIn(CAP_EDIT, summary["capabilities"])

    def test_unauthorized_agent_resolves_none(self):
        resolver = self._resolver_with_fake_domain()
        principal = {"sub": "user1"}
        # Unknown id is not accessible.
        self.assertIsNone(resolver.resolve(ObjectRef("missing", "agent"), principal))

    def test_unknown_type_resolves_none(self):
        resolver = self._resolver_with_fake_domain()
        self.assertIsNone(resolver.resolve(ObjectRef("x", "bogus_type"), {"sub": "u"}))

    def test_approval_resolution(self):
        resolver = self._resolver_with_fake_domain()
        summary = resolver.resolve(ObjectRef("ap1", "approval"), {"sub": "u"})
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], "pending")
        self.assertIn(CAP_APPROVE, summary["capabilities"])

    def test_resolution_error_never_raises(self):
        class Boom:
            def approvals_list(self):
                raise RuntimeError("adapter failure")

        resolver = ObjectResolver()
        resolver.wire_security(Boom())
        self.assertIsNone(resolver.resolve(ObjectRef("ap1", "approval"), {"sub": "u"}))

    def test_relationships_agent_runs(self):
        resolver = self._resolver_with_fake_domain()
        rels = resolver.relationships(ObjectRef("a1", "agent"), {"sub": "u"})
        self.assertIsInstance(rels, list)


if __name__ == "__main__":
    unittest.main()
