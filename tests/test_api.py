"""REST + WebSocket API tests (pipeline stages mocked for speed)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auditor.api.app import create_app
from auditor.api.state import AppState
from auditor.contracts.enums import JobStage, JobStatus, TerminalReason
from auditor.contracts.layout import JobPaths
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import AuditProfile
from auditor.pipeline.runner import PipelineRunner, build_default_registry
from auditor.pipeline.store import InMemoryJobStore
from auditor.security.config import SecurityConfig


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppState:
    monkeypatch.delenv("AUDIT_API_TOKEN", raising=False)
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
    return state


@pytest.fixture
def client(api_env: AppState) -> Any:
    app = create_app(state=api_env)
    with TestClient(app) as c:
        yield c


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_submit_and_poll_mocked(client: TestClient, api_env: AppState) -> None:
    def fake_run(self: PipelineRunner, ctx: JobContext) -> JobContext:
        ctx.set_running(JobStage.COMPILE)
        self.bus.emit(ctx.job_id, "compiling", stage=JobStage.COMPILE, progress=20)
        time.sleep(0.05)
        ctx.status = JobStatus.COMPLETED
        ctx.terminal = TerminalReason.COMPLETED
        ctx.progress = 100
        ctx.stage = JobStage.FINALIZE
        self.bus.emit(
            ctx.job_id,
            "done",
            stage=JobStage.FINALIZE,
            progress=100,
            terminal=TerminalReason.COMPLETED,
        )
        self.store.put(ctx)
        return ctx

    with patch.object(PipelineRunner, "run", fake_run):
        src = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract A {}\n"
        r = client.post(
            "/v1/audit",
            json={
                "sources": {"src/A.sol": src},
                "options": {"enable_llm_tests": False},
            },
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        assert r.json()["status"] == "queued"

        # poll until done
        status = None
        for _ in range(50):
            g = client.get(f"/v1/jobs/{job_id}")
            assert g.status_code == 200
            status = g.json()["status"]
            if status in {"completed", "failed", "timed_out", "cancelled"}:
                break
            time.sleep(0.05)
        assert status == "completed"

        art = client.get(f"/v1/jobs/{job_id}/artifacts")
        assert art.status_code == 200
        assert art.json()["job_id"] == job_id


def test_unknown_job_404(client: TestClient) -> None:
    r = client.get("/v1/jobs/does-not-exist")
    assert r.status_code == 404


def test_auth_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_API_TOKEN", "secret")
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    store = InMemoryJobStore()
    runner = PipelineRunner(
        build_default_registry(),
        store=store,
        bus=EventBus(),
        job_root=job_root,
        security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
    )
    from concurrent.futures import ThreadPoolExecutor

    state = AppState(
        runner=runner,
        executor=ThreadPoolExecutor(max_workers=1),
        max_inflight=1,
        api_token="secret",
        job_root=job_root,
    )
    app = create_app(state=state)
    with TestClient(app) as c:
        r = c.post("/v1/audit", json={"sources": {"src/A.sol": "contract A {}"}})
        assert r.status_code == 401
        r2 = c.post(
            "/v1/audit",
            json={"sources": {"src/A.sol": "contract A {}"}},
            headers={"X-API-Token": "secret"},
        )
        # may 202 or 400 depending on validation — not 401
        assert r2.status_code != 401


def test_concurrency_limit(client: TestClient, api_env: AppState) -> None:
    api_env.max_inflight = 1
    gate = threading.Event()

    def blocking_run(self: PipelineRunner, ctx: JobContext) -> JobContext:
        ctx.set_running(JobStage.COMPILE)
        self.store.put(ctx)
        gate.wait(timeout=5)
        ctx.status = JobStatus.COMPLETED
        ctx.terminal = TerminalReason.COMPLETED
        self.store.put(ctx)
        return ctx

    with patch.object(PipelineRunner, "run", blocking_run):
        body = {
            "sources": {"src/A.sol": "pragma solidity ^0.8.20; contract A {}"},
            "options": {"enable_llm_tests": False},
        }
        r1 = client.post("/v1/audit", json=body)
        assert r1.status_code == 202
        r2 = client.post("/v1/audit", json=body)
        assert r2.status_code == 429
        gate.set()


def test_websocket_replay_and_live(client: TestClient, api_env: AppState) -> None:
    job_id = "ws-job-1"
    paths = JobPaths(job_root=api_env.job_root, job_id=job_id)
    paths.ensure_skeleton()
    ctx = JobContext(
        job_id=job_id,
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
    )
    ctx.status = JobStatus.RUNNING
    ctx.stage = JobStage.COMPILE
    api_env.runner.store.put(ctx)
    api_env.runner.bus.emit(
        job_id,
        "already happened",
        stage=JobStage.MATERIALIZE,
        progress=5,
    )

    def produce() -> None:
        time.sleep(0.1)
        api_env.runner.bus.emit(
            job_id,
            "live event",
            stage=JobStage.COMPILE,
            progress=50,
        )
        time.sleep(0.05)
        ctx.status = JobStatus.COMPLETED
        ctx.terminal = TerminalReason.COMPLETED
        api_env.runner.store.put(ctx)
        api_env.runner.bus.emit(
            job_id,
            "done",
            stage=JobStage.FINALIZE,
            progress=100,
            terminal=TerminalReason.COMPLETED,
        )

    threading.Thread(target=produce, daemon=True).start()

    with client.websocket_connect(f"/v1/ws/jobs/{job_id}") as ws:
        messages = []
        for _ in range(10):
            data = ws.receive_json()
            if data.get("type") == "heartbeat":
                continue
            messages.append(data)
            if data.get("terminal") == "completed":
                break
        assert any(m.get("message") == "already happened" for m in messages)
        assert any(m.get("message") == "live event" for m in messages)
        assert messages[-1].get("terminal") == "completed"


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.post(
        "/v1/metrics",
        json={
            "sources": {
                "src/A.sol": "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n"
                "contract A { function f() external { if (true) {} } }\n"
            }
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["file_count"] >= 1
    assert "tools_available" in body
