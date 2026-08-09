"""Terminal REST + WebSocket endpoints (authenticated, bounded)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from server.identity.authority_router import require_application_session

from .gateway import TerminalError

router = APIRouter(prefix="/api/v1/terminal", tags=["terminal"])

_MAX_INBOUND = 64 * 1024


def _gateway(request: Request):
    gateway = getattr(request.app.state, "terminal_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="terminal gateway unavailable")
    return gateway


@router.post("/sessions", status_code=201)
async def create_session(
    payload: dict,
    request: Request,
    principal: dict = Depends(require_application_session),
) -> dict:
    gateway = _gateway(request)
    try:
        created = gateway.create(
            cols=payload.get("cols", 120),
            rows=payload.get("rows", 30),
            principal=principal,
        )
    except TerminalError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return created


@router.get("/sessions")
async def list_sessions(
    request: Request,
    principal: dict = Depends(require_application_session),
) -> dict:
    return {"sessions": _gateway(request).list()}


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    request: Request,
    principal: dict = Depends(require_application_session),
) -> dict:
    closed = _gateway(request).close(session_id)
    return {"closed": session_id, "existed": closed}


@router.websocket("/ws/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str) -> None:
    gateway = websocket.app.state.terminal_gateway
    if gateway is None:
        await websocket.close(code=1011)
        return
    token = websocket.query_params.get("token", "")
    app_session = websocket.query_params.get("session", "")
    authority = getattr(websocket.app.state, "authority_service", None)
    if authority is None or not app_session:
        await websocket.close(code=1008)
        return
    if authority.principal_for_session(app_session) is None:
        await websocket.close(code=1008)
        return
    session = gateway.get(session_id)
    if session is None or session.closed or session.token != token:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    # Replay a bounded snapshot so a reconnect is usable.
    snapshot = gateway.snapshot(session_id)
    if snapshot:
        try:
            await websocket.send_text(snapshot)
        except Exception:  # noqa: BLE001
            pass

    async def pump() -> None:
        try:
            while True:
                chunk = await session.queue.get()
                if chunk is None or session.closed:
                    break
                await websocket.send_text(chunk)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - pump must never crash the socket
            pass

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                break
            if message_type != "websocket.receive":
                continue
            text = message.get("text")
            if text is None:
                continue
            if len(text.encode("utf-8")) > _MAX_INBOUND:
                await websocket.close(code=1009)
                break
            try:
                control = json.loads(text)
            except (ValueError, TypeError):
                gateway.write(session_id, text)
                continue
            if isinstance(control, dict) and control.get("type") == "resize":
                gateway.resize(session_id, control.get("cols"), control.get("rows"))
            elif isinstance(control, dict) and control.get("type") == "ping":
                pass
            else:
                gateway.write(session_id, text)
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
