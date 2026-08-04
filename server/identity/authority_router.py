"""Authoritative application session and principal API (Phase P3A).

Deny by default: every protected endpoint requires a valid application session
header. There is no unauthenticated owner-creation endpoint; bootstrap is
local-console-only.
"""

from __future__ import annotations

from typing import Dict
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .authority_models import (
    AuthChallengeRequest,
    AuthChallengeResponse,
    AuthRefreshRequest,
    AuthSolveRequest,
    LogoutRequest,
    PrincipalResponse,
    SessionResponse,
)
from .authority_service import (
    AuthorityError,
    AuthorityService,
)


router = APIRouter(prefix="/api/v1", tags=["authority"])


def get_authority_service(request: Request) -> AuthorityService:
    service = getattr(request.app.state, "authority_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "authority_unavailable",
                "message": "The authoritative identity service is not initialized.",
            },
        )
    return service


def _raise_authority_error(error: AuthorityError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    ) from error


def require_application_session(
    x_joeos_session: str = Header(default="", alias="X-JoeOS-Session"),
    service: AuthorityService = Depends(get_authority_service),
) -> Dict:
    """FastAPI dependency that enforces a live application session.

    Missing or invalid session ids are rejected before any handler runs.
    """
    session_id = _parse_session_id(x_joeos_session)
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "session_required", "message": "An application session is required."},
        )
    principal = service.principal_for_session(session_id)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "session_invalid",
                "message": "The application session is invalid, expired, or revoked.",
            },
        )
    return principal


def _parse_session_id(raw: str) -> UUID | None:
    value = raw.strip()
    if len(value) != 36:
        return None
    try:
        identifier = UUID(value)
    except (ValueError, TypeError):
        return None
    return identifier if str(identifier) == value.lower() else None


@router.post(
    "/auth/challenge",
    response_model=AuthChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_authentication_challenge(
    payload: AuthChallengeRequest,
    service: AuthorityService = Depends(get_authority_service),
) -> AuthChallengeResponse:
    try:
        result = service.create_authentication_challenge(
            device_id=payload.device_id,
            user_id=payload.user_id,
        )
    except AuthorityError as error:
        _raise_authority_error(error)
    return AuthChallengeResponse(**result)


@router.post("/auth/session", response_model=SessionResponse)
def solve_authentication_challenge(
    payload: AuthSolveRequest,
    service: AuthorityService = Depends(get_authority_service),
) -> SessionResponse:
    try:
        result = service.solve_authentication_challenge(
            challenge_id=payload.challenge_id,
            signature_b64url=payload.signature,
        )
    except AuthorityError as error:
        _raise_authority_error(error)
    return SessionResponse(**result)


@router.post("/auth/refresh", response_model=SessionResponse)
def refresh_application_session(
    payload: AuthRefreshRequest,
    service: AuthorityService = Depends(get_authority_service),
) -> SessionResponse:
    try:
        result = service.refresh_session(
            refresh_id=payload.refresh_id,
            refresh_token=payload.refresh_token,
        )
    except AuthorityError as error:
        _raise_authority_error(error)
    return SessionResponse(**result)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_application_session(
    payload: LogoutRequest,
    service: AuthorityService = Depends(get_authority_service),
) -> None:
    service.logout(payload.session_id)


@router.get("/principal", response_model=PrincipalResponse)
def get_principal(
    principal: Dict = Depends(require_application_session),
) -> PrincipalResponse:
    return PrincipalResponse(**principal)
