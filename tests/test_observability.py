"""M7.3 — observability hooks (logs, Prometheus, exit codes)."""

from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auditor.api.app import create_app
from auditor.api.state import AppState
from auditor.contracts.enums import JobStatus
from auditor.contracts.layout import JobPaths
from auditor.observability.exit_codes import (
    EXIT_CANCELLED,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_TIMED_OUT,
    EXIT_USAGE,
    exit_code_for_status,
)
from auditor.observability.logging import (
    JsonFormatter,
    bound_log_context,
    configure_logging,
    get_log_format,
)
from auditor.observability.prometheus import (
    MetricsRegistry,
    metrics_enabled,
    reset_metrics_for_tests,
)
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import AuditProfile
from auditor.pipeline.runner import PipelineRunner, build_default_registry
from auditor.pipeline.store import InMemoryJobStore
from auditor.security.config import SecurityConfig


def test_exit_code_mapping() -> None:
    assert exit_code_for_status(JobStatus.COMPLETED) == EXIT_OK
    assert exit_code_for_status(JobStatus.FAILED) == EXIT_JOB_FAILED
    assert exit_code_for_status(JobStatus.TIMED_OUT) == EXIT_TIMED_OUT
    assert exit_code_for_status(JobStatus.CANCELLED) == EXIT_CANCELLED
    assert exit_code_for_status(JobStatus.RUNNING) == EXIT_JOB_FAILED
    assert exit_code_for_status(JobStatus.QUEUED) == EXIT_JOB_FAILED


def test_cli_reexports_exit_codes() -> None:
    from auditor import cli

    assert cli.EXIT_OK == 0
    assert cli.EXIT_USAGE == 2
    assert cli.EXIT_TIMED_OUT == 3
    assert cli.EXIT_CANCELLED == 4


def test_get_log_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_LOG_FORMAT", raising=False)
    assert get_log_format() == "text"
    monkeypatch.setenv("AUDIT_LOG_FORMAT", "json")
    assert get_log_format() == "json"
    monkeypatch.setenv("AUDIT_LOG_FORMAT", "JSONL")
    assert get_log_format() == "json"


def test_json_formatter_emits_parseable_line() -> None:
    stream = StringIO()
    configure_logging(fmt="json", stream=stream, force=True, level="info")
    log = logging.getLogger("auditor.test.obs")
    with bound_log_context(job_id="j-1", profile="static"):
        log.info("hello world", extra={"stage": "compile"})
    line = stream.getvalue().strip().splitlines()[-1]
    data = json.loads(line)
    assert data["msg"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "auditor.test.obs"
    assert data["job_id"] == "j-1"
    assert data["profile"] == "static"
    assert data["stage"] == "compile"
    assert "ts" in data


def test_json_formatter_standalone() -> None:
    record = logging.LogRecord(
        name="x",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="warn %s",
        args=("me",),
        exc_info=None,
    )
    record.job_id = "abc"
    text = JsonFormatter().format(record)
    data = json.loads(text)
    assert data["msg"] == "warn me"
    assert data["job_id"] == "abc"
    assert data["level"] == "WARNING"


def test_metrics_registry_render() -> None:
    reg = MetricsRegistry()
    reg.counter("auditor_jobs_started_total", "started")
    reg.gauge("auditor_jobs_inflight", "inflight")
    reg.summary("auditor_stage_duration_seconds", "stage secs")
    reg.job_started(profile="static")
    reg.job_started(profile="static")
    reg.job_finished(status="completed", profile="static")
    reg.set_inflight(1)
    reg.stage_duration("compile", 0.25)
    reg.stage_duration("compile", 0.75)
    body = reg.render(version="9.9.9")
    assert "auditor_build_info" in body
    assert 'version="9.9.9"' in body
    assert "auditor_jobs_started_total" in body
    assert 'profile="static"' in body
    assert "auditor_jobs_inflight 1" in body
    assert "auditor_stage_duration_seconds_sum" in body
    assert "auditor_stage_duration_seconds_count" in body
    # sum of 0.25+0.75
    assert "1.0" in body or "1" in body


def test_metrics_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_METRICS_ENABLED", raising=False)
    assert metrics_enabled() is True
    monkeypatch.setenv("AUDIT_METRICS_ENABLED", "false")
    assert metrics_enabled() is False
    monkeypatch.setenv("AUDIT_METRICS_ENABLED", "1")
    assert metrics_enabled() is True


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUDIT_API_TOKEN", raising=False)
    monkeypatch.setenv("AUDIT_METRICS_ENABLED", "true")
    reset_metrics_for_tests()
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    store = InMemoryJobStore()
    bus = EventBus()
    runner = PipelineRunner(
        build_default_registry(),
        store=store,
        bus=bus,
        job_root=job_root,
        security=SecurityConfig(
            timeout_seconds=30,
            memory_limit_bytes=None,
            rlimit_cpu_seconds=None,
        ),
    )
    state = AppState(
        runner=runner,
        executor=__import__(
            "concurrent.futures", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor(max_workers=2),
        max_inflight=2,
        api_token=None,
        job_root=job_root,
    )
    app = create_app(state=state)
    with TestClient(app) as client:
        yield client


def test_metrics_endpoint(api_client: TestClient) -> None:
    r = api_client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    assert "auditor_build_info" in r.text
    assert "auditor_jobs_inflight" in r.text or "auditor_process_start_time" in r.text


def test_metrics_disabled_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_METRICS_ENABLED", "false")
    monkeypatch.delenv("AUDIT_API_TOKEN", raising=False)
    reset_metrics_for_tests()
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    state = AppState(
        runner=PipelineRunner(
            build_default_registry(),
            store=InMemoryJobStore(),
            bus=EventBus(),
            job_root=job_root,
            security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
        ),
        executor=__import__(
            "concurrent.futures", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor(max_workers=1),
        max_inflight=1,
        api_token=None,
        job_root=job_root,
    )
    app = create_app(state=state)
    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "metrics_disabled"


def test_cli_timed_out_exit_code(tmp_path: Path) -> None:
    from auditor.cli import main

    sol = tmp_path / "T.sol"
    sol.write_text("contract T {}", encoding="utf-8")
    job_root = tmp_path / "jobs"
    paths = JobPaths(job_root=job_root, job_id="to-job")
    paths.ensure_skeleton()
    fake_ctx = JobContext(
        job_id="to-job",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
    )
    fake_ctx.status = JobStatus.TIMED_OUT

    mock_runner = MagicMock()
    bus = EventBus()
    mock_runner.bus = bus
    mock_runner.submit.return_value = fake_ctx
    mock_runner.run.return_value = fake_ctx

    with (
        patch("auditor.cli.PipelineRunner", return_value=mock_runner),
        patch("auditor.cli.build_default_registry", return_value=MagicMock()),
    ):
        code = main(["run", str(sol), "--job-root", str(job_root), "--no-llm"])
    assert code == EXIT_TIMED_OUT


def test_cli_cancelled_exit_code(tmp_path: Path) -> None:
    from auditor.cli import main

    sol = tmp_path / "C.sol"
    sol.write_text("contract C {}", encoding="utf-8")
    job_root = tmp_path / "jobs"
    paths = JobPaths(job_root=job_root, job_id="cancel-job")
    paths.ensure_skeleton()
    fake_ctx = JobContext(
        job_id="cancel-job",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
    )
    fake_ctx.status = JobStatus.CANCELLED

    mock_runner = MagicMock()
    bus = EventBus()
    mock_runner.bus = bus
    mock_runner.submit.return_value = fake_ctx
    mock_runner.run.return_value = fake_ctx

    with (
        patch("auditor.cli.PipelineRunner", return_value=mock_runner),
        patch("auditor.cli.build_default_registry", return_value=MagicMock()),
    ):
        code = main(["run", str(sol), "--job-root", str(job_root), "--no-llm"])
    assert code == EXIT_CANCELLED


def test_usage_exit_code_unchanged() -> None:
    from auditor.cli import main

    assert main(["run", "/nonexistent/Contract.sol"]) == EXIT_USAGE
