"""Typed RPC boundary between JoeOS and an Extension Host.

Every message is newline-delimited JSON. Requests carry a request ID, plugin
ID, API version, method, validated parameters, permission context, and a trace
ID. Responses carry the request ID, status, validated result, and a normalized
error. Both directions are validated with the strict models. Extension
responses are treated as untrusted: no executable objects are ever
deserialized and arbitrary method invocation is impossible.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from .models import RpcRequest, RpcResponse

RPC_MAX_BYTES = 1024 * 1024
PROTOCOL_VERSION = 1

VALID_METHODS = frozenset(
    {
        "lifecycle.activate",
        "lifecycle.deactivate",
        "lifecycle.dispose",
        "contribution.invoke",
        "system.ping",
        "system.stats",
    }
)


def encode_request(
    *,
    request_id: int,
    plugin_id: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
    api_version: int = PROTOCOL_VERSION,
) -> bytes:
    envelope = RpcRequest(id=request_id, method=method, params=params or {})
    payload = {
        "v": PROTOCOL_VERSION,
        "plugin_id": plugin_id,
        "api_version": api_version,
        "trace_id": trace_id[:80],
        "request": envelope.model_dump(),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def encode_response(
    *,
    request_id: int,
    status: str = "ok",
    result: Any = None,
    error_code: str = "",
    error_message: str = "",
) -> bytes:
    envelope = RpcResponse(
        id=request_id,
        status=status,
        result=result,
        error_code=error_code,
        error_message=error_message,
    )
    return json.dumps(envelope.model_dump(), separators=(",", ":")).encode("utf-8") + b"\n"


def decode_line(line: bytes) -> Tuple[str, Any]:
    """Decode one newline-delimited RPC message.

    Returns (kind, payload) where kind is 'request' or 'response'. Raises
    RpcProtocolError for malformed or oversized messages.
    """
    if len(line) > RPC_MAX_BYTES:
        raise RpcProtocolError("RPC message exceeds the size limit.")
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RpcProtocolError("malformed RPC message.") from exc
    elif isinstance(line, str):
        text = line
    else:
        raise RpcProtocolError("malformed RPC message.")
    try:
        payload = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RpcProtocolError("malformed RPC message.") from exc
    if not isinstance(payload, dict):
        raise RpcProtocolError("RPC message must be an object.")
    if "request" in payload:
        if payload.get("v") != PROTOCOL_VERSION:
            raise RpcProtocolError("unsupported RPC protocol version.")
        try:
            request = RpcRequest.model_validate(payload["request"])
        except ValidationError as exc:
            raise RpcProtocolError("invalid RPC request: %s" % exc.errors()[0].get("msg")) from exc
        if request.method not in VALID_METHODS:
            raise RpcProtocolError("unknown RPC method: %r" % request.method)
        return "request", request
    if "result" in payload or "status" in payload:
        try:
            response = RpcResponse.model_validate(payload)
        except ValidationError as exc:
            raise RpcProtocolError("invalid RPC response: %s" % exc.errors()[0].get("msg")) from exc
        return "response", response
    raise RpcProtocolError("RPC message is neither a request nor a response.")


class RpcProtocolError(RuntimeError):
    pass