"""Artifact manifest models (``artifacts/manifest.json``)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auditor.contracts.enums import (
    ArtifactKind,
    JobStage,
    JobStatus,
    SchemaVersion,
    StageRunStatus,
)


class CompilerSettings(BaseModel):
    """Solc / Foundry settings used for the build fingerprint."""

    model_config = ConfigDict(extra="allow")

    solc_version: str | None = None
    optimizer_enabled: bool | None = None
    optimizer_runs: int | None = None
    via_ir: bool | None = None
    evm_version: str | None = None
    remappings: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class FingerprintContract(BaseModel):
    """Per-contract bytecode hashes for exact-match verification."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source_path: str
    creation_bytecode_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    runtime_bytecode_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    metadata_hash: str | None = Field(
        default=None,
        description="CBOR metadata hash from runtime bytecode suffix when present.",
    )


class Fingerprint(BaseModel):
    """Compilation fingerprint artifact (also written as fingerprint.json)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = SchemaVersion.V1
    compiler: CompilerSettings = Field(default_factory=CompilerSettings)
    contracts: list[FingerprintContract] = Field(default_factory=list)
    sources_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Hash over canonical source snapshot used for the build.",
    )


class StageSummary(BaseModel):
    """One pipeline stage record inside the manifest."""

    model_config = ConfigDict(extra="forbid")

    stage: JobStage
    status: StageRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None
    artifact_paths: list[str] = Field(
        default_factory=list,
        description="Paths relative to the job base that this stage produced.",
    )


class ArtifactEntry(BaseModel):
    """One content-addressed file in the job artifact tree."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Path relative to the job base.")
    kind: ArtifactKind = ArtifactKind.OTHER
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(..., ge=0)
    content_type: str = Field(default="application/octet-stream")
    optional: bool = Field(
        default=False,
        description="If true, stage may skip producing this file without failing the job.",
    )
    stage: JobStage | None = None

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if not value or value.startswith("/") or ".." in value.split("/"):
            msg = f"Invalid artifact path: {value!r}"
            raise ValueError(msg)
        return value


class ArtifactManifest(BaseModel):
    """Root document written to ``artifacts/manifest.json``.

    A finished job (any terminal status) should always leave a valid manifest,
    even when stages were skipped or failed.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = SchemaVersion.V1
    job_id: str
    created_at: datetime
    updated_at: datetime
    status: JobStatus
    layout_version: SchemaVersion = SchemaVersion.V1
    stages: list[StageSummary] = Field(default_factory=list)
    artifacts: list[ArtifactEntry] = Field(default_factory=list)
    fingerprint: Fingerprint | None = None
    notes: str | None = None

    @classmethod
    def empty(
        cls,
        job_id: str,
        *,
        created_at: datetime,
        status: JobStatus = JobStatus.QUEUED,
    ) -> ArtifactManifest:
        """Bootstrap manifest for a new job (valid even before stages run)."""
        return cls(
            job_id=job_id,
            created_at=created_at,
            updated_at=created_at,
            status=status,
            stages=[
                StageSummary(stage=stage, status=StageRunStatus.PENDING)
                for stage in JobStage
            ],
            artifacts=[],
        )

    def add_file(
        self,
        path: Path,
        *,
        relative_path: str,
        kind: ArtifactKind,
        stage: JobStage | None = None,
        content_type: str = "application/octet-stream",
        optional: bool = False,
    ) -> ArtifactEntry:
        """Hash ``path`` and append an :class:`ArtifactEntry`."""
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entry = ArtifactEntry(
            path=relative_path,
            kind=kind,
            sha256=digest,
            size_bytes=len(data),
            content_type=content_type,
            optional=optional,
            stage=stage,
        )
        self.artifacts.append(entry)
        return entry
