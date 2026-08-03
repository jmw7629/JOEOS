from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_id: int = Field(ge=1)
    occurred_at: datetime
    source: str = Field(min_length=1, max_length=80)
    severity: Literal["info", "success", "warn", "error"]
    message: str = Field(min_length=1, max_length=500)


class RealtimeEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: Optional[int] = Field(default=None, ge=1)
    cursor: int = Field(ge=0)
    event_type: Literal["telemetry.snapshot", "audit.event", "stream.heartbeat"]
    occurred_at: datetime
    source: str = Field(min_length=1, max_length=80)
    severity: Literal["info", "success", "warn", "error"]
    payload: Dict[str, Any]
