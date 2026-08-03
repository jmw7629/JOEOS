from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Optional

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

router = APIRouter()


async def _producer(websocket: WebSocket, stream, stop_event: asyncio.Event) -> None:
    try:
        async for envelope in stream:
            await websocket.send_json(envelope.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()


async def _receiver(
    websocket: WebSocket,
    max_inbound_bytes: int,
    stop_event: asyncio.Event,
) -> None:
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                stop_event.set()
                return
            if message_type != "websocket.receive":
                continue
            text = message.get("text")
            if text is None:
                continue
            if len(text.encode("utf-8")) > max_inbound_bytes:
                await websocket.close(code=1009)
                stop_event.set()
                return
            if not text.strip():
                continue
            await websocket.close(code=1003)
            stop_event.set()
            return
    except WebSocketDisconnect:
        stop_event.set()


@router.websocket("/ws/events")
async def realtime_stream(websocket: WebSocket) -> None:
    service = websocket.app.state.realtime_service
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not service.origin_allowed(origin, host):
        await websocket.close(code=1008)
        return
    raw_after = websocket.query_params.get("after")
    try:
        after: Optional[int] = int(raw_after) if raw_after is not None else None
    except (TypeError, ValueError):
        await websocket.close(code=1000)
        return
    await websocket.accept()

    stop_event = asyncio.Event()
    stream = service.stream(after, stop_event)
    producer = asyncio.create_task(_producer(websocket, stream, stop_event))
    receiver = asyncio.create_task(_receiver(websocket, service.max_inbound_bytes, stop_event))
    try:
        await asyncio.gather(producer, receiver)
    finally:
        stop_event.set()
        producer.cancel()
        receiver.cancel()
        with suppress(asyncio.CancelledError):
            await producer
        with suppress(asyncio.CancelledError):
            await receiver
