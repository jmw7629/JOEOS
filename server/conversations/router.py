"""Canonical conversation HTTP API (Phase P3A).

Every route is protected by a live application session (deny by default). The
backend is authoritative for conversation history and message state.
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from server.identity.authority_router import (
    require_application_session,
)

from .models import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationMessageRequest,
    ConversationPayload,
)
from .service import ConversationError, ConversationService


router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def get_conversation_service(request: Request) -> ConversationService:
    service = getattr(request.app.state, "conversation_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "conversations_unavailable",
                "message": "The conversation service is not initialized.",
            },
        )
    return service


def _raise_conversation_error(error: ConversationError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    ) from error


@router.post("", response_model=ConversationPayload, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreateRequest,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationPayload:
    try:
        result = service.create_conversation(principal, payload.title)
    except ConversationError as error:
        _raise_conversation_error(error)
    return ConversationPayload(**result)


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationListResponse:
    try:
        conversations = service.list_conversations(principal)
    except ConversationError as error:
        _raise_conversation_error(error)
    return ConversationListResponse(
        conversations=[
            ConversationPayload(**item) for item in conversations
        ],
        stream_supported=service.streaming_supported(principal),
    )


@router.get("/{conversation_id}", response_model=ConversationPayload)
def get_conversation(
    conversation_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationPayload:
    try:
        result = service.get_conversation(principal, conversation_id)
    except ConversationError as error:
        _raise_conversation_error(error)
    return ConversationPayload(**result)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationPayload,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_message(
    conversation_id: UUID,
    payload: ConversationMessageRequest,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationPayload:
    try:
        result = await service.submit_message(principal, conversation_id, payload.content)
    except ConversationError as error:
        _raise_conversation_error(error)
    return ConversationPayload(**result)


@router.post(
    "/{conversation_id}/stream",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_message(
    conversation_id: UUID,
    payload: ConversationMessageRequest,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> StreamingResponse:
    """Server-sent events for one message and its response. Partial `message.delta`
    events are emitted only when the selected provider genuinely streams;
    otherwise a single completed delta is emitted with honest non-streaming
    semantics. `run.completed`, `run.cancelled`, and `run.failed` are terminal
    events; `done` closes the stream."""

    async def events() -> None:
        try:
            yield "event: conversation.opened\ndata: {}\n\n"
            async for item in service.stream_message(principal, conversation_id, payload.content):
                event = str(item["event"])
                data = json.dumps(item, separators=(",", ":"))
                yield "event: %s\ndata: %s\n\n" % (event, data)
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            yield "event: done\ndata: {\"cancelled\":true}\n\n"
            raise
        except ConversationError as error:
            yield (
                "event: error\ndata: %s\n\n"
                % json.dumps({"code": error.code, "message": error.public_message}, separators=(",", ":"))
            )
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/retry", response_model=ConversationPayload)
async def retry_last_message(
    conversation_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationPayload:
    try:
        result = await service.retry_last_message(principal, conversation_id)
    except ConversationError as error:
        _raise_conversation_error(error)
    return ConversationPayload(**result)


@router.post(
    "/{conversation_id}/runs/{run_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_generation(
    conversation_id: UUID,
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ConversationService = Depends(get_conversation_service),
) -> None:
    cancelled = service.cancel_run(principal, run_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": "No active run matches that id."},
        )
