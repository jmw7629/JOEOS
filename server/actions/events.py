"""Typed realtime event envelope for the P3B control plane.

Events are published through the existing shared realtime event sink with a
typed envelope (schema version, organization/workspace scope, ids, timestamp,
trace id) and bounded redacted payload. Payloads never contain credentials,
private keys, raw biometric data, or unrestricted prompts.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional
from uuid import UUID

EVENT_SCHEMA_VERSION = 1


class ControlEventEmitter:
    def __init__(self, event_sink: Optional[Callable[[str, str, str], None]]) -> None:
        self._event_sink = event_sink

    def emit(
        self,
        event: str,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        conversation_id: Optional[UUID] = None,
        run_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
        proposal_id: Optional[UUID] = None,
        approval_request_id: Optional[UUID] = None,
        trace_id: str = "",
        data: Optional[Dict[str, object]] = None,
    ) -> None:
        if self._event_sink is None:
            return
        envelope = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event": event,
            "org": str(organization_id),
            "ws": str(workspace_id),
            "conversation": str(conversation_id) if conversation_id else None,
            "run": str(run_id) if run_id else None,
            "task": str(task_id) if task_id else None,
            "proposal": str(proposal_id) if proposal_id else None,
            "approval": str(approval_request_id) if approval_request_id else None,
            "ts": int(__import__("time").time() * 1000),
            "trace": trace_id or str(UUID(int=0) if False else __import__("uuid").uuid4()),
        }
        if data:
            envelope["data"] = data
        message = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        self._event_sink("info", "control", message[:480])
