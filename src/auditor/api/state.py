"""Process-wide API state: runner, executor, concurrency gate."""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from auditor.observability.prometheus import get_metrics
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.runner import PipelineRunner, build_default_registry
from auditor.pipeline.store import InMemoryJobStore
from auditor.security.config import SecurityConfig


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass
class AppState:
    """Shared by REST + WebSocket handlers."""

    runner: PipelineRunner
    executor: ThreadPoolExecutor
    max_inflight: int = 2
    api_token: str | None = None
    job_root: Path = field(default_factory=lambda: Path("/work/jobs"))
    _inflight: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _futures: dict[str, Future[JobContext]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AppState:
        job_root = Path(os.environ.get("AUDIT_JOB_ROOT", "/work/jobs"))
        job_root.mkdir(parents=True, exist_ok=True)
        store = InMemoryJobStore()
        bus = EventBus(max_per_job=_env_int("AUDIT_EVENT_BUFFER_SIZE", 2000))
        runner = PipelineRunner(
            build_default_registry(),
            store=store,
            bus=bus,
            job_root=job_root,
            security=SecurityConfig.from_env(),
        )
        max_inflight = max(1, _env_int("AUDIT_MAX_INFLIGHT_JOBS", 2))
        workers = max(1, _env_int("AUDIT_WORKER_THREADS", max_inflight))
        token = os.environ.get("AUDIT_API_TOKEN") or None
        if token is not None and token.strip() == "":
            token = None
        return cls(
            runner=runner,
            executor=ThreadPoolExecutor(max_workers=workers, thread_name_prefix="audit-job"),
            max_inflight=max_inflight,
            api_token=token,
            job_root=job_root,
        )

    def try_acquire_slot(self) -> bool:
        with self._lock:
            if self._inflight >= self.max_inflight:
                return False
            self._inflight += 1
            get_metrics().set_inflight(self._inflight)
            return True

    def release_slot(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            get_metrics().set_inflight(self._inflight)

    def submit_job(self, ctx: JobContext) -> Future[JobContext]:
        """Run pipeline in background; releases concurrency slot when done."""
        profile = ctx.profile.value
        get_metrics().job_started(profile=profile)

        def _run() -> JobContext:
            try:
                result = self.runner.run(ctx)
                status = result.status.value if result.status is not None else "failed"
                get_metrics().job_finished(status=status, profile=profile)
                return result
            except Exception:
                get_metrics().job_finished(status="failed", profile=profile)
                raise
            finally:
                self.release_slot()

        fut = self.executor.submit(_run)
        with self._lock:
            self._futures[ctx.job_id] = fut
        return fut

    def shutdown(self, *, wait: bool = True, cancel: bool = False) -> None:
        if cancel:
            for ctx_id in list(self.runner.store.list_ids()):
                ctx = self.runner.store.get(ctx_id)
                if ctx is not None:
                    ctx.request_cancel()
        self.executor.shutdown(wait=wait, cancel_futures=not wait)
