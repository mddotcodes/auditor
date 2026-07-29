"""REST request/response models for the versioned audit API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auditor.contracts.enums import JobStage, JobStatus, SchemaVersion


class AuditOptions(BaseModel):
    """Optional knobs for a single audit job."""

    model_config = ConfigDict(extra="forbid")

    auto_fix_compile: bool = Field(
        default=False,
        description="Allow up to 3 LLM compile-fix attempts (requires API key).",
    )
    enable_llm_tests: bool = Field(
        default=True,
        description="Generate/run LLM fuzz tests when a provider key is available.",
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=3600,
        description="Override job wall-clock timeout; defaults to server/env config.",
    )


class AuditRequest(BaseModel):
    """Body for ``POST /v1/audit``."""

    model_config = ConfigDict(extra="forbid")

    sources: dict[str, str] | None = Field(
        default=None,
        description="Map of relative file path → Solidity (or project) source text.",
    )
    gist_url: str | None = Field(
        default=None,
        description="Optional GitHub Gist URL (requires allow_fetch network policy).",
    )
    options: AuditOptions = Field(default_factory=AuditOptions)

    @model_validator(mode="after")
    def _require_input(self) -> AuditRequest:
        has_sources = bool(self.sources)
        has_gist = bool(self.gist_url and self.gist_url.strip())
        if has_sources and has_gist:
            msg = "Provide only one of 'sources' or 'gist_url'"
            raise ValueError(msg)
        if not has_sources and not has_gist:
            msg = "Provide either 'sources' or 'gist_url'"
            raise ValueError(msg)
        if self.sources is not None:
            if len(self.sources) == 0:
                msg = "'sources' must not be empty"
                raise ValueError(msg)
            for path in self.sources:
                if not path or path.startswith("/") or ".." in path.split("/"):
                    msg = f"Invalid source path: {path!r}"
                    raise ValueError(msg)
        return self


class AuditSubmitResponse(BaseModel):
    """Response for ``POST /v1/audit``."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1)
    status: JobStatus = JobStatus.QUEUED


class ErrorBody(BaseModel):
    """Machine-readable error summary on a job or HTTP error payload."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, examples=["compile_failed", "timeout"])
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class JobStatusResponse(BaseModel):
    """Response for ``GET /v1/jobs/{job_id}``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = SchemaVersion.V1
    job_id: str
    status: JobStatus
    stage: JobStage | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: ErrorBody | None = None
    artifact_manifest_path: str | None = Field(
        default=None,
        description="Relative path to manifest when available (e.g. artifacts/manifest.json).",
    )


class ArtifactDownloadResponse(BaseModel):
    """JSON listing for ``GET /v1/jobs/{job_id}/artifacts`` (zip may be added later)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    manifest_available: bool = False
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Echo of manifest artifact entries when present.",
    )
