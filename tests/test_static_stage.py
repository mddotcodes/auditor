"""Tests for StaticAnalysisStage (mocked tools)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from tests.test_static_parsers import MINIMAL_ADERYN, MINIMAL_SLITHER

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT, JobPaths
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import FindingSeverity
from auditor.pipeline.profiles import STAGE_STATIC, AuditProfile
from auditor.pipeline.stages.static_analysis import StaticAnalysisStage
from auditor.security import CommandResult, CommandTimeoutError, SecurityConfig


@pytest.fixture
def job_ctx(tmp_path: Path) -> JobContext:
    job_id = "static-test-job"
    paths = JobPaths(job_root=tmp_path, job_id=job_id)
    paths.ensure_skeleton()
    # Minimal foundry-ish project so should_run passes.
    (paths.project / "src").mkdir(parents=True, exist_ok=True)
    (paths.project / "src" / "C.sol").write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.0;\ncontract C {}\n",
        encoding="utf-8",
    )
    ctx = JobContext(
        job_id=job_id,
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=60, memory_limit_bytes=None),
    )
    ctx.meta["stage_timeout_seconds"] = 30
    return ctx


def _ok_result(cmd: list[str], *, stdout: bytes = b"") -> CommandResult:
    return CommandResult(
        command=tuple(cmd),
        returncode=0,
        stdout=stdout,
        stderr=b"",
        duration_seconds=0.1,
    )


def test_should_run_requires_project(tmp_path: Path) -> None:
    paths = JobPaths(job_root=tmp_path, job_id="j")
    # No ensure_skeleton — no project dir
    ctx = JobContext(
        job_id="j",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(),
    )
    stage = StaticAnalysisStage()
    ok, reason = stage.should_run(ctx)
    assert ok is False
    assert reason is not None


def test_stage_metadata() -> None:
    stage = StaticAnalysisStage()
    assert stage.name == STAGE_STATIC
    assert stage.job_stage is JobStage.STATIC
    assert stage.optional is True


def test_both_tools_missing_soft_fail(job_ctx: JobContext) -> None:
    bus = EventBus()
    stage = StaticAnalysisStage()

    with patch("auditor.pipeline.stages.static_analysis.shutil.which", return_value=None):
        result = stage.run(job_ctx, bus)

    assert result.hard_fail is False
    assert result.status is StageRunStatus.FAILED
    assert "tools_failed" in (result.message or "")
    findings_file = job_ctx.job_paths.resolve(JOB_LAYOUT.findings)
    assert findings_file.is_file()
    doc = json.loads(findings_file.read_text(encoding="utf-8"))
    assert set(doc["tools_failed"]) == {"slither", "aderyn"}
    assert doc["findings"] == []
    assert "slither" in job_ctx.findings.tools_failed
    assert "aderyn" in job_ctx.findings.tools_failed


def test_slither_ok_aderyn_missing(job_ctx: JobContext) -> None:
    bus = EventBus()
    stage = StaticAnalysisStage()
    slither_path = job_ctx.job_paths.resolve(JOB_LAYOUT.slither_raw)

    def which(name: str) -> str | None:
        return "/usr/bin/slither" if name == "slither" else None

    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> CommandResult:
        assert command[0] == "slither"
        slither_path.parent.mkdir(parents=True, exist_ok=True)
        slither_path.write_text(json.dumps(MINIMAL_SLITHER), encoding="utf-8")
        # Non-zero exit is common when findings exist.
        return CommandResult(
            command=tuple(command),
            returncode=255,
            stdout=b"",
            stderr=b"detectors found",
            duration_seconds=0.2,
        )

    with (
        patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which),
        patch(
            "auditor.pipeline.stages.static_analysis.run_command",
            side_effect=fake_run,
        ),
    ):
        result = stage.run(job_ctx, bus)

    assert result.hard_fail is False
    assert result.status is StageRunStatus.COMPLETED
    assert JOB_LAYOUT.slither_raw in result.artifact_paths
    assert JOB_LAYOUT.findings in result.artifact_paths
    assert any(f.tool == "slither" for f in job_ctx.findings.findings)
    assert "slither" in job_ctx.findings.tools_run
    assert "aderyn" in job_ctx.findings.tools_failed
    assert any(f.severity is FindingSeverity.HIGH for f in job_ctx.findings.findings)

    findings_file = job_ctx.job_paths.resolve(JOB_LAYOUT.findings)
    doc = json.loads(findings_file.read_text(encoding="utf-8"))
    assert len(doc["findings"]) >= 1


def test_both_tools_ok_parallel(job_ctx: JobContext) -> None:
    bus = EventBus()
    stage = StaticAnalysisStage()
    slither_path = job_ctx.job_paths.resolve(JOB_LAYOUT.slither_raw)
    aderyn_path = job_ctx.job_paths.resolve(JOB_LAYOUT.aderyn_raw)

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        tool = command[0]
        if tool == "slither":
            slither_path.write_text(json.dumps(MINIMAL_SLITHER), encoding="utf-8")
        elif tool == "aderyn":
            aderyn_path.write_text(json.dumps(MINIMAL_ADERYN), encoding="utf-8")
        else:
            raise AssertionError(f"unexpected tool {tool}")
        return _ok_result(list(command))

    with (
        patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which),
        patch(
            "auditor.pipeline.stages.static_analysis.run_command",
            side_effect=fake_run,
        ),
    ):
        result = stage.run(job_ctx, bus)

    assert result.status is StageRunStatus.COMPLETED
    assert result.hard_fail is False
    tools = {f.tool for f in job_ctx.findings.findings}
    assert tools == {"slither", "aderyn"}
    assert set(job_ctx.findings.tools_run) == {"slither", "aderyn"}
    assert job_ctx.findings.tools_failed == []
    assert JOB_LAYOUT.aderyn_raw in result.artifact_paths
    assert job_ctx.meta["static"]["finding_count"] >= 1


def test_slither_timeout_soft(job_ctx: JobContext) -> None:
    bus = EventBus()
    stage = StaticAnalysisStage()

    def which(name: str) -> str | None:
        return "/bin/slither" if name == "slither" else None

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        raise CommandTimeoutError(command, 15.0, stdout=b"", stderr=b"hang")

    with (
        patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which),
        patch(
            "auditor.pipeline.stages.static_analysis.run_command",
            side_effect=fake_run,
        ),
    ):
        result = stage.run(job_ctx, bus)

    assert result.hard_fail is False
    assert result.status is StageRunStatus.FAILED
    assert "slither" in job_ctx.findings.tools_failed


def test_dedup_across_identical_findings(job_ctx: JobContext) -> None:
    """If both tools somehow emit identical detector_id+loc+title, dedup keeps one."""
    bus = EventBus()
    stage = StaticAnalysisStage()
    slither_path = job_ctx.job_paths.resolve(JOB_LAYOUT.slither_raw)

    # Craft two identical slither detectors in one file — parser returns both,
    # dedup_findings should collapse them.
    dup = {
        "success": True,
        "error": None,
        "results": {
            "detectors": [
                {
                    "check": "x",
                    "impact": "Low",
                    "confidence": "High",
                    "description": "Same title",
                    "elements": [
                        {
                            "type": "contract",
                            "name": "C",
                            "source_mapping": {
                                "filename_relative": "C.sol",
                                "lines": [1],
                            },
                        }
                    ],
                },
                {
                    "check": "x",
                    "impact": "Low",
                    "confidence": "High",
                    "description": "Same title",
                    "elements": [
                        {
                            "type": "contract",
                            "name": "C",
                            "source_mapping": {
                                "filename_relative": "C.sol",
                                "lines": [1],
                            },
                        }
                    ],
                },
            ]
        },
    }

    def which(name: str) -> str | None:
        return "/bin/slither" if name == "slither" else None

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        slither_path.write_text(json.dumps(dup), encoding="utf-8")
        return _ok_result(list(command))

    with (
        patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which),
        patch(
            "auditor.pipeline.stages.static_analysis.run_command",
            side_effect=fake_run,
        ),
    ):
        stage.run(job_ctx, bus)

    assert len(job_ctx.findings.findings) == 1


def test_timeout_split_passed_to_run_command(job_ctx: JobContext) -> None:
    bus = EventBus()
    stage = StaticAnalysisStage()
    job_ctx.meta["stage_timeout_seconds"] = 40
    seen_timeouts: list[float] = []

    def which(name: str) -> str | None:
        return None  # no tools — still check we don't call run_command

    with patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which):
        stage.run(job_ctx, bus)

    # With tools missing we never call run_command; exercise timeout via mock which.
    def which2(name: str) -> str | None:
        return f"/bin/{name}"

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        seen_timeouts.append(float(kwargs.get("timeout_seconds") or 0))
        # Write empty-ish success so parse doesn't fail hard
        tool = command[0]
        path = job_ctx.job_paths.resolve(
            JOB_LAYOUT.slither_raw if tool == "slither" else JOB_LAYOUT.aderyn_raw
        )
        if tool == "slither":
            path.write_text(
                json.dumps({"success": True, "results": {"detectors": []}}),
                encoding="utf-8",
            )
        else:
            path.write_text(json.dumps({"high_issues": {"issues": []}}), encoding="utf-8")
        return _ok_result(list(command))

    job_ctx2 = job_ctx
    # Fresh findings
    from auditor.pipeline.findings import FindingsDocument

    job_ctx2.findings = FindingsDocument()

    with (
        patch("auditor.pipeline.stages.static_analysis.shutil.which", side_effect=which2),
        patch(
            "auditor.pipeline.stages.static_analysis.run_command",
            side_effect=fake_run,
        ),
    ):
        stage.run(job_ctx2, bus)

    assert seen_timeouts
    # Fair split: 40 / 2 = 20
    assert all(t == 20.0 for t in seen_timeouts)


def test_event_bus_receives_messages(job_ctx: JobContext) -> None:
    bus = MagicMock(spec=EventBus)
    stage = StaticAnalysisStage()
    with patch("auditor.pipeline.stages.static_analysis.shutil.which", return_value=None):
        stage.run(job_ctx, bus)
    assert bus.emit.called
