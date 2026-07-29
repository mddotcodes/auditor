"""Unit tests for pydantic contract models and job layout."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditor.contracts import (
    JOB_LAYOUT,
    ArtifactKind,
    ArtifactManifest,
    AuditRequest,
    AuditSubmitResponse,
    EventLevel,
    JobEvent,
    JobPaths,
    JobStage,
    JobStatus,
    JobStatusResponse,
    TerminalReason,
)


def test_audit_request_sources_ok() -> None:
    req = AuditRequest(sources={"src/A.sol": "pragma solidity ^0.8.20;"})
    assert req.options.enable_llm_tests is True


def test_audit_request_rejects_path_escape() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(sources={"../etc/passwd": "x"})


def test_audit_request_requires_xor_input() -> None:
    with pytest.raises(ValidationError):
        AuditRequest()
    with pytest.raises(ValidationError):
        AuditRequest(
            sources={"src/A.sol": "x"},
            gist_url="https://gist.github.com/example/1",
        )


def test_job_event_terminal() -> None:
    event = JobEvent(
        job_id="j1",
        seq=9,
        ts=datetime(2026, 7, 29, 12, 0, 51, tzinfo=UTC),
        stage=JobStage.FINALIZE,
        level=EventLevel.INFO,
        message="done",
        progress=100,
        terminal=TerminalReason.COMPLETED,
    )
    assert event.is_terminal()
    payload = event.model_dump(mode="json")
    assert payload["schema_version"] == "1"
    assert payload["terminal"] == "completed"


def test_job_status_response_roundtrip() -> None:
    body = JobStatusResponse(
        job_id="j1",
        status=JobStatus.RUNNING,
        stage=JobStage.COMPILE,
        progress=20,
        created_at=datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 29, 12, 0, 5, tzinfo=UTC),
    )
    again = JobStatusResponse.model_validate_json(body.model_dump_json())
    assert again.status is JobStatus.RUNNING


def test_audit_submit_response() -> None:
    resp = AuditSubmitResponse(job_id="j1", status=JobStatus.QUEUED)
    assert resp.model_dump()["status"] == "queued"


def test_manifest_empty_has_all_stages() -> None:
    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    manifest = ArtifactManifest.empty("j1", created_at=now)
    assert manifest.status is JobStatus.QUEUED
    assert [s.stage for s in manifest.stages] == list(JobStage)
    assert all(s.status.value == "pending" for s in manifest.stages)


def test_manifest_add_file(tmp_path: Path) -> None:
    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    manifest = ArtifactManifest.empty("j1", created_at=now, status=JobStatus.FAILED)
    f = tmp_path / "forge-build.log"
    f.write_text("error: oops\n", encoding="utf-8")
    entry = manifest.add_file(
        f,
        relative_path=JOB_LAYOUT.forge_build_log,
        kind=ArtifactKind.FORGE_BUILD_LOG,
        stage=JobStage.COMPILE,
        content_type="text/plain",
    )
    assert entry.size_bytes > 0
    assert len(entry.sha256) == 64
    assert manifest.artifacts[0].path == "artifacts/compile/forge-build.log"


def test_job_paths_refuse_escape(tmp_path: Path) -> None:
    paths = JobPaths(job_root=tmp_path, job_id="abc")
    with pytest.raises(ValueError, match="unsafe"):
        paths.resolve("../outside")
    paths.ensure_skeleton()
    assert paths.manifest.parent.is_dir()
    assert paths.project.is_dir()


def test_job_status_terminal_property() -> None:
    assert JobStatus.COMPLETED.is_terminal
    assert not JobStatus.RUNNING.is_terminal
