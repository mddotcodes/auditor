"""Job store abstraction (in-memory v1)."""

from __future__ import annotations

import threading
from typing import Protocol

from auditor.pipeline.context import JobContext


class JobStore(Protocol):
    def put(self, ctx: JobContext) -> None: ...
    def get(self, job_id: str) -> JobContext | None: ...
    def list_ids(self) -> list[str]: ...
    def delete(self, job_id: str) -> None: ...


class InMemoryJobStore:
    """Process-local job map. Concurrent jobs must use distinct workdirs."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobContext] = {}
        self._lock = threading.RLock()

    def put(self, ctx: JobContext) -> None:
        with self._lock:
            self._jobs[ctx.job_id] = ctx

    def get(self, job_id: str) -> JobContext | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
