"""Shared mutable context for a single audit job run."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auditor.contracts.enums import JobStage, JobStatus, StageRunStatus, TerminalReason
from auditor.contracts.layout import JobPaths
from auditor.contracts.manifest import ArtifactManifest, StageSummary
from auditor.pipeline.findings import Finding, FindingsDocument
from auditor.pipeline.profiles import AuditProfile
from auditor.security.config import SecurityConfig


def new_job_id() -> str:
    """UUID4 job id (string)."""
    return str(uuid.uuid4())


@dataclass
class JobContext:
    """In-memory state for one job."""

    job_id: str
    job_paths: JobPaths
    profile: AuditProfile
    security: SecurityConfig
    sources: dict[str, str] = field(default_factory=dict)
    gist_url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    status: JobStatus = JobStatus.QUEUED
    stage: JobStage | None = None
    progress: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_code: str | None = None
    error_message: str | None = None
    terminal: TerminalReason | None = None

    cancel_requested: bool = False
    hard_fail: bool = False

    findings: FindingsDocument = field(default_factory=FindingsDocument)
    stage_results: dict[str, StageRunStatus] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    manifest: ArtifactManifest | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            self.touch()

    def set_running(self, stage: JobStage | None = None) -> None:
        with self._lock:
            self.status = JobStatus.RUNNING
            if stage is not None:
                self.stage = stage
            self.touch()

    def set_progress(self, progress: int) -> None:
        with self._lock:
            self.progress = max(0, min(100, progress))
            self.touch()

    def add_findings(self, items: list[Finding]) -> None:
        with self._lock:
            self.findings.extend(items)

    def project_dir(self) -> Path:
        return self.job_paths.project

    def ensure_manifest(self) -> ArtifactManifest:
        if self.manifest is None:
            self.manifest = ArtifactManifest.empty(
                self.job_id,
                created_at=self.created_at,
                status=self.status,
            )
        return self.manifest

    def record_stage(
        self,
        stage: JobStage,
        status: StageRunStatus,
        *,
        message: str | None = None,
        artifact_paths: list[str] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        with self._lock:
            m = self.ensure_manifest()
            # replace existing summary for stage
            others = [s for s in m.stages if s.stage is not stage]
            others.append(
                StageSummary(
                    stage=stage,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at or datetime.now(UTC),
                    message=message,
                    artifact_paths=list(artifact_paths or []),
                )
            )
            # keep pipeline order
            order = list(JobStage)
            others.sort(key=lambda s: order.index(s.stage) if s.stage in order else 99)
            m.stages = others
            m.status = self.status
            m.updated_at = datetime.now(UTC)
            self.stage_results[stage.value] = status
            self.touch()
