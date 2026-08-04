"""Performance Tracing.

Spans carry only safe metadata: operation names, durations, queue vs.
execution time, status, and cancellation flags. Secrets, prompts, source code,
private messages, full paths, and raw query values are never accepted — the
``begin``/``finish`` API only stores a caller-supplied safe_metadata dict that
is validated to contain scalar, redacted values.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .storage import PerformanceStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Span:
    def __init__(self, tracer: "Tracer", trace_id: str, parent_trace_id: str, service: str, operation: str, sampling: bool) -> None:
        self.tracer = tracer
        self.trace_id = trace_id
        self.parent_trace_id = parent_trace_id
        self.service = service
        self.operation = operation
        self.sampling = sampling
        self._start = time.monotonic()
        self._start_iso = _now_iso()
        self.queue_ms = 0.0
        self.execution_ms = 0.0
        self.status = "ok"
        self.cancelled = False
        self.safe_metadata: Dict[str, Any] = {}
        self._finished = False

    def set_queue_ms(self, value: float) -> "Span":
        self.queue_ms = max(0.0, value)
        return self

    def set_cancelled(self, value: bool = True) -> "Span":
        self.cancelled = bool(value)
        if self.cancelled:
            self.status = "cancelled"
        return self

    def add_metadata(self, key: str, value: Any) -> "Span":
        if not self.sampling:
            return self
        if len(self.safe_metadata) >= 16:
            return self
        if key in {"secret", "prompt", "token", "path", "query", "message", "source_code", "credentials"}:
            raise ValueError("Trace metadata key is not allowed: %s" % key)
        if not isinstance(value, (str, int, float, bool)) or value is None:
            raise ValueError("Trace metadata values must be scalar and redacted.")
        self.safe_metadata[key] = value
        return self

    def finish(self, *, status: Optional[str] = None) -> None:
        if self._finished:
            return
        self._finished = True
        if status is not None:
            self.status = status
        self.tracer._finish(self)


class Tracer:
    """Sampled, redacted tracing for operations across JoeOS."""

    def __init__(
        self,
        storage: Optional[PerformanceStorage] = None,
        *,
        sample_rate: float = 1.0,
        max_spans_per_batch: int = 100,
    ) -> None:
        self._storage = storage
        self.sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self._max = max(1, int(max_spans_per_batch))
        self._lock = threading.RLock()
        self._pending: List[Span] = []
        self._trace_counter = 0
        self._span_counter = 0

    def begin(self, service: str, operation: str, *, parent: Optional[Span] = None, trace_id: str = "") -> Span:
        with self._lock:
            self._span_counter += 1
            sampling = self.sample_rate >= 1.0 or (self._span_counter % _denominator(self.sample_rate) == 0)
            span_trace_id = trace_id or (parent.trace_id if parent else self._new_trace_id())
            parent_id = parent.trace_id if parent else ""
        return Span(self, span_trace_id, parent_id, service[:64], operation[:96], sampling)

    def _finish(self, span: Span) -> None:
        if not span.sampling:
            return
        span.execution_ms = max(0.0, (time.monotonic() - span._start) * 1000.0)
        with self._lock:
            self._pending.append(span)
            if len(self._pending) >= self._max:
                batch = self._pending
                self._pending = []
            else:
                batch = None
        if batch:
            self._persist(batch)

    def _persist(self, spans: List[Span]) -> None:
        if self._storage is None:
            return
        import json
        for span in spans:
            self._storage.insert_trace({
                "trace_id": span.trace_id,
                "parent_trace_id": span.parent_trace_id,
                "service": span.service,
                "operation": span.operation,
                "start_iso": span._start_iso,
                "duration_ms": span.queue_ms + span.execution_ms,
                "queue_ms": span.queue_ms,
                "execution_ms": span.execution_ms,
                "status": span.status,
                "cancelled": span.cancelled,
                "safe_metadata": json.dumps(span.safe_metadata, sort_keys=True, separators=(",", ":")),
            })

    def recent(self, service: str = "", operation: str = "", limit: int = 100) -> List[dict]:
        if self._storage is None:
            return []
        return self._storage.list_traces(service=service, operation=operation, limit=limit)

    def _new_trace_id(self) -> str:
        self._trace_counter += 1
        return "trace-%04d-%d" % (self._trace_counter % 10000, int(time.monotonic()))


def _denominator(rate: float) -> int:
    if rate <= 0.0:
        return 1000000
    return max(1, int(round(1.0 / min(1.0, rate))))
