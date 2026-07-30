from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlsplit

from .models import AuditEventRecord, RealtimeEnvelope


class EventRepository(Protocol):
    def latest_cursor(self) -> int:
        ...

    def oldest_cursor(self) -> int:
        ...

    def fetch_after(self, cursor: int, limit: int) -> List[AuditEventRecord]:
        ...


class RealtimeService:
    """Creates deterministic snapshots and resumable audit-event streams."""

    def __init__(
        self,
        repository: EventRepository,
        snapshot_provider: Callable[[], Dict[str, Any]],
        *,
        allowed_origins: Sequence[str] = (),
        batch_size: int = 40,
        poll_seconds: float = 0.5,
        heartbeat_seconds: float = 15.0,
        max_payload_bytes: int = 262_144,
        max_inbound_bytes: int = 4_096,
        now_provider: Optional[Callable[[], datetime]] = None,
        monotonic_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        self.repository = repository
        self.snapshot_provider = snapshot_provider
        self.batch_size = max(1, min(100, int(batch_size)))
        self.poll_seconds = max(0.05, min(5.0, float(poll_seconds)))
        self.heartbeat_seconds = max(0.25, min(30.0, float(heartbeat_seconds)))
        self.max_payload_bytes = max(4_096, min(1_048_576, int(max_payload_bytes)))
        self.max_inbound_bytes = max(256, min(65_536, int(max_inbound_bytes)))
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic_provider or time.monotonic
        self._allowed_origins = {
            normalized
            for origin in allowed_origins
            for normalized in [self._normalize_origin(origin)]
            if normalized
        }

    def initial_snapshot(self, after: Optional[int]) -> Tuple[RealtimeEnvelope, int]:
        latest = max(0, int(self.repository.latest_cursor()))
        oldest = max(0, int(self.repository.oldest_cursor()))
        cursor = latest if after is None else max(0, int(after))
        telemetry = self._bounded_payload(self.snapshot_provider())
        payload = self._bounded_payload(
            {
                "telemetry": telemetry,
                "resume": {
                    "requested_after": after,
                    "oldest_event_cursor": oldest,
                    "latest_event_cursor": latest,
                    "history_gap": after is not None and oldest > 0 and after < oldest - 1,
                    "cursor_ahead": after is not None and after > latest,
                },
            }
        )
        return (
            RealtimeEnvelope(
                event_id=None,
                cursor=cursor,
                event_type="telemetry.snapshot",
                occurred_at=self._now(),
                source="joeos",
                severity="info",
                payload=payload,
            ),
            cursor,
        )

    def events_after(self, cursor: int) -> List[RealtimeEnvelope]:
        records = self.repository.fetch_after(cursor, self.batch_size)
        envelopes: List[RealtimeEnvelope] = []
        last_cursor = cursor
        for record in sorted(records, key=lambda item: item.event_id):
            if record.event_id <= last_cursor:
                continue
            envelopes.append(
                RealtimeEnvelope(
                    event_id=record.event_id,
                    cursor=record.event_id,
                    event_type="audit.event",
                    occurred_at=record.occurred_at,
                    source=record.source,
                    severity=record.severity,
                    payload=self._bounded_payload({"message": record.message}),
                )
            )
            last_cursor = record.event_id
            if len(envelopes) >= self.batch_size:
                break
        return envelopes

    def heartbeat(self, cursor: int) -> RealtimeEnvelope:
        return RealtimeEnvelope(
            event_id=None,
            cursor=max(0, int(cursor)),
            event_type="stream.heartbeat",
            occurred_at=self._now(),
            source="joeos",
            severity="info",
            payload={"status": "connected"},
        )

    async def stream(
        self,
        after: Optional[int],
        stop_event: asyncio.Event,
    ) -> AsyncIterator[RealtimeEnvelope]:
        snapshot, cursor = await asyncio.to_thread(self.initial_snapshot, after)
        yield snapshot
        last_emit = self._monotonic()
        while not stop_event.is_set():
            envelopes = await asyncio.to_thread(self.events_after, cursor)
            if envelopes:
                for envelope in envelopes:
                    if stop_event.is_set():
                        return
                    if envelope.event_id is None or envelope.event_id <= cursor:
                        continue
                    cursor = envelope.cursor
                    last_emit = self._monotonic()
                    yield envelope
                if len(envelopes) >= self.batch_size:
                    await asyncio.sleep(0)
                    continue
            now = self._monotonic()
            if now - last_emit >= self.heartbeat_seconds:
                last_emit = now
                yield self.heartbeat(cursor)
            remaining = max(0.05, min(self.poll_seconds, self.heartbeat_seconds - (self._monotonic() - last_emit)))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

    def origin_allowed(self, origin: Optional[str], host: Optional[str]) -> bool:
        if origin is None:
            return True  # Native clients do not send the browser Origin header.
        if not origin or len(origin) > 512 or not host or len(host) > 512:
            return False
        normalized = self._normalize_origin(origin)
        if normalized is None:
            return False
        if normalized in self._allowed_origins:
            return True
        parsed = urlsplit(normalized)
        try:
            host_parts = urlsplit("//" + host)
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            host_port = host_parts.port or origin_port
        except ValueError:
            return False
        return (
            bool(parsed.hostname)
            and parsed.hostname.lower() == (host_parts.hostname or "").lower()
            and origin_port == host_port
        )

    def _bounded_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Realtime payload must be an object.")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Realtime payload must be JSON serializable.") from exc
        if len(encoded) > self.max_payload_bytes:
            raise ValueError("Realtime payload exceeds the configured size limit.")
        return payload

    @staticmethod
    def _normalize_origin(origin: str) -> Optional[str]:
        value = origin.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return None
            port = parsed.port
        except ValueError:
            return None
        authority = parsed.hostname.lower()
        if ":" in authority:
            authority = "[" + authority + "]"
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += ":" + str(port)
        return parsed.scheme + "://" + authority
