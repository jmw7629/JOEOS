from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .models import (
    ConfigurationGuideRequest,
    ConfigurationProposalEnvelope,
    WorkspaceEnvelope,
    WorkspaceUpdate,
)
from .service import RevisionConflictError, WorkspaceService, WorkspaceValidationError


router = APIRouter(prefix="/api", tags=["workspace"])


def get_workspace_service(request: Request) -> WorkspaceService:
    service = getattr(request.app.state, "workspace_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace service is not initialized.",
        )
    return service


@router.get("/workspace", response_model=WorkspaceEnvelope)
def get_workspace(service: WorkspaceService = Depends(get_workspace_service)) -> WorkspaceEnvelope:
    return service.get_workspace()


@router.put("/workspace", response_model=WorkspaceEnvelope)
def put_workspace(
    payload: WorkspaceUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceEnvelope:
    try:
        return service.update_workspace(payload)
    except RevisionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Workspace changed on another device. Reload and apply the customization again.",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except WorkspaceValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/configuration/guide", response_model=ConfigurationProposalEnvelope)
def configuration_guide(
    payload: ConfigurationGuideRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> ConfigurationProposalEnvelope:
    try:
        return service.guide(payload)
    except WorkspaceValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
