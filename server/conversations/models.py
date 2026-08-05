"""Wire models for canonical conversations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConversationCreateRequest(StrictWireModel):
    title: str = Field(default="Conversation", max_length=120)


class ConversationRenameRequest(StrictWireModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationMessageRequest(StrictWireModel):
    content: str = Field(min_length=1, max_length=40_000)
    idempotency_key: Optional[UUID] = Field(default=None, strict=False)


class MessagePayload(StrictWireModel):
    message_id: UUID
    role: str
    content: str
    status: str
    provider: Optional[str]
    model: Optional[str]
    tokens_used: Optional[int]
    created_at: int
    completed_at: Optional[int]
    error_detail: str = ""


class ConversationPayload(StrictWireModel):
    conversation_id: UUID
    title: str
    status: str
    created_at: int
    updated_at: int
    revision: int
    messages: List[MessagePayload]


class ConversationListResponse(StrictWireModel):
    conversations: List[ConversationPayload]
    stream_supported: bool = False


class RunPayload(StrictWireModel):
    run_id: UUID
    conversation_id: UUID
    message_id: UUID
    status: str
    provider: Optional[str]
    model: Optional[str]
    parent_run_id: Optional[UUID]
    created_at: int
    started_at: Optional[int]
    terminal_at: Optional[int]
    error_detail: str = ""


class RetryRequest(StrictWireModel):
    parent_run_id: Optional[UUID] = Field(default=None, strict=False)
