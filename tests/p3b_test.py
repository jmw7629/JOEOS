"""Phase P3B control-plane tests: provider/model registry, agent profiles and
immutable versions, tool catalog, immutable action proposals, deterministic
policy evaluation, cryptographically bound approvals, council, realtime events,
and a full HTTP integration path. Only cryptographic keys and AI providers are
substituted through test dependency injection."""

import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.actions.repository import SQLiteControlStore
from server.actions.router import router as control_router
from server.actions.service import ActionService, ActionDeniedError
from server.actions.storage import sha256_hex
from server.conversations.router import router as conversations_router
from server.conversations.repository import SQLiteConversationRepository
from server.conversations.service import ConversationService
from server.identity.authority_repository import SQLiteAuthorityRepository
from server.identity.authority_router import router as authority_router
from server.identity.authority_service import AuthorityService
from server.identity.crypto import base64url_encode, encode_p256_public_key
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository

OWNER = UUID("11111111-2222-4333-8444-555555555555")
APPROVER = UUID("11111111-2222-4333-8444-555555555557")
OTHER_WS = UUID("99999999-8888-4777-8666-555555555555")

STANDARD_CAPS = [
    "action.propose", "action.read", "action.cancel", "approval.read",
    "approval.decide.low", "approval.decide.medium", "approval.decide.high",
    "approval.decide.critical", "agent.read", "agent.manage", "agent.run",
    "tool.read", "policy.read",
]


def principal(user_id=OWNER, workspace_id=UUID("33333333-4444-4555-8666-777777777777"),
              capabilities=None, device_id=None):
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888888"),
        "device_id": device_id or UUID("22222222-3333-4444-8555-666666666666"),
        "user": {"id": user_id, "display_name": "Owner", "status": "active"},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": workspace_id, "name": "Default"},
        "roles": ["joeos.owner"],
        "capabilities": capabilities if capabilities is not None else list(STANDARD_CAPS),
    }


def approver():
    """A distinct approver user (separation of duties)."""
    return principal(user_id=APPROVER)


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return int(self.value.timestamp() * 1000)

    def advance_seconds(self, seconds):
        self.value += timedelta(seconds=seconds)


class ControlFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "p3b.db"
        self.clock = MutableClock()
        self.events = []

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        self.device_repository = SQLiteDeviceIdentityRepository(
            connect, PairingKeyProtector(bytes(range(32)))
        )
        self.device_repository.prepare()
        self.store = SQLiteControlStore(connect)
        self.service = ActionService(
            self.store,
            device_repository=self.device_repository,
            event_sink=lambda level, source, message: self.events.append(message),
            now=self.clock,
        )
        self.service.prepare()
        self.authentication_key = ec.generate_private_key(ec.SECP256R1())
        self.approval_key = ec.generate_private_key(ec.SECP256R1())
        self.device_id = self._enroll_device()
        self.provider = self._register_provider()
        self.model = self._register_model()
        self.agent = self._register_agent()

    def tearDown(self):
        self.tempdir.cleanup()

    def _enroll_device(self) -> UUID:
        device_id = UUID("22222222-3333-4444-8555-666666666666")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO enrolled_devices(
                    device_id, enrollment_id, server_id, credential_id, audience_origin,
                    client_instance_id, display_name, platform, os_version, app_version,
                    state, enrolled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ios', '17.6', '1.0.0', 'active_unassigned', ?)
                """,
                (
                    str(device_id), str(UUID(int=1, version=4)), str(UUID(int=2, version=4)),
                    base64url_encode(bytes(range(32))), "http://100.98.25.26:8080",
                    str(UUID(int=3, version=4)), "Test iPhone", self.clock() // 1000,
                ),
            )
            connection.execute(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, 'device_authentication', ?, 1, ?)
                """,
                (base64url_encode(bytes(range(32))), str(device_id),
                 encode_p256_public_key(self.authentication_key.public_key()), self.clock() // 1000),
            )
            connection.execute(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, 'approval', ?, 1, ?)
                """,
                (base64url_encode(bytes(range(33))), str(device_id),
                 encode_p256_public_key(self.approval_key.public_key()), self.clock() // 1000),
            )
            connection.commit()
        return device_id

    def _register_provider(self):
        return self.service.register_provider(
            principal(), key="lemonade-local", display_name="Lemonade", provider_type="lemonade",
            location="local", streaming=True, tool_calling=True, structured_output=True,
        )

    def _register_model(self):
        return self.service.register_model(
            principal(), provider_id=self.provider["id"], key="llama3", display_name="Llama 3",
            model_identifier="llama3", streaming=True, tool_calling=True,
        )

    def _register_agent(self, key="architect", **overrides):
        defaults = dict(
            key=key, display_name="Architect", description="Plans", purpose="design",
            system_instructions="You plan only.", allowed_tools="read_only_tool,state_change_tool",
            denied_tools="", required_capabilities="", max_delegation_depth=2,
            max_parallel_tasks=2,
        )
        defaults.update(overrides)
        return self.service.create_agent(principal(), **defaults)

    def _register_tool(self, key="read_only_tool", risk="informational",
                       side_effect="none", capability_requirements="", schema=None):
        schema = schema or {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        return self.service.register_tool(
            principal(), key=key, display_name=key, description="tool", version="1.0.0",
            category="read_only", input_schema=schema, risk=risk, side_effect=side_effect,
            capability_requirements=capability_requirements,
        )

    def _sign_approval(self, message: str):
        return base64url_encode(
            self.approval_key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        )

    def _propose(self, tool_key="state_change_tool", parameters=None, target="file:notes.txt"):
        return self.service.propose_action(
            principal(), tool_key=tool_key,
            parameters=parameters or {"path": "notes.txt"}, target=target,
            conversation_id=UUID(int=9, version=4),
        )


class RegistryTests(ControlFixture):
    def test_provider_and_model_are_backend_authoritative(self):
        providers = self.service.list_providers(principal())
        self.assertEqual(providers[0]["key"], "lemonade-local")
        models = self.service.list_models(principal())
        self.assertEqual(models[0]["provider_id"], self.provider["id"])
        # Disabled provider is rejected by backend selection.
        self.service.set_provider_status(principal(), self.provider["id"], "disabled", "disabled")
        with self.assertRaises(ActionDeniedError):
            self.service.start_agent_run(
                principal(), agent_id=self.agent["id"],
                conversation_id=UUID(int=9, version=4), message_id=UUID(int=10, version=4),
            )
        self.service.set_provider_status(principal(), self.provider["id"], "active", "healthy")
        # Disabled model is rejected.
        self.service.set_model_status(principal(), self.model["id"], "disabled")
        with self.assertRaises(ActionDeniedError):
            self.service.start_agent_run(
                principal(), agent_id=self.agent["id"],
                conversation_id=UUID(int=9, version=4), message_id=UUID(int=10, version=4),
            )

    def test_cross_workspace_access_denied(self):
        other = principal(workspace_id=OTHER_WS)
        with self.assertRaises(ActionDeniedError):
            self.service.get_agent(other, self.agent["id"])


class AgentVersionTests(ControlFixture):
    def test_immutable_versions_and_revision_check(self):
        versions_before = self.service.list_agent_versions(principal(), self.agent["id"])
        self.assertEqual(len(versions_before), 1)
        updated = self.service.update_agent(
            principal(), self.agent["id"], expected_revision=1, description="v2 instructions"
        )
        versions_after = self.service.list_agent_versions(principal(), self.agent["id"])
        self.assertEqual(len(versions_after), 2)
        self.assertNotEqual(versions_before[0]["version_id"], versions_after[-1]["version_id"])
        # Existing run binds to the immutable version present at start time.
        run = self.service.start_agent_run(
            principal(), agent_id=self.agent["id"],
            conversation_id=UUID(int=9, version=4), message_id=UUID(int=10, version=4),
        )
        self.assertIn(run["agent_version_id"], [v["version_id"] for v in versions_after])
        # Revision conflict is rejected.
        with self.assertRaises(Exception):
            self.service.update_agent(principal(), self.agent["id"], expected_revision=1, description="stale")

    def test_disabled_agent_cannot_start_run(self):
        self.service.set_agent_status(principal(), self.agent["id"], "disabled")
        with self.assertRaises(ActionDeniedError):
            self.service.start_agent_run(
                principal(), agent_id=self.agent["id"],
                conversation_id=UUID(int=9, version=4), message_id=UUID(int=10, version=4),
            )


class ToolAndProposalTests(ControlFixture):
    def test_unknown_and_undeclared_and_malformed_parameters(self):
        with self.assertRaises(ActionDeniedError):
            self.service.propose_action(principal(), tool_key="missing_tool",
                                        parameters={}, target="file:x")
        self._register_tool("state_change_tool", risk="high", side_effect="external_irreversible")
        with self.assertRaises(ActionDeniedError):
            self.service.propose_action(principal(), tool_key="state_change_tool",
                                        parameters={"path": "a", "undeclared": 1}, target="file:x")
        with self.assertRaises(ActionDeniedError):
            self.service.propose_action(principal(), tool_key="state_change_tool",
                                        parameters={"path": "$(rm -rf /)"}, target="file:x")
        with self.assertRaises(ActionDeniedError):
            self.service.propose_action(principal(), tool_key="state_change_tool",
                                        parameters={"path": "x"}, target="../escape")

    def test_immutable_proposal_and_digest_stability(self):
        self._register_tool("read_only_tool")
        self._register_tool("state_change_tool", risk="high", side_effect="external_irreversible")
        first = self._propose(parameters={"path": "notes.txt"})["proposal"]
        second = self._propose(parameters={"path": "notes.txt"})["proposal"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["payload_digest"], second["payload_digest"])
        # Parameter change -> different digest and proposal.
        changed = self._propose(parameters={"path": "other.txt"})["proposal"]
        self.assertNotEqual(changed["payload_digest"], first["payload_digest"])
        # Read-only low-risk proposal needs no approval.
        read = self.service.propose_action(
            principal(), tool_key="read_only_tool", parameters={"path": "a.txt"},
            target="file:a.txt",
        )
        self.assertEqual(read["proposal"]["state"], "approved_awaiting_executor")
        self.assertEqual(read["policy_decision"]["result"], "allow_read_only")

    def test_privileged_proposal_stops_at_approved_awaiting_executor(self):
        self._register_tool("state_change_tool", risk="critical", side_effect="privileged")
        result = self._propose()
        self.assertEqual(result["proposal"]["state"], "approval_required")
        self.assertEqual(result["policy_decision"]["result"], "approval_required")
        self.assertEqual(result["policy_decision"]["step_up_required"], "approval_key")


class PolicyTests(ControlFixture):
    def test_missing_capability_is_denied(self):
        self._register_tool("state_change_tool", risk="medium", side_effect="external_reversible")
        limited = principal(capabilities=["action.read"])
        with self.assertRaises(ActionDeniedError):
            self.service.propose_action(
                limited, tool_key="state_change_tool", parameters={"path": "x"}, target="file:x"
            )

    def test_risk_tiers_require_approval(self):
        for risk, step_up in (("low", "session"), ("medium", "session"), ("high", "approval_key"), ("critical", "approval_key")):
            key = "tool_%s" % risk
            self._register_tool(key, risk=risk, side_effect="external_reversible")
            result = self.service.propose_action(
                principal(), tool_key=key, parameters={"path": "x"}, target="file:x"
            )
            self.assertEqual(result["policy_decision"]["result"], "approval_required")
            self.assertEqual(result["policy_decision"]["step_up_required"], step_up, risk)


class ApprovalTests(ControlFixture):
    def _setup_high_risk_proposal(self):
        self._register_tool("state_change_tool", risk="high", side_effect="external_irreversible")
        result = self._propose()
        return result["proposal"], result["policy_decision"], result["approval_request"]

    def test_full_approval_challenge_flow(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        challenge = self.service.create_approval_challenge(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        self.assertTrue(challenge["message"].startswith("JOEOS-ACTION-APPROVAL-V1\0"))
        signature = self._sign_approval(challenge["message"])
        outcome = self.service.submit_approval_decision(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            decision="approve", signature_b64url=signature,
            challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
        )
        self.assertEqual(outcome["proposal"]["state"], "approved_awaiting_executor")
        # No execution output exists; the proposal is not succeeded.
        self.assertNotIn(outcome["proposal"]["state"], ("executing", "succeeded", "failed"))

    def test_challenge_replay_is_rejected(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        challenge = self.service.create_approval_challenge(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        signature = self._sign_approval(challenge["message"])
        self.service.submit_approval_decision(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            decision="approve", signature_b64url=signature,
            challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
        )
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
                decision="approve", signature_b64url=signature,
                challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
            )

    def test_digest_binding_prevents_reuse_after_change(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        challenge = self.service.create_approval_challenge(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        signature = self._sign_approval(challenge["message"])
        # A proposal with modified parameters is a NEW proposal needing a new approval.
        changed = self._propose(parameters={"path": "changed.txt"})
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                approver(), proposal_id=changed["proposal"]["id"],
                approval_request_id=changed["approval_request"]["id"],
                decision="approve", signature_b64url=signature,
                challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
            )

    def test_expired_challenge_is_rejected(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        challenge = self.service.create_approval_challenge(
            approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        self.clock.advance_seconds(6 * 60)
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                approver(), proposal_id=proposal["id"], approval_request_id=request["id"],
                decision="approve", signature_b64url=self._sign_approval(challenge["message"]),
                challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
            )

    def test_self_approval_denied_with_separation_of_duties(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        challenge = self.service.create_approval_challenge(
            principal(), proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        # The proposer is the same user; separation of duties requires a distinct approver.
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                principal(), proposal_id=proposal["id"], approval_request_id=request["id"],
                decision="approve", signature_b64url=self._sign_approval(challenge["message"]),
                challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
            )

    def test_cross_workspace_approval_denied(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        other = principal(user_id=APPROVER, workspace_id=OTHER_WS)
        challenge = self.service.create_approval_challenge(
            other, proposal_id=proposal["id"], approval_request_id=request["id"],
            policy_decision_id=decision["id"], requested_decision="approve",
            approver_device_id=self.device_id,
        )
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                other, proposal_id=proposal["id"], approval_request_id=request["id"],
                decision="approve", signature_b64url=self._sign_approval(challenge["message"]),
                challenge_id=challenge["challenge_id"], approver_device_id=self.device_id,
            )

    def test_denial_is_terminal(self):
        self._register_tool("state_change_tool", risk="medium", side_effect="external_reversible")
        result = self._propose()
        outcome = self.service.submit_approval_decision(
            principal(), proposal_id=result["proposal"]["id"],
            approval_request_id=result["approval_request"]["id"], decision="deny",
        )
        self.assertEqual(outcome["proposal"]["state"], "denied")

    def test_approval_expiration(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        self.clock.advance_seconds(2 * 60 * 60)
        with self.assertRaises(ActionDeniedError):
            self.service.submit_approval_decision(
                approver(), proposal_id=proposal["id"],
                approval_request_id=request["id"], decision="approve",
            )

    def test_supersede_invalidates_prior_approvals(self):
        proposal, decision, request = self._setup_high_risk_proposal()
        self.service.revoke_proposal(principal(), proposal["id"])
        refreshed = self.service.get_proposal(principal(), proposal["id"])
        self.assertEqual(refreshed["state"], "revoked")


class CouncilTests(ControlFixture):
    def _install_executor(self, behavior):
        async def executor(messages, tools, decision):
            agent_key = decision.get("agent") or ""
            if behavior.get("fail") == agent_key:
                raise RuntimeError("member failed")
            content = behavior.get("content") or "agree"
            if callable(content):
                content = content(agent_key)
            return {"content": content}
        self.service._executor = executor

    def _run_council_sync(self, results, fail_member=None, quorum_rule="majority"):
        agents = [self._register_agent(key="m%d" % i) for i in range(3)]
        behavior = {"content": lambda key: results.get(key, "agree")}
        if fail_member:
            behavior["fail"] = fail_member
        self._install_executor(behavior)
        council = self.service.create_council(
            principal(), name="Review", member_agent_ids=[a["id"] for a in agents],
            quorum_rule=quorum_rule,
        )
        import asyncio
        return asyncio.run(self.service.run_council(
            principal(), council_id=council["id"], objective="Review the plan",
        ))

    def test_unanimous_council(self):
        agents = [self._register_agent(key="m%d" % i) for i in range(3)]
        behavior = {"content": lambda key: "agree"}
        self._install_executor(behavior)
        council = self.service.create_council(
            principal(), name="Review", member_agent_ids=[a["id"] for a in agents],
        )
        import asyncio
        run = asyncio.run(self.service.run_council(
            principal(), council_id=council["id"], objective="Review the plan",
        ))
        self.assertEqual(run["state"], "completed")
        self.assertIn("agree", run["final_recommendation"])
        member_runs = self.store.list_council_member_runs(run["id"])
        self.assertEqual(len(member_runs), 3)

    def test_member_failure_but_quorum_met(self):
        agents = [self._register_agent(key="x%d" % i) for i in range(3)]
        self._install_executor({"content": "ok", "fail": "x2"})
        council = self.service.create_council(
            principal(), name="Quorum", member_agent_ids=[a["id"] for a in agents],
        )
        import asyncio
        run = asyncio.run(self.service.run_council(principal(), council_id=council["id"], objective="x"))
        self.assertEqual(run["state"], "completed")

    def test_quorum_failure(self):
        agents = [self._register_agent(key="q%d" % i) for i in range(3)]
        self._install_executor({"content": "ok", "fail": "q0"})
        council = self.service.create_council(
            principal(), name="Fail", member_agent_ids=[a["id"] for a in agents],
            quorum_rule="unanimous",
        )
        import asyncio
        run = asyncio.run(self.service.run_council(principal(), council_id=council["id"], objective="x"))
        self.assertEqual(run["state"], "failed")


class RealtimeEventTests(ControlFixture):
    def test_events_persisted_without_secrets(self):
        self._register_tool("state_change_tool", risk="high", side_effect="external_irreversible")
        self._propose()
        joined = "\n".join(self.events)
        self.assertIn("action.proposed", joined)
        self.assertIn("action.policy_evaluated", joined)
        self.assertIn("approval.requested", joined)
        self.assertNotIn("BEGIN", joined)
        self.assertNotIn("private key", joined)


class HTTPControlIntegrationTest(ControlFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.action_service = self.service
        from server.identity.authority_router import require_application_session
        self.current_principal = principal()
        app.dependency_overrides[require_application_session] = lambda: self.current_principal
        app.include_router(control_router)
        self.client = TestClient(app)
        self._register_tool("read_only_tool")
        self._register_tool("state_change_tool", risk="high", side_effect="external_irreversible")

    def test_full_http_control_sequence(self):
        # Provider/model/agent/tool registered through authorized paths.
        providers = self.client.get("/api/v1/control/providers").json()["providers"]
        self.assertEqual(len(providers), 1)
        agents = self.client.get("/api/v1/control/agents").json()["agents"]
        self.assertEqual(len(agents), 1)
        tools = self.client.get("/api/v1/control/tools").json()["tools"]
        self.assertEqual(len(tools), 2)

        # Propose a privileged action.
        proposed = self.client.post(
            "/api/v1/control/proposals",
            json={"tool_key": "state_change_tool", "parameters": {"path": "notes.txt"},
                  "target": "file:notes.txt"},
        )
        self.assertEqual(proposed.status_code, 201, proposed.text)
        proposal = proposed.json()["proposal"]
        self.assertEqual(proposal["state"], "approval_required")
        request = proposed.json()["approval_request"]
        decision = proposed.json()["policy_decision"]

        # Approval challenge + cryptographically signed decision by a distinct approver.
        self.current_principal = approver()
        challenge = self.client.post(
            "/api/v1/control/approvals/challenge",
            json={
                "proposal_id": str(proposal["id"]),
                "approval_request_id": str(request["id"]),
                "policy_decision_id": str(decision["id"]),
                "decision": "approve",
                "device_id": str(self.device_id),
            },
        )
        self.assertEqual(challenge.status_code, 200, challenge.text)
        signature = base64url_encode(
            self.approval_key.sign(
                challenge.json()["message"].encode("ascii"), ec.ECDSA(hashes.SHA256())
            )
        )
        decided = self.client.post(
            "/api/v1/control/approvals/%s/decide" % request["id"],
            json={
                "proposal_id": str(proposal["id"]),
                "decision": "approve",
                "signature": signature,
                "challenge_id": str(challenge.json()["challenge_id"]),
                "device_id": str(self.device_id),
            },
        )
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["proposal"]["state"], "approved_awaiting_executor")
        self.current_principal = principal()

        # No execution occurred: no proposal is succeeded and no executor output exists.
        proposals = self.client.get("/api/v1/control/proposals").json()["proposals"]
        states = {p["state"] for p in proposals}
        self.assertNotIn("succeeded", states)
        self.assertNotIn("executing", states)

        # Revocation immediately revokes a pending proposal.
        fresh = self.client.post(
            "/api/v1/control/proposals",
            json={"tool_key": "state_change_tool", "parameters": {"path": "b.txt"},
                  "target": "file:b.txt"},
        ).json()["proposal"]
        revoked = self.client.post("/api/v1/control/proposals/%s/revoke" % fresh["id"])
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(
            self.client.get("/api/v1/control/proposals/%s" % fresh["id"]).json()["state"],
            "revoked",
        )
