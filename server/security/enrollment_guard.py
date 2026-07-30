from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from starlette.responses import JSONResponse


class _RequestBodyTooLarge(Exception):
    pass


class EnrollmentRequestGuardMiddleware:
    """Bound enrollment request bodies and per-client attempts before JSON parsing."""

    def __init__(
        self,
        app,
        *,
        maximum_body_bytes: int = 32_768,
        attempts_per_minute: int = 30,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if maximum_body_bytes < 1 or attempts_per_minute < 1:
            raise ValueError("Enrollment request guard limits must be positive.")
        self.app = app
        self.maximum_body_bytes = maximum_body_bytes
        self.attempts_per_minute = attempts_per_minute
        self._clock = clock or time.monotonic
        self._attempts: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send) -> None:
        if not self._guards(scope):
            await self.app(scope, receive, send)
            return

        source = self._source(scope)
        if not self._admit(source):
            await self._error(
                scope,
                receive,
                send,
                429,
                "device_enrollment_rate_limited",
                "Too many device-enrollment attempts. Wait one minute and retry.",
                {"Retry-After": "60"},
            )
            return

        content_length = self._content_length(scope)
        if content_length is False:
            await self._error(
                scope,
                receive,
                send,
                400,
                "invalid_content_length",
                "The enrollment request Content-Length is invalid.",
            )
            return
        if isinstance(content_length, int) and content_length > self.maximum_body_bytes:
            await self._too_large(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        response_started = False

        async def guarded_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await self._too_large(scope, receive, send)

    def _admit(self, source: str) -> bool:
        now = self._clock()
        cutoff = now - 60.0
        with self._lock:
            attempts = self._attempts.setdefault(source, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.attempts_per_minute:
                return False
            attempts.append(now)
            if len(self._attempts) > 4_096:
                for key in tuple(self._attempts):
                    bucket = self._attempts[key]
                    while bucket and bucket[0] <= cutoff:
                        bucket.popleft()
                    if not bucket:
                        del self._attempts[key]
                    if len(self._attempts) <= 2_048:
                        break
            return True

    @staticmethod
    def _guards(scope) -> bool:
        return (
            scope.get("type") == "http"
            and str(scope.get("method", "")).upper() == "POST"
            and str(scope.get("path", "")).startswith("/api/v1/device-enrollment/")
        )

    @staticmethod
    def _source(scope) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])[:128]
        return "unknown"

    @staticmethod
    def _content_length(scope):
        values = [
            value
            for name, value in scope.get("headers", ())
            if name.lower() == b"content-length"
        ]
        if not values:
            return None
        if len(values) != 1:
            return False
        try:
            raw = values[0].decode("ascii")
            if not raw or not raw.isdigit():
                return False
            value = int(raw)
        except (UnicodeDecodeError, ValueError):
            return False
        return value

    async def _too_large(self, scope, receive, send) -> None:
        await self._error(
            scope,
            receive,
            send,
            413,
            "device_enrollment_body_too_large",
            "Device-enrollment request bodies may not exceed %d bytes."
            % self.maximum_body_bytes,
        )

    @staticmethod
    async def _error(
        scope,
        receive,
        send,
        status_code: int,
        code: str,
        message: str,
        headers: Optional[dict] = None,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": message}},
            headers=headers,
        )
        await response(scope, receive, send)
