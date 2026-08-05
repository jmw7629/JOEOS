"""HTTP API for the runner execution plane.

User-facing routes require a live application session and explicit
capabilities. Runner-protocol routes are separated and authenticated by the
runner connection credential; they are never reachable through user sessions.
No route returns secret values. No public unauthenticated queue exists.
"""

from __future__ import annotations

from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from server.identity.authority_router import require_application_session

from .service import RunnerDeniedError, RunnerError, RunnerNotFoundError, RunnerService

router = APIRouter(prefix="/api/v1/control", tags=["runners"])
runner_router = APIRouter(prefix="/api/v1/runner", tags=["runner-protocol"])


def get_runner_service(request: Request) -> RunnerService:
    service = getattr(request.app.state, "runner_service", None)
    if service is None:
        raise HTTPException(status_code=503,
                            detail={"code": "runner_unavailable", "message": "The runner plane is not initialized."})
    return service


def _raise(error: RunnerError) -> None:
    raise HTTPException(status_code=error.status_code,
                        detail={"code": error.code, "message": error.public_message})


def _run(service: RunnerService, principal: Dict, operation) -> Dict:
    try:
        return operation(principal)
    except RunnerError as error:
        _raise(error)


# ---------------------------------------------------------------------------
# User-facing routes
# ---------------------------------------------------------------------------


@router.get("/runners")
def list_runners(principal: Dict = Depends(require_application_session),
                 service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"runners": service.list_runners(p)})


@router.get("/runners/{runner_id}")
def get_runner(runner_id: UUID, principal: Dict = Depends(require_application_session),
               service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.get_runner(p, runner_id))


@router.get("/runners/{runner_id}/health")
def runner_health(runner_id: UUID, principal: Dict = Depends(require_application_session),
                  service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.runner_health(p, runner_id))


@router.post("/runners/{runner_id}/revoke")
def revoke_runner(runner_id: UUID, principal: Dict = Depends(require_application_session),
                  service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"revoked": service.revoke_runner(p, runner_id)})


@router.post("/runners/enroll-challenge")
def create_enrollment_challenge(payload: Dict, principal: Dict = Depends(require_application_session),
                                service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.create_enrollment_challenge(
        p, machine_fingerprint=str(payload.get("machine_fingerprint", ""))))


@router.post("/runners/enroll")
def complete_enrollment(payload: Dict, principal: Dict = Depends(require_application_session),
                        service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.complete_enrollment(
        p, challenge_id=UUID(str(payload["challenge_id"])),
        key_identifier=str(payload["key_identifier"]), public_key=str(payload["public_key"]),
        machine_fingerprint=str(payload["machine_fingerprint"]),
        runner_version=str(payload.get("runner_version", "")),
        protocol_version=int(payload.get("protocol_version", 1)),
        operating_system=str(payload.get("operating_system", "")),
        architecture=str(payload.get("architecture", "")),
        signature_b64url=str(payload["signature"]),
        private_network_identity=str(payload.get("private_network_identity", "")),
        allowed_executors=str(payload.get("allowed_executors", "")),
    ))


@router.get("/executors")
def list_executors(principal: Dict = Depends(require_application_session),
                   service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"executors": service.list_executors(p)})


@router.get("/executors/{executor_id}")
def get_executor(executor_id: UUID, principal: Dict = Depends(require_application_session),
                 service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.get_executor(p, executor_id))


@router.post("/executions", status_code=status.HTTP_202_ACCEPTED)
def create_execution_job(payload: Dict, principal: Dict = Depends(require_application_session),
                         service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.create_execution_job(
        p, proposal_id=UUID(str(payload["proposal_id"])),
        idempotency_key=str(payload["idempotency_key"])))


@router.get("/executions")
def list_executions(state: str = "", principal: Dict = Depends(require_application_session),
                    service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"executions": service.list_jobs(p, state or None)})


@router.get("/executions/{job_id}")
def get_execution(job_id: UUID, principal: Dict = Depends(require_application_session),
                  service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.get_job(p, job_id))


@router.post("/executions/{job_id}/cancel")
def cancel_execution(job_id: UUID, principal: Dict = Depends(require_application_session),
                     service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"cancelled": service.cancel_job(p, job_id)})


@router.get("/executions/{job_id}/artifacts")
def list_artifacts(job_id: UUID, principal: Dict = Depends(require_application_session),
                   service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"artifacts": service.list_artifacts(p, job_id)})


@router.get("/secrets")
def list_secret_references(principal: Dict = Depends(require_application_session),
                           service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: {"secrets": service.list_secret_references(p)})


@router.post("/secrets")
def create_secret_reference(payload: Dict, principal: Dict = Depends(require_application_session),
                            service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.create_secret_reference(
        p, key=str(payload["key"]), purpose=str(payload.get("purpose", "")),
        allowed_tools=str(payload.get("allowed_tools", "")),
        allowed_executors=str(payload.get("allowed_executors", "")),
        allowed_targets=str(payload.get("allowed_targets", ""))))


@router.post("/emergency-stop")
def emergency_stop(payload: Dict, principal: Dict = Depends(require_application_session),
                   service: RunnerService = Depends(get_runner_service)):
    return _run(service, principal, lambda p: service.emergency_stop(
        p, scope=str(payload.get("scope", "workspace")),
        workspace_id=UUID(str(payload["workspace_id"])) if payload.get("workspace_id") else None))


# ---------------------------------------------------------------------------
# Runner-protocol routes (runner-credential authenticated, never public)
# ---------------------------------------------------------------------------


def runner_credential(x_runner_credential: str = Header(default="", alias="X-Runner-Credential")):
    if not x_runner_credential:
        raise HTTPException(status_code=401,
                            detail={"code": "runner_credential_required", "message": "A runner credential is required."})
    return x_runner_credential


@runner_router.post("/connect/challenge")
def runner_connect_challenge(payload: Dict, service: RunnerService = Depends(get_runner_service)):
    try:
        return service.runner_request_connection(UUID(str(payload["runner_id"])))
    except RunnerError as error:
        _raise(error)


@runner_router.post("/connect")
def runner_connect(payload: Dict, service: RunnerService = Depends(get_runner_service)):
    try:
        return service.runner_connect(
            challenge_id=UUID(str(payload["challenge_id"])),
            signature_b64url=str(payload["signature"]),
            protocol_version=int(payload.get("protocol_version", 1)),
            runner_version=str(payload.get("runner_version", "")),
            catalog_digest=str(payload.get("catalog_digest", "")),
            source_identity=str(payload.get("source_identity", "")),
        )
    except RunnerError as error:
        _raise(error)


@runner_router.post("/heartbeat")
def runner_heartbeat(credential: str = Depends(runner_credential),
                     service: RunnerService = Depends(get_runner_service)):
    try:
        return {"ok": service.runner_heartbeat(credential)}
    except RunnerError as error:
        _raise(error)


@runner_router.post("/rotate")
def runner_rotate(credential: str = Depends(runner_credential),
                  service: RunnerService = Depends(get_runner_service)):
    try:
        return service.rotate_connection_credential(credential)
    except RunnerError as error:
        _raise(error)


@runner_router.post("/health")
def runner_health_report(payload: Dict, credential: str = Depends(runner_credential),
                         service: RunnerService = Depends(get_runner_service)):
    try:
        return {"ok": service.runner_report_health(credential, dict(payload))}
    except RunnerError as error:
        _raise(error)


@runner_router.post("/lease")
def runner_lease(credential: str = Depends(runner_credential),
                 service: RunnerService = Depends(get_runner_service)):
    try:
        return service.lease_next_job(credential)
    except RunnerError as error:
        _raise(error)


@runner_router.post("/acknowledge")
def runner_acknowledge(payload: Dict, credential: str = Depends(runner_credential),
                       service: RunnerService = Depends(get_runner_service)):
    try:
        return {"ok": service.acknowledge_job(credential, UUID(str(payload["job_id"])),
                                              str(payload["signature"]))}
    except RunnerError as error:
        _raise(error)


@runner_router.post("/start")
def runner_start(payload: Dict, credential: str = Depends(runner_credential),
                 service: RunnerService = Depends(get_runner_service)):
    try:
        return {"ok": service.start_job(credential, UUID(str(payload["job_id"])))}
    except RunnerError as error:
        _raise(error)


@runner_router.post("/progress")
def runner_progress(payload: Dict, credential: str = Depends(runner_credential),
                    service: RunnerService = Depends(get_runner_service)):
    try:
        return {"ok": service.report_progress(credential, UUID(str(payload["job_id"])),
                                              str(payload.get("progress", "")))}
    except RunnerError as error:
        _raise(error)


@runner_router.post("/complete")
def runner_complete(payload: Dict, credential: str = Depends(runner_credential),
                    service: RunnerService = Depends(get_runner_service)):
    try:
        return service.complete_job(credential, UUID(str(payload["job_id"])),
                                    str(payload["signature"]), dict(payload.get("result", {})))
    except RunnerError as error:
        _raise(error)
