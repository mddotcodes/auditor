"""Normalized multi-tool findings schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    UNKNOWN = "unknown"


class FindingConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class FindingLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_col: int | None = Field(default=None, ge=0)
    end_col: int | None = Field(default=None, ge=0)


class Finding(BaseModel):
    """One normalized issue from any analyzer."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(..., min_length=1, description="slither | aderyn | metamorphic | …")
    detector_id: str = Field(..., min_length=1)
    severity: FindingSeverity = FindingSeverity.UNKNOWN
    confidence: FindingConfidence = FindingConfidence.UNKNOWN
    title: str = Field(..., min_length=1)
    description: str = ""
    locations: list[FindingLocation] = Field(default_factory=list)
    raw_ref: str | None = Field(
        default=None,
        description="Relative artifact path to the tool's raw output.",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


class FindingsDocument(BaseModel):
    """``artifacts/static/findings.json`` (and aggregates)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    findings: list[Finding] = Field(default_factory=list)
    tools_run: list[str] = Field(default_factory=list)
    tools_failed: list[str] = Field(default_factory=list)

    def extend(self, items: list[Finding]) -> None:
        self.findings.extend(items)

    def counts_by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.severity.value] = out.get(f.severity.value, 0) + 1
        return out

    def counts_by_tool(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.tool] = out.get(f.tool, 0) + 1
        return out


def dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Best-effort dedup: same tool-agnostic key of file+line+normalized title."""
    seen: set[str] = set()
    out: list[Finding] = []
    for f in findings:
        loc = f.locations[0] if f.locations else None
        key = "|".join(
            [
                (loc.file or "").lower() if loc is not None else "",
                str(loc.start_line or "") if loc is not None else "",
                f.title.strip().lower()[:80],
                f.detector_id.lower(),
            ]
        )
        # Allow same detector from different tools (keep both) — key includes tool-less title
        # but detector_id differs across tools, so both retained unless identical detector_id.
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
