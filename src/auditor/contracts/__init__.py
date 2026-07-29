"""Public I/O contracts: jobs, events, and artifact manifests (schema version 1)."""

from __future__ import annotations

from auditor.contracts.enums import (
    ArtifactKind,
    EventLevel,
    JobStage,
    JobStatus,
    SchemaVersion,
    StageRunStatus,
    TerminalReason,
)
from auditor.contracts.events import JobEvent
from auditor.contracts.jobs import (
    ArtifactDownloadResponse,
    AuditOptions,
    AuditRequest,
    AuditSubmitResponse,
    ErrorBody,
    JobStatusResponse,
)
from auditor.contracts.layout import JOB_LAYOUT, JobPaths
from auditor.contracts.manifest import (
    ArtifactEntry,
    ArtifactManifest,
    CompilerSettings,
    Fingerprint,
    FingerprintContract,
    StageSummary,
)

__all__ = [
    "JOB_LAYOUT",
    "ArtifactDownloadResponse",
    "ArtifactEntry",
    "ArtifactKind",
    "ArtifactManifest",
    "AuditOptions",
    "AuditRequest",
    "AuditSubmitResponse",
    "CompilerSettings",
    "ErrorBody",
    "EventLevel",
    "Fingerprint",
    "FingerprintContract",
    "JobEvent",
    "JobPaths",
    "JobStage",
    "JobStatus",
    "JobStatusResponse",
    "SchemaVersion",
    "StageRunStatus",
    "StageSummary",
    "TerminalReason",
]
