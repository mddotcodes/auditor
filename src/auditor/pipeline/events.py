"""In-process event bus for job progress (feeds WS later)."""

from __future__ import annotations

import contextlib
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from auditor.contracts.enums import EventLevel, JobStage, TerminalReason
from auditor.contracts.events import JobEvent


class EventBus:
    """Thread-safe per-job event buffer + optional subscribers."""

    def __init__(self, *, max_per_job: int = 2000) -> None:
        self._max = max_per_job
        self._events: dict[str, deque[JobEvent]] = defaultdict(lambda: deque(maxlen=self._max))
        self._seq: dict[str, int] = defaultdict(int)
        self._subs: dict[str, list[Callable[[JobEvent], None]]] = defaultdict(list)
        self._lock = threading.RLock()

    def emit(
        self,
        job_id: str,
        message: str,
        *,
        stage: JobStage | None = None,
        level: EventLevel = EventLevel.INFO,
        progress: int | None = None,
        data: dict[str, Any] | None = None,
        terminal: TerminalReason | None = None,
    ) -> JobEvent:
        with self._lock:
            seq = self._seq[job_id]
            self._seq[job_id] = seq + 1
            event = JobEvent(
                job_id=job_id,
                seq=seq,
                ts=datetime.now(UTC),
                stage=stage,
                level=level,
                message=message,
                progress=progress,
                data=dict(data or {}),
                terminal=terminal,
            )
            self._events[job_id].append(event)
            listeners = list(self._subs.get(job_id, []))
        for cb in listeners:
            with contextlib.suppress(Exception):
                cb(event)
        return event

    def history(self, job_id: str) -> list[JobEvent]:
        with self._lock:
            return list(self._events.get(job_id, ()))

    def subscribe(self, job_id: str, callback: Callable[[JobEvent], None]) -> None:
        with self._lock:
            self._subs[job_id].append(callback)

    def unsubscribe(self, job_id: str, callback: Callable[[JobEvent], None]) -> None:
        with self._lock:
            subs = self._subs.get(job_id, [])
            if callback in subs:
                subs.remove(callback)
