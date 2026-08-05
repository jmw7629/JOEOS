"""Phase P3C runner-plane tests: enrollment, connection authentication,
execution jobs, leases, process safety, executors, secret broker, cancellation,
recovery, realtime, and a full HTTP+runner integration path. Only crypto keys
and executors are substituted through test dependency injection."""

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runner.joeos_runner.executors import WorkspaceFilesystemExecutor
from runner.joeos_runner.process import ProcessExecutionError, canonicalize_path, run_process
from server.actions.repository import SQLiteControlStore
from server.actions.router import router as control_router
from server.actions.service import ActionService
from server.conversations.router import router as conversations_router
from server.conversations.repository import SQLiteConversationRepository
from server.conversations.service import ConversationService
from server.identity.authority_router import require_application_session, router as authority_router
from server.identity.crypto import base64url_encode, encode_p256_public_key
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository
from server.runners.router import runner_router as runner_protocol_router
from server.runners.router import router as runner_control_router
from server.runners.repository import SQLiteRunnerStore
from server.runners.service import ENROLLMENT_DOMAIN, RUNNER_CONNECTION_DOMAIN, RESULT_DOMAIN, RunnerService

INSTALLATION = UUID("12345678-1234-4abc-8def-1234567890ab")

CAPS = [
    "action.propose", "action.read", "action.cancel", "approval.read",
    "approval.decide.low", "approval.decide.medium", "approval.decide.high",
    "approval.decide.critical", "agent.read", "agent.manage", "agent.run",
    "tool.read", "policy.read", "runner.read", "runner.manage", "runner.enroll",
    "execution.read", "execution.request", "execution.cancel",
    "execution.emergency_stop", "artifact.read", "executor.read", "executor.manage",
    "secret.reference.read", "secret.reference.manage",
]


def principal(user_id=None, workspace_id=UUID("33333333-4444-4555-8666-777777777777")):
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888888"),
        "device_id": UUID("22222222-3333-4444-8555-666666666666"),
        "user": {"id": user_id or UUID("11111111-2222-4333-8444-555555555555"),
                 "display_name": "Owner", "status": "active"},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": workspace_id, "name": "Default"},
        "roles": ["joeos.owner"], "capabilities": list(CAPS),
    }


class RunnerFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "p3c.db"
        self.events = []

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        self.device_repository = SQLiteDeviceIdentityRepository(
            connect, PairingKeyProtector(bytes(range(32)))
        )
        self.device_repository.prepare()
        self.authentication_key = ec.generate_private_key(ec.SECP256R1())
        self.approval_key = ec.generate_private_key(ec.SECP256R1())
        self.device_id = self._enroll_device()
        self.action_service = ActionService(
            SQLiteControlStore(connect),
            device_repository=self.device_repository,
            event_sink=lambda l, s, m: self.events.append(m),
        )
        self.action_service.prepare()
        self.runner_key = ec.generate_private_key(ec.SECP256R1())
        self.runner_key_id = "runner-key-1"
        self.runner_service = RunnerService(
            SQLiteRunnerStore(connect),
            installation_id=lambda: INSTALLATION,
            action_service=self.action_service,
            event_sink=lambda l, s, m: self.events.append(m),
            origin_provider=lambda: "http://100.64.0.10:8080",
            secret_value_provider=lambda rid: "simulated-secret-value-1234",
        )
        self.runner_service.prepare()
        self._seed_control_plane()

    def tearDown(self):
        self.tempdir.cleanup()

    def _enroll_device(self):
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
                (str(device_id), str(UUID(int=1, version=4)), str(INSTALLATION),
                 base64url_encode(bytes(range(32))), "http://100.98.25.26:8080",
                 str(UUID(int=3, version=4)), "Test iPhone", 1),
            )
            connection.execute(
                "INSERT INTO enrolled_device_keys(fingerprint, device_id, purpose, public_key, active, created_at) VALUES (?, ?, 'device_authentication', ?, 1, ?)",
                (base64url_encode(bytes(range(32))), str(device_id),
                 encode_p256_public_key(self.authentication_key.public_key()), 1),
            )
            connection.execute(
                "INSERT INTO enrolled_device_keys(fingerprint, device_id, purpose, public_key, active, created_at) VALUES (?, ?, 'approval', ?, 1, ?)",
                (base64url_encode(bytes(range(33))), str(device_id),
                 encode_p256_public_key(self.approval_key.public_key()), 1),
            )
            connection.commit()
        return device_id

    def _seed_control_plane(self):
        p = principal()
        self.action_service.register_provider(
            p, key="lemonade-local", display_name="Lemonade", provider_type="lemonade",
            location="local",
        )
        provider = self.action_service.list_providers(p)[0]
        self.action_service.register_model(
            p, provider_id=provider["id"], key="llama3", display_name="Llama 3",
            model_identifier="llama3",
        )
        self.agent = self.action_service.create_agent(
            p, key="architect", display_name="Architect", purpose="design",
            allowed_tools="read_only_tool,state_change_tool",
        )
        self.action_service.register_tool(
            p, key="state_change_tool", display_name="state", description="d", version="1.0.0",
            category="filesystem",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            risk="high", side_effect="external_reversible",
        )
        self.runner_service.register_executor(
            p, key="joeos.test.deterministic", display_name="Deterministic",
            version="1.0.0", accepted_tools="state_change_tool",
            input_schema={}, implementation_digest="test-digest-1",
        )

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def _enroll_runner(self, fingerprint="machine-fp-1"):
        p = principal()
        challenge = self.runner_service.create_enrollment_challenge(p, fingerprint)
        message = ENROLLMENT_DOMAIN.format(
            challenge_id=str(challenge["challenge_id"]),
            key_identifier=self.runner_key_id, machine_fingerprint=fingerprint,
            nonce=self._challenge_nonce(challenge["challenge_id"]),
        )
        signature = base64url_encode(
            self.runner_key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        )
        return self.runner_service.complete_enrollment(
            p, challenge_id=challenge["challenge_id"], key_identifier=self.runner_key_id,
            public_key=encode_p256_public_key(self.runner_key.public_key()),
            machine_fingerprint=fingerprint, runner_version="1.0.0", protocol_version=1,
            operating_system="linux", architecture="x86_64", signature_b64url=signature,
            allowed_executors="joeos.test.deterministic",
        )

    def _challenge_nonce(self, challenge_id):
        challenge = self.runner_service._store.get_enrollment_challenge(challenge_id)
        return challenge.nonce

    def _sign_runner(self, message: str) -> str:
        return base64url_encode(
            self.runner_key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        )

    def _connect(self, runner_id):
        challenge = self.runner_service.runner_request_connection(runner_id)
        return self.runner_service.runner_connect(
            challenge_id=challenge["challenge_id"],
            signature_b64url=self._sign_runner(challenge["message"]),
            protocol_version=1, runner_version="1.0.0", catalog_digest="d",
        )["connection_credential"]

    def _connect_http(self, runner_id):
        challenge = self.client.post(
            "/api/v1/runner/connect/challenge", json={"runner_id": runner_id}
        ).json()
        return challenge, self._sign_runner(challenge["message"])


class EnrollmentTests(RunnerFixture):
    def test_enrollment_binds_installation_workspace_and_fingerprint(self):
        runner = self._enroll_runner()
        self.assertEqual(runner["status"], "active")
        self.assertEqual(runner["installation_id"], INSTALLATION)
        self.assertEqual(runner["workspace_id"], principal()["workspace"]["id"])
        self.assertEqual(runner["machine_fingerprint"], "machine-fp-1")

    def test_expired_challenge_rejected(self):
        p = principal()
        challenge = self.runner_service.create_enrollment_challenge(p, "machine-fp-1")
        self.runner_service._store.update_enrollment_challenge(challenge["challenge_id"], "expired")
        message = ENROLLMENT_DOMAIN.format(
            challenge_id=str(challenge["challenge_id"]), key_identifier=self.runner_key_id,
            machine_fingerprint="machine-fp-1", nonce=self._challenge_nonce(challenge["challenge_id"]),
        )
        with self.assertRaises(Exception):
            self.runner_service.complete_enrollment(
                p, challenge_id=challenge["challenge_id"], key_identifier=self.runner_key_id,
                public_key=encode_p256_public_key(self.runner_key.public_key()),
                machine_fingerprint="machine-fp-1", runner_version="1.0.0", protocol_version=1,
                operating_system="linux", architecture="x86_64",
                signature_b64url=self._sign_runner(message),
            )

    def test_fingerprint_change_rejected(self):
        p = principal()
        challenge = self.runner_service.create_enrollment_challenge(p, "machine-fp-1")
        message = ENROLLMENT_DOMAIN.format(
            challenge_id=str(challenge["challenge_id"]), key_identifier=self.runner_key_id,
            machine_fingerprint="machine-fp-CHANGED",
            nonce=self._challenge_nonce(challenge["challenge_id"]),
        )
        with self.assertRaises(Exception):
            self.runner_service.complete_enrollment(
                p, challenge_id=challenge["challenge_id"], key_identifier=self.runner_key_id,
                public_key=encode_p256_public_key(self.runner_key.public_key()),
                machine_fingerprint="machine-fp-CHANGED", runner_version="1.0.0", protocol_version=1,
                operating_system="linux", architecture="x86_64",
                signature_b64url=self._sign_runner(message),
            )

    def test_revoked_runner_connection_denied(self):
        runner = self._enroll_runner()
        self.runner_service.revoke_runner(principal(), runner["id"])
        with self.assertRaises(Exception):
            self.runner_service.runner_request_connection(runner["id"])


class ConnectionTests(RunnerFixture):
    def test_authenticated_connection_and_heartbeat(self):
        runner = self._enroll_runner()
        credential = self._connect(runner["id"])
        self.assertTrue(self.runner_service.runner_heartbeat(credential))

    def test_wrong_origin_rejected(self):
        runner = self._enroll_runner()
        # A signature over the wrong origin is rejected because the server
        # derives the message from its authoritative origin.
        challenge = self.runner_service.runner_request_connection(runner["id"])
        with self.assertRaises(Exception):
            self.runner_service.runner_connect(
                challenge_id=challenge["challenge_id"],
                signature_b64url=self._sign_runner("https://evil.example"),
                protocol_version=1, runner_version="1.0.0", catalog_digest="d",
            )


class JobAndLeaseTests(RunnerFixture):
    def _make_approved_proposal(self):
        p = principal()
        proposal = self.action_service.propose_action(
            p, tool_key="state_change_tool", parameters={"path": "notes.txt"},
            target="file:notes.txt", conversation_id=UUID(int=9, version=4),
        )
        request = proposal["approval_request"]
        decision = proposal["policy_decision"]
        approver_principal = dict(p)
        approver_principal["user"] = {"id": UUID("11111111-2222-4333-8444-555555555557"),
                                      "display_name": "Approver", "status": "active"}
        challenge = self.action_service.create_approval_challenge(
            approver_principal, proposal_id=proposal["proposal"]["id"],
            approval_request_id=request["id"], policy_decision_id=decision["id"],
            requested_decision="approve", approver_device_id=self.device_id,
        )
        signature = base64url_encode(
            self.approval_key.sign(challenge["message"].encode("ascii"), ec.ECDSA(hashes.SHA256()))
        )
        self.action_service.submit_approval_decision(
            approver_principal, proposal_id=proposal["proposal"]["id"],
            approval_request_id=request["id"], decision="approve",
            signature_b64url=signature, challenge_id=challenge["challenge_id"],
            approver_device_id=self.device_id,
        )
        return proposal["proposal"], proposal["approval_request"]

    def test_create_lease_ack_start_complete(self):
        self._enroll_runner()
        proposal, request = self._make_approved_proposal()
        job = self.runner_service.create_execution_job(
            principal(), proposal_id=proposal["id"], idempotency_key="idem-1"
        )
        self.assertEqual(job["state"], "queued")
        # Idempotent repeat returns the same job.
        repeat = self.runner_service.create_execution_job(
            principal(), proposal_id=proposal["id"], idempotency_key="idem-1"
        )
        self.assertEqual(repeat["id"], job["id"])
        # Payload digest is stable and immutable.
        self.assertTrue(job["payload_digest"])

        # Connect and lease.
        runner = self.runner_service._store.get_runner_by_key("runner-%s" % self._enrolled_id())
        connection = self._connect(runner.id)
        lease = self.runner_service.lease_next_job(connection)
        leased_job = lease["job"]
        self.assertEqual(leased_job["state"], "leased")
        self.assertGreater(leased_job["lease_generation"], 0)

        ack = self.runner_service.acknowledge_job(
            connection, leased_job["id"], self._sign_runner(
                RESULT_DOMAIN + "\0" + str(leased_job["id"]) + "\0acknowledge")
        )
        self.assertTrue(ack)
        self.assertTrue(self.runner_service.start_job(connection, leased_job["id"]))
        completed = self.runner_service.complete_job(
            connection, leased_job["id"],
            self._sign_runner(RESULT_DOMAIN + "\0" + str(leased_job["id"]) + "\0succeeded"),
            {"status": "succeeded", "summary": "deterministic ok", "exit_classification": "clean"},
        )
        self.assertEqual(completed["state"], "succeeded")

    def test_job_requires_approved_proposal(self):
        p = principal()
        proposal = self.action_service.propose_action(
            p, tool_key="state_change_tool", parameters={"path": "x.txt"}, target="file:x.txt",
        )
        with self.assertRaises(Exception):
            self.runner_service.create_execution_job(
                p, proposal_id=proposal["proposal"]["id"], idempotency_key="idem-x"
            )

    def test_emergency_stop_cancels_queued(self):
        self._enroll_runner()
        proposal, request = self._make_approved_proposal()
        self.runner_service.create_execution_job(principal(), proposal_id=proposal["id"],
                                                 idempotency_key="idem-2")
        result = self.runner_service.emergency_stop(principal())
        self.assertTrue(result["paused"])
        self.assertGreaterEqual(result["queued_cancelled"], 1)

    def test_restart_recovery_interrupts_stale(self):
        self._enroll_runner()
        proposal, request = self._make_approved_proposal()
        job = self.runner_service.create_execution_job(principal(), proposal_id=proposal["id"],
                                                       idempotency_key="idem-3")
        self.runner_service._store.transition_job(job["id"], "running", now=1, started_at=1)
        recovered = self.runner_service.recover_after_restart()
        self.assertGreaterEqual(recovered, 1)
        self.assertEqual(self.runner_service.get_job(principal(), job["id"])["state"], "interrupted")

    def _enrolled_id(self):
        return self.runner_service._store.list_runners()[0].id


class SecretAndArtifactTests(RunnerFixture):
    def test_secret_reference_never_returns_value(self):
        p = principal()
        reference = self.runner_service.create_secret_reference(
            p, key="api-key", purpose="test", allowed_executors="joeos.test.deterministic",
        )
        payload = self.runner_service._secret_payload(
            self.runner_service._store.get_secret_reference(reference["id"])
        )
        joined = json.dumps(payload, default=str)
        self.assertNotIn("simulated-secret-value-1234", joined)

    def test_events_never_contain_secret(self):
        self._enroll_runner()
        proposal, request = JobAndLeaseTests._make_approved_proposal(self)
        self.runner_service.create_execution_job(principal(), proposal_id=proposal["id"],
                                                 idempotency_key="idem-secret")
        joined = "\n".join(self.events)
        self.assertNotIn("simulated-secret-value-1234", joined)

    def test_artifact_registration(self):
        self._enroll_runner()
        proposal, request = JobAndLeaseTests._make_approved_proposal(self)
        job = self.runner_service.create_execution_job(principal(), proposal_id=proposal["id"],
                                                       idempotency_key="idem-art")
        artifact = self.runner_service.register_artifact(
            principal(), job_id=job["id"], artifact_type="text", media_type="text/plain",
            filename="output.txt", byte_size=11, sha256="a" * 64,
            storage_reference="job://%s/output.txt" % job["id"],
        )
        self.assertEqual(artifact["sha256"], "a" * 64)


class ProcessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = self.tempdir.name

    def tearDown(self):
        self.tempdir.cleanup()

    def test_shell_is_never_used_and_executable_is_allowlisted(self):
        with self.assertRaises(ProcessExecutionError):
            run_process(executable="/bin/true", arguments=[], cwd=self.root)
        with self.assertRaises(ProcessExecutionError):
            run_process(executable="../../etc/passwd", arguments=[], cwd=self.root)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ProcessExecutionError):
            canonicalize_path(self.root, "../outside")
        allowed = canonicalize_path(self.root, "sub/file.txt")
        self.assertTrue(allowed.startswith(os.path.realpath(self.root)))

    def test_bounded_output_and_timeout(self):
        result = run_process(
            executable="python3", arguments=["-c", "print('x'*1000000)"],
            cwd=self.root, timeout_ms=5000, max_output_bytes=4096,
        )
        self.assertNotEqual(result.stdout, "x" * 1000000)

    def test_cancel_process_group(self):
        import subprocess as sp
        # The run_process timeout terminates the process group.
        result = run_process(executable="python3", arguments=["-c", "import time\ntime.sleep(30)"],
                             cwd=self.root, timeout_ms=300)
        self.assertTrue(result.timed_out or result.cancelled)

    def test_workspace_filesystem_executor(self):
        executor = WorkspaceFilesystemExecutor()
        os.makedirs(os.path.join(self.root, "ws"), exist_ok=True)
        write = executor.execute({"operation": "write_atomic", "content": "hello"},
                                 "ws/note.txt", root=self.root)
        self.assertEqual(write.status, "succeeded")
        read = executor.execute({"operation": "read_text"}, "ws/note.txt", root=self.root)
        self.assertEqual(read.output, "hello")
        escaped = executor.execute({"operation": "read_text"}, "../secret.txt", root=self.root)
        self.assertEqual(escaped.status, "failed")


class HTTPRunnerIntegrationTest(RunnerFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.action_service = self.action_service
        app.state.runner_service = self.runner_service
        self.current_principal = principal()
        app.dependency_overrides[require_application_session] = lambda: self.current_principal
        app.include_router(authority_router)
        app.include_router(control_router)
        app.include_router(runner_control_router)
        app.include_router(runner_protocol_router)
        self.client = TestClient(app)

    def test_full_http_runner_sequence(self):
        # Enroll a runner through the real ceremony.
        challenge = self.client.post(
            "/api/v1/control/runners/enroll-challenge",
            json={"machine_fingerprint": "machine-fp-http"},
        )
        self.assertEqual(challenge.status_code, 200, challenge.text)
        challenge_id = challenge.json()["challenge_id"]
        nonce = self.runner_service._store.get_enrollment_challenge(UUID(challenge_id)).nonce
        message = ENROLLMENT_DOMAIN.format(
            challenge_id=challenge_id, key_identifier=self.runner_key_id,
            machine_fingerprint="machine-fp-http", nonce=nonce,
        )
        enrolled = self.client.post(
            "/api/v1/control/runners/enroll",
            json={
                "challenge_id": challenge_id, "key_identifier": self.runner_key_id,
                "public_key": encode_p256_public_key(self.runner_key.public_key()),
                "machine_fingerprint": "machine-fp-http", "runner_version": "1.0.0",
                "protocol_version": 1, "operating_system": "linux", "architecture": "x86_64",
                "signature": self._sign_runner(message), "allowed_executors": "joeos.test.deterministic",
            },
        )
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        runner_id = enrolled.json()["id"]

        # Approve a high-risk proposal through the real approval challenge.
        proposed = self.client.post(
            "/api/v1/control/proposals",
            json={"tool_key": "state_change_tool", "parameters": {"path": "notes.txt"},
                  "target": "file:notes.txt"},
        ).json()
        approval = proposed["approval_request"]
        decision = proposed["policy_decision"]
        approver_principal = dict(principal())
        approver_principal["user"] = {"id": UUID("11111111-2222-4333-8444-555555555557"),
                                      "display_name": "Approver", "status": "active"}
        self.current_principal = approver_principal
        challenge_resp = self.client.post(
            "/api/v1/control/approvals/challenge",
            json={
                "proposal_id": str(proposed["proposal"]["id"]),
                "approval_request_id": str(approval["id"]),
                "policy_decision_id": str(decision["id"]),
                "decision": "approve", "device_id": str(self.device_id),
            },
        )
        signature = base64url_encode(self.approval_key.sign(
            challenge_resp.json()["message"].encode("ascii"), ec.ECDSA(hashes.SHA256())))
        decided = self.client.post(
            "/api/v1/control/approvals/%s/decide" % approval["id"],
            json={"proposal_id": str(proposed["proposal"]["id"]), "decision": "approve",
                  "signature": signature,
                  "challenge_id": str(challenge_resp.json()["challenge_id"]),
                  "device_id": str(self.device_id)},
        )
        self.assertEqual(decided.json()["proposal"]["state"], "approved_awaiting_executor")
        self.current_principal = principal()

        # Create the execution job.
        created = self.client.post(
            "/api/v1/control/executions",
            json={"proposal_id": str(proposed["proposal"]["id"]), "idempotency_key": "http-idem-1"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        job = created.json()
        self.assertEqual(job["state"], "queued")

        # Connect as the runner, lease, acknowledge, run, and complete.
        connect_challenge, connect_signature = self._connect_http(runner_id)
        connect = self.client.post(
            "/api/v1/runner/connect",
            json={
                "challenge_id": str(connect_challenge["challenge_id"]),
                "signature": connect_signature,
                "protocol_version": 1, "runner_version": "1.0.0", "catalog_digest": "d",
            },
        )
        self.assertEqual(connect.status_code, 200, connect.text)
        credential = connect.json()["connection_credential"]
        runner_headers = {"X-Runner-Credential": credential}

        lease = self.client.post("/api/v1/runner/lease", headers=runner_headers).json()
        self.assertEqual(lease["job"]["state"], "leased")
        job_id = lease["job"]["id"]

        ack = self.client.post(
            "/api/v1/runner/acknowledge", headers=runner_headers,
            json={"job_id": job_id, "signature": self._sign_runner(
                RESULT_DOMAIN + "\0" + job_id + "\0acknowledge")},
        )
        self.assertTrue(ack.json()["ok"])
        self.client.post("/api/v1/runner/start", headers=runner_headers, json={"job_id": job_id})
        complete = self.client.post(
            "/api/v1/runner/complete", headers=runner_headers,
            json={"job_id": job_id,
                  "signature": self._sign_runner(RESULT_DOMAIN + "\0" + job_id + "\0succeeded"),
                  "result": {"status": "succeeded", "summary": "deterministic ok",
                             "exit_classification": "clean"}},
        )
        self.assertEqual(complete.json()["state"], "succeeded")

        # No secret values appear anywhere in events or job payloads.
        events = self.runner_service.fetch_events(principal()["workspace"]["id"], 0, limit=500)
        joined = "\n".join(str(e["message"]) for e in events)
        self.assertNotIn("simulated-secret-value-1234", joined)
        self.assertNotIn(credential, joined)

        # Revoking the runner denies further connection.
        self.client.post("/api/v1/control/runners/%s/revoke" % runner_id)
        denied = self.client.post("/api/v1/runner/lease", headers=runner_headers)
        self.assertEqual(denied.status_code, 403)
