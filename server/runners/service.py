"""Authoritative private runner execution plane (Phase P3C).

The backend is authoritative for runner enrollment, connection authentication,
execution-job creation and revalidation, job leasing, cancellation, secret
leases, artifacts, and terminal results. Models and clients never obtain shell
authority and never connect to runners. Runner communication is private and
authenticated; the VPS is never an unrestricted shell gateway.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid as _uuid
from typing import Callable, Dict, List, Optional
from uuid import UUID

from server.identity.crypto import base64url_encode, verify_p256_signature
from runner.joeos_runner.executors import ExecutorResult, get_executor

from .repository import SQLiteRunnerStore
from .storage import (
    ArtifactRecord,
    EnrollmentChallengeRecord,
    ExecutionJobRecord,
    ExecutorDefinitionRecord,
    RunnerConnectionRecord,
    RunnerKeyRecord,
    RunnerRecord,
    SecretLeaseRecord,
    SecretReferenceRecord,
    TERMINAL_JOB_STATES,
)

RUNNER_CONNECTION_DOMAIN = (
    "JOEOS-RUNNER-CONNECTION-V1\0{runner_id}\0{challenge_id}\0{nonce}\0{origin}\0{protocol_version}"
)
ENROLLMENT_DOMAIN = (
    "JOEOS-RUNNER-ENROLLMENT-V1\0{challenge_id}\0{key_identifier}\0{machine_fingerprint}\0{nonce}"
)
JOB_DOMAIN = "JOEOS-EXECUTION-JOB-V1"
RESULT_DOMAIN = "JOEOS-EXECUTION-RESULT-V1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _uid() -> UUID:
    return _uuid.uuid4()


class RunnerError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class RunnerDeniedError(RunnerError):
    pass


class RunnerNotFoundError(RunnerError):
    pass


class RunnerService:
    """Coordinates runner enrollment, connections, jobs, leases, secrets, and
    artifacts. All privileged execution requires an immutable approved proposal,
    revalidated policy/approvals, and an active compatible runner."""

    lease_ms = 120_000
    connection_ttl_ms = 30 * 60 * 1000
    protocol_version = 1
    installation_salt = "joeos-installation"

    def __init__(
        self,
        store: SQLiteRunnerStore,
        *,
        installation_id: Callable[[], UUID],
        action_service: Optional[object] = None,
        secret_value_provider: Callable[[UUID], Optional[str]] = lambda reference_id: None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        executor_resolver: Callable[[str], Optional[object]] = get_executor,
        now: Callable[[], int] = _now_ms,
        origin_provider: Callable[[], str] = lambda: "http://127.0.0.1:8080",
    ) -> None:
        self._store = store
        self._installation_id = installation_id
        self._action_service = action_service
        self._secret_value_provider = secret_value_provider
        self._events = event_sink
        self._executor_resolver = executor_resolver
        self._now = now
        self._origin = origin_provider
        self._paused: Dict[str, int] = {}
        self._connection_credentials: Dict[str, UUID] = {}
        self._connection_challenges: Dict[UUID, Dict[str, object]] = {}

    def prepare(self) -> None:
        self._store.prepare()

    def recover_after_restart(self) -> int:
        return self._store.recover_stale_jobs(self._now())

    def _emit(self, event: str, *, runner_id=None, job_id=None, organization_id=None,
              workspace_id=None, data: Optional[dict] = None) -> None:
        if self._events is None:
            return
        envelope = {
            "schema_version": 1,
            "event": event,
            "org": str(organization_id) if organization_id else None,
            "ws": str(workspace_id) if workspace_id else None,
            "runner": str(runner_id) if runner_id else None,
            "job": str(job_id) if job_id else None,
            "ts": self._now(),
            "trace": str(_uid()),
        }
        if data:
            envelope["data"] = data
        self._events("info", "runner", json.dumps(envelope, sort_keys=True, separators=(",", ":"))[:480])

    # ------------------------------------------------------------------
    # Enrollment ceremony
    # ------------------------------------------------------------------

    def create_enrollment_challenge(self, principal: Dict, machine_fingerprint: str) -> Dict:
        self._require(principal, "runner.enroll")
        challenge = EnrollmentChallengeRecord(
            id=_uid(), installation_id=self._installation_id(),
            organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"], purpose="runner.enroll",
            nonce=str(_uid()), expected_fingerprint=machine_fingerprint,
            issued_at=self._now(), expires_at=self._now() + 5 * 60 * 1000,
            state="open", revision=1,
        )
        self._store.create_enrollment_challenge(challenge)
        self._emit("runner.enrollment_requested", organization_id=challenge.organization_id,
                   workspace_id=challenge.workspace_id)
        return {"challenge_id": challenge.id, "expires_at": challenge.expires_at,
                "installation_id": challenge.installation_id,
                "organization_id": challenge.organization_id,
                "workspace_id": challenge.workspace_id}

    def complete_enrollment(
        self, principal: Dict, *, challenge_id: UUID, key_identifier: str, public_key: str,
        machine_fingerprint: str, runner_version: str, protocol_version: int,
        operating_system: str, architecture: str, signature_b64url: str,
        private_network_identity: str = "", allowed_executors: str = "",
    ) -> Dict:
        self._require(principal, "runner.enroll")
        challenge = self._store.get_enrollment_challenge(challenge_id)
        if challenge is None or challenge.state != "open":
            raise RunnerDeniedError(403, "challenge_not_open", "The enrollment challenge is not open.")
        if challenge.expires_at <= self._now():
            self._store.update_enrollment_challenge(challenge_id, "expired")
            raise RunnerDeniedError(403, "challenge_expired", "The enrollment challenge has expired.")
        if challenge.installation_id != self._installation_id():
            raise RunnerDeniedError(403, "installation_mismatch", "The installation id does not match.")
        if challenge.organization_id != principal["organization"]["id"] or challenge.workspace_id != principal["workspace"]["id"]:
            raise RunnerDeniedError(403, "scope_mismatch", "Organization or workspace mismatch.")
        if challenge.expected_fingerprint != machine_fingerprint:
            raise RunnerDeniedError(403, "fingerprint_mismatch", "The machine fingerprint changed.")
        message = ENROLLMENT_DOMAIN.format(
            challenge_id=str(challenge_id), key_identifier=key_identifier,
            machine_fingerprint=machine_fingerprint, nonce=challenge.nonce,
        )
        try:
            verify_p256_signature(public_key, message.encode("ascii"), signature_b64url)
        except Exception as error:  # noqa: BLE001
            raise RunnerDeniedError(403, "signature_invalid", "The runner enrollment signature is invalid.") from error
        self._store.update_enrollment_challenge(challenge_id, "solved")
        runner_id = _uid()
        runner = RunnerRecord(
            id=runner_id, key="runner-%s" % runner_id, installation_id=challenge.installation_id,
            organization_id=challenge.organization_id, workspace_id=challenge.workspace_id,
            display_name=key_identifier, machine_identity=key_identifier,
            machine_fingerprint=machine_fingerprint, operating_system=operating_system,
            architecture=architecture, runner_version=runner_version,
            protocol_version=protocol_version, private_network_identity=private_network_identity,
            allowed_executors=allowed_executors, denied_executors="",
            allowed_roots="/tmp/joeos-runner", max_concurrent_jobs=1,
            max_job_runtime_ms=600_000, artifact_limits="count:20,bytes:10485760",
            status="active", health="unknown", enrolled_at=self._now(),
            last_seen_at=self._now(), revoked_at=None, revision=1,
        )
        self._store.create_runner(runner)
        self._store.add_runner_key(RunnerKeyRecord(
            runner_id=runner_id, key_identifier=key_identifier, public_key=public_key,
            purpose="connection", created_at=self._now(), expires_at=None,
            rotation_state="active", revoked_at=None, revision=1,
        ))
        self._emit("runner.enrolled", runner_id=runner_id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id)
        return self._runner_payload(runner)

    # ------------------------------------------------------------------
    # Runner lifecycle
    # ------------------------------------------------------------------

    def revoke_runner(self, principal: Dict, runner_id: UUID) -> bool:
        self._require(principal, "runner.manage")
        runner = self._store.get_runner(runner_id)
        if runner is None:
            return False
        self._store.close_connections(runner_id, self._now())
        self._store.revoke_leases_for_runner(runner_id, self._now())
        updated = self._store.update_runner_state(runner_id, "revoked", "revoked", self._now())
        self._emit("runner.revoked", runner_id=runner_id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id)
        return updated

    def set_runner_state(self, principal: Dict, runner_id: UUID, status: str, health: str) -> bool:
        self._require(principal, "runner.manage")
        runner = self._store.get_runner(runner_id)
        if runner is None:
            return False
        if status == "revoked":
            self._store.close_connections(runner_id, self._now())
            self._store.revoke_leases_for_runner(runner_id, self._now())
        updated = self._store.update_runner_state(runner_id, status, health, self._now())
        self._emit("runner.%s" % status, runner_id=runner_id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id)
        return updated

    def rotate_runner_key(self, principal: Dict, runner_id: UUID, new_public_key: str,
                          key_identifier: str) -> bool:
        self._require(principal, "runner.manage")
        self._store.rotate_key(runner_id, self._now())
        self._store.add_runner_key(RunnerKeyRecord(
            runner_id=runner_id, key_identifier=key_identifier, public_key=new_public_key,
            purpose="connection", created_at=self._now(), expires_at=None,
            rotation_state="active", revoked_at=None, revision=1,
        ))
        return True

    def list_runners(self, principal: Dict) -> List[Dict]:
        self._require(principal, "runner.read")
        return [self._runner_payload(r) for r in self._store.list_runners(principal["workspace"]["id"])]

    def get_runner(self, principal: Dict, runner_id: UUID) -> Dict:
        self._require(principal, "runner.read")
        runner = self._store.get_runner(runner_id)
        if runner is None:
            raise RunnerNotFoundError(404, "runner_not_found", "The runner does not exist.")
        if runner.workspace_id != principal["workspace"]["id"]:
            raise RunnerDeniedError(403, "cross_workspace_denied", "Cross-workspace runner access is denied.")
        return self._runner_payload(runner)

    def runner_health(self, principal: Dict, runner_id: UUID) -> Dict:
        self._require(principal, "runner.read")
        runner = self._store.get_runner(runner_id)
        if runner is None:
            raise RunnerNotFoundError(404, "runner_not_found", "The runner does not exist.")
        return {"runner_id": runner.id, "status": runner.status, "health": runner.health,
                "last_seen_at": runner.last_seen_at}

    # ------------------------------------------------------------------
    # Connection authentication
    # ------------------------------------------------------------------

    def runner_request_connection(self, runner_id: UUID) -> Dict:
        runner = self._store.get_runner(runner_id)
        if runner is None:
            raise RunnerDeniedError(403, "runner_unknown", "The runner is unknown.")
        if runner.status in ("revoked", "disabled", "quarantined"):
            raise RunnerDeniedError(403, "runner_not_active", "The runner is not active.")
        challenge_id = _uid()
        nonce = str(_uid())
        message = RUNNER_CONNECTION_DOMAIN.format(
            runner_id=str(runner_id), challenge_id=str(challenge_id), nonce=nonce,
            origin=self._origin(), protocol_version=runner.protocol_version,
        )
        self._connection_challenges[challenge_id] = {
            "runner_id": runner_id,
            "message": message,
            "expires_at": self._now() + 5 * 60 * 1000,
        }
        return {"challenge_id": challenge_id, "message": message,
                "expires_at": self._now() + 5 * 60 * 1000}

    def runner_connect(
        self, *, challenge_id: UUID, signature_b64url: str,
        protocol_version: int, runner_version: str, catalog_digest: str,
        source_identity: str = "",
    ) -> Dict:
        pending = self._connection_challenges.get(challenge_id)
        if pending is None or pending["expires_at"] <= self._now():
            raise RunnerDeniedError(403, "challenge_invalid", "The connection challenge is invalid or expired.")
        runner_id = pending["runner_id"]
        runner = self._store.get_runner(runner_id)
        if runner is None:
            raise RunnerDeniedError(403, "runner_unknown", "The runner is unknown.")
        if runner.status in ("revoked", "disabled", "quarantined"):
            raise RunnerDeniedError(403, "runner_not_active", "The runner is not active.")
        if runner.protocol_version != protocol_version:
            raise RunnerDeniedError(403, "protocol_mismatch", "Protocol version mismatch.")
        key = self._store.get_active_key(runner_id)
        if key is None:
            raise RunnerDeniedError(403, "key_unknown", "The runner key is unknown or rotated.")
        try:
            verify_p256_signature(key.public_key, pending["message"].encode("ascii"), signature_b64url)
        except Exception as error:  # noqa: BLE001
            raise RunnerDeniedError(403, "signature_invalid", "The runner connection signature is invalid.") from error
        self._connection_challenges.pop(challenge_id, None)
        connection = RunnerConnectionRecord(
            id=_uid(), runner_id=runner_id, generation=1, connected_at=self._now(),
            last_heartbeat_at=self._now(), disconnected_at=None,
            protocol_version=protocol_version,
            runner_version=runner_version, catalog_digest=catalog_digest,
            source_identity=source_identity, status="active", revision=1,
        )
        self._store.create_connection(connection)
        self._store.update_runner_state(runner_id, "active", "healthy", self._now(),
                                        last_seen_at=self._now())
        credential = str(_uid())
        self._connection_credentials[credential] = runner_id
        self._emit("runner.connected", runner_id=runner_id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id)
        return {"connection_id": connection.id, "connection_credential": credential,
                "connection_ttl_ms": self.connection_ttl_ms}

    def _authorize_connection(self, credential: str) -> RunnerRecord:
        runner_id = self._connection_credentials.get(credential)
        if runner_id is None:
            raise RunnerDeniedError(403, "connection_invalid", "The runner connection credential is invalid.")
        runner = self._store.get_runner(runner_id)
        if runner is None or runner.status != "active":
            raise RunnerDeniedError(403, "runner_not_active", "The runner is not active.")
        if not self._store.has_active_connection(runner_id):
            raise RunnerDeniedError(403, "connection_closed", "The runner connection is closed.")
        return runner

    def runner_heartbeat(self, credential: str) -> bool:
        runner = self._authorize_connection(credential)
        connection = self._store.get_active_connection(runner.id)
        if connection is None:
            return False
        self._store.update_heartbeat(connection.id, self._now())
        self._store.update_runner_state(runner.id, "active", "healthy", self._now(),
                                        last_seen_at=self._now())
        self._emit("runner.health_updated", runner_id=runner.id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id)
        return True

    # ------------------------------------------------------------------
    # Executors
    # ------------------------------------------------------------------

    def register_executor(self, principal: Dict, *, key, display_name, version,
                          accepted_tools, input_schema, risk_floor="medium",
                          implementation_digest="") -> Dict:
        self._require(principal, "executor.manage")
        record = ExecutorDefinitionRecord(
            id=_uid(), key=key, display_name=display_name, version=version,
            supported_os="", supported_arch="", accepted_tools=accepted_tools,
            input_schema=json.dumps(input_schema), target_schema="{}", output_schema="{}",
            risk_floor=risk_floor, timeout_min_ms=1_000, timeout_max_ms=600_000,
            artifact_policy="bounded", environment_policy="allowlisted",
            network_policy="blocked", filesystem_policy="restricted",
            secret_policy="no_injection", cancellation=True, idempotency=True,
            status="active", implementation_digest=implementation_digest, revision=1,
        )
        self._store.register_executor(record)
        return self._executor_payload(record)

    def list_executors(self, principal: Dict) -> List[Dict]:
        self._require(principal, "executor.read")
        return [self._executor_payload(e) for e in self._store.list_executors()]

    def get_executor(self, principal: Dict, executor_id: UUID) -> Dict:
        self._require(principal, "executor.read")
        executor = self._store.get_executor(executor_id)
        if executor is None:
            raise RunnerNotFoundError(404, "executor_not_found", "The executor does not exist.")
        return self._executor_payload(executor)

    # ------------------------------------------------------------------
    # Execution jobs
    # ------------------------------------------------------------------

    def create_execution_job(self, principal: Dict, *, proposal_id: UUID, idempotency_key: str) -> Dict:
        self._require(principal, "execution.request")
        action_service = getattr(self, "_action_service", None)
        if action_service is None:
            raise RunnerError(503, "control_unavailable", "The action control plane is unavailable.")
        proposal = action_service._store.get_proposal(proposal_id)
        if proposal is None:
            raise RunnerDeniedError(404, "proposal_not_found", "The proposal does not exist.")
        if proposal.state != "approved_awaiting_executor":
            raise RunnerDeniedError(409, "proposal_not_approved", "Only an approved proposal can execute.")
        if proposal.workspace_id != principal["workspace"]["id"]:
            raise RunnerDeniedError(403, "cross_workspace_denied", "Cross-workspace execution is denied.")
        tool = action_service._store.get_tool_by_id(proposal.tool_id)
        if tool is None or tool.status != "active":
            raise RunnerDeniedError(403, "tool_disabled", "The tool is disabled.")
        executor = self._resolve_executor_for_tool(principal, tool)
        runner = self._select_runner(principal)
        policy = action_service._store.get_policy_decision(proposal.policy_snapshot_id) if proposal.policy_snapshot_id else None
        approvals = action_service._store.approval_decisions_for_proposal(proposal.id)
        payload = {
            "proposal_id": str(proposal.id),
            "proposal_digest": proposal.payload_digest,
            "policy_decision_id": str(policy.id) if policy else None,
            "policy_digest": policy.policy_digest if policy else None,
            "approval_digests": [a.decision_digest for a in approvals],
            "tool": tool.key,
            "tool_version": tool.version,
            "executor": executor.key,
            "executor_version": executor.version,
            "runner": str(runner.id),
            "parameters": json.loads(proposal.parameters),
            "target": proposal.canonical_target,
            "idempotency_key": idempotency_key,
        }
        payload_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        job = ExecutionJobRecord(
            id=_uid(), organization_id=proposal.organization_id,
            workspace_id=proposal.workspace_id, proposal_id=proposal.id,
            proposal_digest=proposal.payload_digest,
            policy_decision_id=policy.id if policy else _uid(),
            policy_digest=policy.policy_digest if policy else "",
            approval_ids=",".join(str(a.id) for a in approvals),
            approval_digests=",".join(a.decision_digest for a in approvals),
            tool_id=tool.id, tool_version=tool.version,
            executor_id=executor.id, executor_version=executor.version,
            runner_id=runner.id, parameters=json.dumps(payload["parameters"], sort_keys=True),
            target=proposal.canonical_target, payload=json.dumps(payload, sort_keys=True),
            payload_digest=payload_digest, requested_by=principal["user"]["id"],
            dispatched_by=None, trace_id=str(_uid()), idempotency_key=idempotency_key,
            priority=0, state="pending_revalidation", lease_generation=0, lease_owner="",
            lease_issued_at=0, lease_expires_at=0, requested_at=self._now(),
            dispatched_at=None, acknowledged_at=None, started_at=None,
            cancellation_requested_at=None, completed_at=None,
            terminal_classification="", exit_classification="", result_summary="",
            artifact_refs="", revision=1,
        )
        existing = self._store.get_job_by_idempotency(idempotency_key)
        if existing is not None:
            return self._job_payload(existing)
        self._store.create_job(job)
        # The revalidation gate ran during construction; the job is now queued.
        self._store.transition_job(job.id, "queued", now=self._now())
        self._emit("execution.queued", job_id=job.id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id,
                   data={"executor": executor.key})
        return self._job_payload(self._store.get_job(job.id))  # type: ignore[arg-type]

    def _resolve_executor_for_tool(self, principal, tool):
        executors = self._store.list_executors()
        enabled = [e for e in executors if e.status == "active" and tool.key in e.accepted_tools]
        if not enabled:
            raise RunnerDeniedError(403, "executor_unavailable", "No enabled executor accepts this tool.")
        return enabled[0]

    def _select_runner(self, principal) -> RunnerRecord:
        runners = [r for r in self._store.list_runners(principal["workspace"]["id"])
                   if r.status == "active" and r.workspace_id == principal["workspace"]["id"]]
        if not runners:
            raise RunnerDeniedError(503, "runner_unavailable", "No active compatible runner is available.")
        return runners[0]

    def get_job(self, principal: Dict, job_id: UUID) -> Dict:
        self._require(principal, "execution.read")
        job = self._store.get_job(job_id)
        if job is None:
            raise RunnerNotFoundError(404, "job_not_found", "The execution job does not exist.")
        if job.workspace_id != principal["workspace"]["id"]:
            raise RunnerDeniedError(403, "cross_workspace_denied", "Cross-workspace execution access is denied.")
        return self._job_payload(job)

    def list_jobs(self, principal: Dict, state: Optional[str] = None) -> List[Dict]:
        self._require(principal, "execution.read")
        return [self._job_payload(j) for j in self._store.list_jobs(principal["workspace"]["id"], state)]

    def cancel_job(self, principal: Dict, job_id: UUID) -> bool:
        self._require(principal, "execution.cancel")
        job = self._store.get_job(job_id)
        if job is None or job.state in TERMINAL_JOB_STATES:
            return False
        if job.state == "queued":
            self._store.transition_job(job_id, "cancelled", now=self._now())
        else:
            self._store.transition_job(job_id, "cancellation_requested", now=self._now(),
                                       cancellation_requested_at=self._now())
        self._store.revoke_leases_for_job(job_id, self._now())
        self._emit("execution.cancellation_requested", job_id=job_id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id)
        return True

    # ------------------------------------------------------------------
    # Runner protocol: lease / ack / progress / complete
    # ------------------------------------------------------------------

    def lease_next_job(self, credential: str) -> Dict:
        runner = self._authorize_connection(credential)
        eligible = [j for j in self._store.list_jobs(runner.workspace_id, "queued")
                    if j.runner_id == runner.id and self._pause_allows(runner.workspace_id)]
        if not eligible:
            return {"job": None}
        job = eligible[0]
        connection = self._store.get_active_connection(runner.id)
        leased = self._store.lease_job(job.id, runner.id,
                                       str(connection.id) if connection else "",
                                       job.lease_generation + 1, self._now(), self.lease_ms)
        if not leased:
            return {"job": None}
        leased_job = self._store.get_job(job.id)
        self._emit("execution.leased", job_id=job.id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id,
                   data={"generation": leased_job.lease_generation if leased_job else 0})
        return {"job": self._job_payload(leased_job)}

    def acknowledge_job(self, credential: str, job_id: UUID, signature_b64url: str) -> bool:
        runner = self._authorize_connection(credential)
        job = self._store.get_job(job_id)
        if job is None or job.runner_id != runner.id or job.state != "leased":
            raise RunnerDeniedError(403, "job_not_leased", "The job is not leased to this runner.")
        message = RESULT_DOMAIN + "\0" + str(job_id) + "\0acknowledge"
        key = self._store.get_active_key(runner.id)
        if key is None:
            raise RunnerDeniedError(403, "key_unknown", "The runner key is unknown.")
        try:
            verify_p256_signature(key.public_key, message.encode("ascii"), signature_b64url)
        except Exception as error:  # noqa: BLE001
            raise RunnerDeniedError(403, "signature_invalid", "The acknowledgement signature is invalid.") from error
        self._store.transition_job(job_id, "acknowledged", now=self._now(),
                                   acknowledged_at=self._now())
        self._emit("execution.acknowledged", job_id=job_id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id)
        return True

    def start_job(self, credential: str, job_id: UUID) -> bool:
        runner = self._authorize_connection(credential)
        job = self._store.get_job(job_id)
        if job is None or job.runner_id != runner.id:
            raise RunnerDeniedError(403, "job_mismatch", "The job does not belong to this runner.")
        self._store.transition_job(job_id, "running", now=self._now(), started_at=self._now())
        self._emit("execution.started", job_id=job_id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id)
        return True

    def report_progress(self, credential: str, job_id: UUID, progress: str) -> bool:
        runner = self._authorize_connection(credential)
        self._emit("execution.progress", job_id=job_id, runner_id=runner.id,
                   organization_id=runner.organization_id, workspace_id=runner.workspace_id,
                   data={"progress": progress[:240]})
        return True

    def complete_job(self, credential: str, job_id: UUID, signature_b64url: str,
                     result: Dict) -> Dict:
        runner = self._authorize_connection(credential)
        job = self._store.get_job(job_id)
        if job is None or job.runner_id != runner.id:
            raise RunnerDeniedError(403, "job_mismatch", "The job does not belong to this runner.")
        if job.state in TERMINAL_JOB_STATES:
            raise RunnerDeniedError(409, "job_terminal", "The job already has a terminal state.")
        message = RESULT_DOMAIN + "\0" + str(job_id) + "\0" + result.get("status", "")
        key = self._store.get_active_key(runner.id)
        if key is None:
            raise RunnerDeniedError(403, "key_unknown", "The runner key is unknown.")
        try:
            verify_p256_signature(key.public_key, message.encode("ascii"), signature_b64url)
        except Exception as error:  # noqa: BLE001
            self._store.transition_job(job_id, "result_validation_failed", now=self._now())
            self._emit("execution.result_rejected", job_id=job_id, runner_id=runner.id,
                       organization_id=job.organization_id, workspace_id=job.workspace_id)
            raise RunnerDeniedError(403, "signature_invalid", "The result signature is invalid.") from error
        status = result.get("status")
        terminal = {
            "succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled",
            "timed_out": "timed_out",
        }.get(status, "failed")
        self._store.transition_job(job_id, terminal, now=self._now(),
                                   terminal_classification=terminal,
                                   exit_classification=result.get("exit_classification", "clean"),
                                   result_summary=str(result.get("summary", ""))[:240])
        self._store.revoke_leases_for_job(job_id, self._now())
        self._emit("execution.%s" % terminal, job_id=job_id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id,
                   data={"summary": str(result.get("summary", ""))[:240]})
        return self._job_payload(self._store.get_job(job_id))  # type: ignore[arg-type]

    def _pause_allows(self, workspace_id: UUID) -> bool:
        if self._paused.get("global", 0):
            return False
        return self._paused.get(str(workspace_id), 0) == 0

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    def create_secret_reference(self, principal: Dict, *, key, purpose="", allowed_tools="",
                                allowed_executors="", allowed_targets="") -> Dict:
        self._require(principal, "secret.reference.manage")
        record = SecretReferenceRecord(
            id=_uid(), organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"], key=key, provider_type="development",
            purpose=purpose, allowed_tools=allowed_tools, allowed_executors=allowed_executors,
            allowed_runners="", allowed_targets=allowed_targets, status="active",
            created_at=self._now(), updated_at=self._now(), revision=1,
        )
        self._store.create_secret_reference(record)
        self._emit("secret.reference_created", organization_id=record.organization_id,
                   workspace_id=record.workspace_id)
        return self._secret_payload(record)

    def list_secret_references(self, principal: Dict) -> List[Dict]:
        self._require(principal, "secret.reference.read")
        return [self._secret_payload(r) for r in self._store.list_secret_references(principal["workspace"]["id"])]

    def issue_secret_lease(self, job_id: UUID, runner: RunnerRecord, executor: object) -> Optional[Dict]:
        job = self._store.get_job(job_id)
        if job is None:
            return None
        references = [r for r in self._store.list_secret_references(job.workspace_id) if r.status == "active"]
        if not references:
            return None
        reference = references[0]
        lease = SecretLeaseRecord(
            id=_uid(), reference_id=reference.id, job_id=job.id, runner_id=runner.id,
            executor_id=job.executor_id, purpose=reference.purpose,
            issued_at=self._now(), expires_at=self._now() + 60_000, revision=1,
        )
        self._store.create_secret_lease(lease)
        self._emit("secret.lease_created", job_id=job.id, runner_id=runner.id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id)
        return {"lease_id": lease.id, "reference_key": reference.key}

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def register_artifact(self, principal: Dict, *, job_id: UUID, artifact_type, media_type,
                          filename, byte_size, sha256, storage_reference) -> Dict:
        self._require(principal, "artifact.read")
        job = self._store.get_job(job_id)
        if job is None:
            raise RunnerNotFoundError(404, "job_not_found", "The job does not exist.")
        record = ArtifactRecord(
            id=_uid(), job_id=job.id, runner_id=job.runner_id, artifact_type=artifact_type,
            media_type=media_type, filename=filename[:120], description="", byte_size=byte_size,
            sha256=sha256, storage_reference=storage_reference[:240], sensitivity="restricted",
            created_at=self._now(), expires_at=self._now() + 7 * 24 * 3600 * 1000, revision=1,
        )
        self._store.create_artifact(record)
        self._emit("execution.artifact_created", job_id=job.id, runner_id=job.runner_id,
                   organization_id=job.organization_id, workspace_id=job.workspace_id)
        return self._artifact_payload(record)

    def list_artifacts(self, principal: Dict, job_id: UUID) -> List[Dict]:
        self._require(principal, "artifact.read")
        return [self._artifact_payload(a) for a in self._store.list_artifacts(job_id)]

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    def emergency_stop(self, principal: Dict, *, scope: str = "workspace", workspace_id: Optional[UUID] = None) -> Dict:
        self._require(principal, "execution.emergency_stop")
        key = "global" if scope == "global" else str(workspace_id or principal["workspace"]["id"])
        self._paused[key] = self._now()
        cancelled = 0
        target_ws = workspace_id or principal["workspace"]["id"]
        for job in self._store.list_jobs(target_ws):
            if job.state == "queued":
                self._store.transition_job(job.id, "cancelled", now=self._now())
                cancelled += 1
        return {"paused": True, "scope": scope, "queued_cancelled": cancelled}

    def resume(self, principal: Dict, *, workspace_id: Optional[UUID] = None) -> None:
        self._require(principal, "execution.emergency_stop")
        key = str(workspace_id or principal["workspace"]["id"])
        self._paused.pop(key, None)

    # ------------------------------------------------------------------
    # Realtime fetch (workspace-scoped cursor)
    # ------------------------------------------------------------------

    def fetch_events(self, workspace_id: UUID, cursor: int, limit: int = 100):
        return self._store.fetch_events_after(cursor, workspace_id, limit)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require(self, principal: Dict, capability: str) -> None:
        if capability not in (principal.get("capabilities") or []):
            raise RunnerDeniedError(403, "capability.denied",
                                    "This principal is not granted the %s capability." % capability)

    def _runner_payload(self, r: RunnerRecord) -> Dict:
        return {"id": r.id, "key": r.key, "installation_id": r.installation_id,
                "organization_id": r.organization_id, "workspace_id": r.workspace_id,
                "display_name": r.display_name, "machine_identity": r.machine_identity,
                "machine_fingerprint": r.machine_fingerprint,
                "operating_system": r.operating_system, "architecture": r.architecture,
                "runner_version": r.runner_version, "protocol_version": r.protocol_version,
                "private_network_identity": r.private_network_identity,
                "allowed_executors": r.allowed_executors, "status": r.status,
                "health": r.health, "last_seen_at": r.last_seen_at, "revision": r.revision}

    def _executor_payload(self, e: ExecutorDefinitionRecord) -> Dict:
        return {"id": e.id, "key": e.key, "display_name": e.display_name, "version": e.version,
                "accepted_tools": e.accepted_tools, "risk_floor": e.risk_floor,
                "timeout_max_ms": e.timeout_max_ms, "environment_policy": e.environment_policy,
                "network_policy": e.network_policy, "filesystem_policy": e.filesystem_policy,
                "secret_policy": e.secret_policy, "status": e.status,
                "implementation_digest": e.implementation_digest, "revision": e.revision}

    def _job_payload(self, j: ExecutionJobRecord) -> Dict:
        return {"id": j.id, "organization_id": j.organization_id, "workspace_id": j.workspace_id,
                "proposal_id": j.proposal_id, "proposal_digest": j.proposal_digest,
                "policy_decision_id": j.policy_decision_id, "policy_digest": j.policy_digest,
                "approval_digests": j.approval_digests, "tool_id": j.tool_id,
                "tool_version": j.tool_version, "executor_id": j.executor_id,
                "executor_version": j.executor_version, "runner_id": j.runner_id,
                "parameters": j.parameters, "target": j.target, "payload": j.payload,
                "payload_digest": j.payload_digest, "idempotency_key": j.idempotency_key,
                "state": j.state, "lease_generation": j.lease_generation,
                "lease_owner": j.lease_owner, "lease_expires_at": j.lease_expires_at,
                "requested_at": j.requested_at, "started_at": j.started_at,
                "completed_at": j.completed_at, "terminal_classification": j.terminal_classification,
                "exit_classification": j.exit_classification, "result_summary": j.result_summary,
                "artifact_refs": j.artifact_refs, "revision": j.revision}

    def _secret_payload(self, r: SecretReferenceRecord) -> Dict:
        return {"id": r.id, "organization_id": r.organization_id, "workspace_id": r.workspace_id,
                "key": r.key, "provider_type": r.provider_type, "purpose": r.purpose,
                "allowed_tools": r.allowed_tools, "allowed_executors": r.allowed_executors,
                "allowed_targets": r.allowed_targets, "status": r.status, "revision": r.revision}

    def _artifact_payload(self, a: ArtifactRecord) -> Dict:
        return {"id": a.id, "job_id": a.job_id, "runner_id": a.runner_id,
                "artifact_type": a.artifact_type, "media_type": a.media_type,
                "filename": a.filename, "byte_size": a.byte_size, "sha256": a.sha256,
                "sensitivity": a.sensitivity, "created_at": a.created_at, "expires_at": a.expires_at}
