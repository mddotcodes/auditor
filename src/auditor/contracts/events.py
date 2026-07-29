"""Job event stream envelope (WebSocket / JSONL)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auditor.contracts.enums import EventLevel, JobStage, SchemaVersion, TerminalReason


class JobEvent(BaseModel):
    """One message on the canonical audit event stream.

    Clients should order by ``seq`` (monotonic per job). ``terminal`` is set on
    the final event(s) when the job ends.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = SchemaVersion.V1
    job_id: str = Field(..., min_length=1, description="Job identifier (UUID/ULID).")
    seq: int = Field(..., ge=0, description="Monotonic sequence number within the job.")
    ts: datetime = Field(..., description="Event timestamp (UTC recommended).")
    stage: JobStage | None = Field(
        default=None,
        description="Active pipeline stage, if applicable.",
    )
    level: EventLevel = EventLevel.INFO
    message: str = Field(..., min_length=1)
    progress: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Optional overall progress percentage.",
    )
    data: dict[str, Any] = Field(default_factory=dict)
    terminal: TerminalReason | None = Field(
        default=None,
        description="Set when this event marks (or accompanies) job termination.",
    )

    def is_terminal(self) -> bool:
        return self.terminal is not None
