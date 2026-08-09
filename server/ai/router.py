"""Local AI Runtime REST API.

Provider-neutral inference, local-first embeddings, bounded context
construction, and AI-assisted interpretation with provenance. Availability is
reported honestly; cloud routing is never silent. Mutating actions honor
governance (Lockdown/Emergency Stop).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


def _get_service(request: Request):
    service = getattr(request.app.state, "ai_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Local AI Runtime is unavailable.")
    return service


@router.get("/overview")
def overview(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    view = service.overview()
    return {
        "provider_available": view.provider_available,
        "provider_reason": view.provider_reason,
        "model": view.model,
        "embedding_available": view.embedding_available,
        "embedding_model": view.embedding_model,
        "interpretation_count": view.interpretation_count,
        "generated_at": view.generated_at,
        "message": view.message,
    }


@router.get("/providers")
def providers(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"providers": [_provider(record) for record in service.providers_records()]}


@router.get("/chat/config")
async def chat_config(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    try:
        return await service.assistant_config()
    except Exception as error:  # noqa: BLE001 - never fabricate assistant state
        return {
            "provider": "ollama",
            "available": False,
            "reason": str(error)[:300],
            "model": "",
            "models": [],
            "streaming": False,
            "tools": [],
        }


@router.post("/chat/stream")
async def chat_stream(request: Request):
    service = _get_service(request)
    payload = await request.json()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be an array.")
    model = str(payload.get("model") or "").strip()
    context = payload.get("context")
    if not isinstance(context, dict):
        context = None

    async def event_stream():
        try:
            async for event in service.assistant_chat_stream(messages, model=model, context=context):
                yield "data: " + json.dumps(event) + "\n\n"
        except Exception as error:  # noqa: BLE001 - surfaced as an SSE error event
            yield "data: " + json.dumps({"kind": "error", "message": str(error)[:500]}) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/inference")
async def inference(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages is required.")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise HTTPException(status_code=400, detail="Each message must have a valid role and text content.")
    try:
        result = await service.infer(
            messages,
            model=str(payload.get("model") or "").strip(),
            temperature=_bounded_float(payload.get("temperature"), 0.0, 2.0, 0.25),
            max_tokens=_bounded_int(payload.get("max_tokens"), 1, 8000, 1200),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "reply": result.reply,
        "model": result.model,
        "provider": result.provider,
        "runtime": result.runtime,
        "tokens_used": result.tokens_used,
        "latency_ms": result.latency_ms,
        "cancelled": result.cancelled,
    }


@router.post("/embeddings")
async def embeddings(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    texts = payload.get("texts")
    if not isinstance(texts, list) or not texts:
        raise HTTPException(status_code=400, detail="texts is required.")
    if any(not isinstance(text, str) for text in texts):
        raise HTTPException(status_code=400, detail="texts must be strings.")
    try:
        result = await service.embed(
            texts,
            project=str(payload.get("project") or ""),
            source_refs=payload.get("source_refs"),
            privacy_class=str(payload.get("privacy_class") or "restricted"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "model": result.model,
        "provider": result.provider,
        "dimension": result.dimension,
        "vectors": result.vectors,
        "sources": result.sources,
        "deduplicated": result.deduplicated,
    }


@router.post("/context")
def build_context(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise HTTPException(status_code=400, detail="sources is required.")
    result = service.build_context(
        sources,
        project=str(payload.get("project") or ""),
        token_budget=_bounded_int(payload.get("token_budget"), 0, 32000, 0),
        purpose=str(payload.get("purpose") or "analysis"),
    )
    return {
        "context_id": result.context_id,
        "project": result.project,
        "candidates_considered": result.candidates_considered,
        "sources_selected": result.sources_selected,
        "sources_excluded": result.sources_excluded,
        "duplicate_tokens_removed": result.duplicate_tokens_removed,
        "tokens_used": result.tokens_used,
        "token_budget": result.token_budget,
        "construction_ms": result.construction_ms,
        "privacy_decisions": result.privacy_decisions,
        "chunks": result.chunks,
    }


@router.post("/interpret")
def interpret(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    try:
        record = service.create_interpretation(
            interpretation_type=str(payload.get("interpretation_type") or ""),
            summary=str(payload.get("summary") or ""),
            basis=payload.get("basis"),
            confidence=payload.get("confidence"),
            model=str(payload.get("model") or ""),
            runtime=str(payload.get("runtime") or "local"),
            privacy_class=str(payload.get("privacy_class") or "restricted"),
            project=str(payload.get("project") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _interpretation(record)


@router.get("/interpretations")
def interpretations(request: Request, interpretation_type: str = "", limit: int = 100) -> Dict[str, Any]:
    service = _get_service(request)
    return {"interpretations": [_interpretation(record) for record in service.list_interpretations(interpretation_type=interpretation_type, limit=limit)]}


@router.delete("/interpretations/{interpretation_id}")
def delete_interpretation(interpretation_id: str, request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    try:
        deleted = service.delete_interpretation(interpretation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Interpretation not found.")
    return {"deleted": interpretation_id}


def _provider(record) -> Dict[str, Any]:
    return {
        "provider_id": record.provider_id,
        "name": record.name,
        "kind": record.kind,
        "available": record.available,
        "reason": record.reason,
        "model": record.model,
        "embedding_model": record.embedding_model,
        "base_url": record.base_url,
        "privacy_class": record.privacy_class,
        "cloud_approved": record.cloud_approved,
    }


def _interpretation(record) -> Dict[str, Any]:
    return {
        "interpretation_id": record.interpretation_id,
        "interpretation_type": record.interpretation_type,
        "summary": record.summary,
        "basis": list(record.basis),
        "confidence": record.confidence,
        "model": record.model,
        "runtime": record.runtime,
        "privacy_class": record.privacy_class,
        "is_ai_assisted": record.is_ai_assisted,
        "project": record.project,
        "created_at": record.created_at,
    }


def _bounded_float(value, minimum, maximum, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_int(value, minimum, maximum, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))
