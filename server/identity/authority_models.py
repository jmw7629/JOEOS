"""Wire models for the authoritative application identity and session API."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AuthChallengeRequest(StrictWireModel):
    device_id: UUID = Field(strict=False, description="The enrolled device id.")
    user_id: UUID = Field(strict=False, description="The principal user id this device is assigned to.")


class AuthChallengeResponse(StrictWireModel):
    challenge_id: UUID
    device_id: UUID
    user_id: UUID
    organization_id: UUID
    workspace_id: UUID
    server_nonce: str
    expires_at: int
    message: str


class AuthSolveRequest(StrictWireModel):
    challenge_id: UUID = Field(strict=False)
    signature: str = Field(min_length=20, max_length=512)


class AuthRefreshRequest(StrictWireModel):
    refresh_id: UUID = Field(strict=False)
    refresh_token: str = Field(min_length=32, max_length=128)


class SessionPayload(StrictWireModel):
    session_id: UUID
    user_id: UUID
    device_id: UUID
    organization_id: UUID
    workspace_id: UUID
    status: str
    created_at: int
    expires_at: int


class SessionResponse(StrictWireModel):
    session: SessionPayload
    refresh_token: str
    refresh_id: UUID
    principal: Dict[str, Any]


class PrincipalResponse(StrictWireModel):
    session_id: UUID
    device_id: UUID
    user: Dict[str, Any]
    organization: Dict[str, Any]
    workspace: Dict[str, Any]
    roles: List[str]
    capabilities: List[str]


class LogoutRequest(StrictWireModel):
    session_id: UUID = Field(strict=False)


class AuthorityListResponse(StrictWireModel):
    users: List[Dict[str, Any]] = []
    organizations: List[Dict[str, Any]] = []
    workspaces: List[Dict[str, Any]] = []
    roles: List[Dict[str, Any]] = []
    capabilities: List[Dict[str, Any]] = []
    devices: List[Dict[str, Any]] = []
