"""Map JobContext → public REST models."""

from __future__ import annotations

from auditor.contracts.jobs import (
    ArtifactDownloadResponse,
    ErrorBody,
    JobStatusResponse,
)
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext


def to_job_status(ctx: JobContext) -> JobStatusResponse:
    error: ErrorBody | None = None
    if ctx.error_code or ctx.error_message:
        error = ErrorBody(
            code=ctx.error_code or "error",
            message=ctx.error_message or "job error",
        )
    manifest_path: str | None = None
    if ctx.job_paths.manifest.is_file() or ctx.manifest is not None:
        manifest_path = JOB_LAYOUT.manifest
    return JobStatusResponse(
        job_id=ctx.job_id,
        status=ctx.status,
        stage=ctx.stage,
        progress=ctx.progress,
        created_at=ctx.created_at,
        updated_at=ctx.updated_at,
        error=error,
        artifact_manifest_path=manifest_path,
    )


def to_artifacts(ctx: JobContext) -> ArtifactDownloadResponse:
    artifacts: list[dict[str, object]] = []
    manifest_available = False
    man_path = ctx.job_paths.manifest
    if man_path.is_file():
        manifest_available = True
        try:
            import json

            data = json.loads(man_path.read_text(encoding="utf-8"))
            raw = data.get("artifacts") if isinstance(data, dict) else None
            if isinstance(raw, list):
                artifacts = [a for a in raw if isinstance(a, dict)]
        except (OSError, json.JSONDecodeError):
            pass
    elif ctx.manifest is not None:
        manifest_available = True
        artifacts = [a.model_dump(mode="json") for a in ctx.manifest.artifacts]
    return ArtifactDownloadResponse(
        job_id=ctx.job_id,
        status=ctx.status,
        manifest_available=manifest_available,
        artifacts=artifacts,
    )
