from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .models import ActivityEnvelope, OverviewEnvelope, ServicesEnvelope
from .service import CommandCenterService


router = APIRouter(prefix="/api/v1", tags=["command-center"])


def get_command_center_service(request: Request) -> CommandCenterService:
    service = getattr(request.app.state, "command_center_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Command Center service is not initialized.",
        )
    return service


@router.get("/command-center/overview", response_model=OverviewEnvelope)
def command_center_overview(
    service: CommandCenterService = Depends(get_command_center_service),
) -> OverviewEnvelope:
    return service.overview()


@router.get("/command-center/services", response_model=ServicesEnvelope)
def command_center_services(
    service: CommandCenterService = Depends(get_command_center_service),
) -> ServicesEnvelope:
    return service.services()


@router.get("/command-center/activity", response_model=ActivityEnvelope)
def command_center_activity(
    limit: int = 40,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    before: Optional[int] = None,
    service: CommandCenterService = Depends(get_command_center_service),
) -> ActivityEnvelope:
    return service.activity(limit=limit, severity=severity, source=source, before=before)
