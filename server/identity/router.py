from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .enrollment_models import (
    EnrollmentChallengeRequest,
    EnrollmentChallengeResponse,
    EnrollmentCompletionRequest,
    EnrollmentReceipt,
)
from .service import (
    DeviceEnrollmentService,
    EnrollmentConflictError,
    EnrollmentProtocolError,
)


router = APIRouter(prefix="/api/v1/device-enrollment", tags=["device-enrollment"])


def get_device_enrollment_service(request: Request) -> DeviceEnrollmentService:
    service = getattr(request.app.state, "device_enrollment_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "device_enrollment_unavailable",
                "message": "Device enrollment is not initialized.",
            },
        )
    return service


def _raise_protocol_error(error: EnrollmentProtocolError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    ) from error


@router.post(
    "/challenges",
    response_model=EnrollmentChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_device_enrollment_challenge(
    payload: EnrollmentChallengeRequest,
    service: DeviceEnrollmentService = Depends(get_device_enrollment_service),
) -> EnrollmentChallengeResponse:
    try:
        return service.create_challenge(payload)
    except EnrollmentProtocolError as error:
        _raise_protocol_error(error)


@router.post(
    "/challenges/{challenge_id}/complete",
    response_model=EnrollmentReceipt,
)
def complete_device_enrollment(
    challenge_id: UUID,
    payload: EnrollmentCompletionRequest,
    service: DeviceEnrollmentService = Depends(get_device_enrollment_service),
) -> EnrollmentReceipt:
    try:
        return service.complete_challenge(challenge_id, payload)
    except (EnrollmentConflictError, EnrollmentProtocolError) as error:
        _raise_protocol_error(error)
