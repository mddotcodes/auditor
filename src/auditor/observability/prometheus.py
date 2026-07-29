"""Minimal Prometheus text exposition (no third-party client).

Enable scraping with ``AUDIT_METRICS_ENABLED=true`` (default: enabled when the
HTTP server is up — the endpoint always responds; set ``false`` to return 404).

Counters / gauges are process-local. For multi-replica Cloud Run / Fargate,
scrape each task and aggregate in Prometheus / Cloud Monitoring.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


def metrics_enabled() -> bool:
    """Whether ``GET /metrics`` should expose data (default true)."""
    raw = (os.environ.get("AUDIT_METRICS_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


@dataclass
class MetricsRegistry:
    """Thread-safe in-process metrics for orchestrator scrapes."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    # Simple summary: sum + count per label set (for durations).
    _summary_sum: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _summary_count: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=dict
    )
    _help: dict[str, str] = field(default_factory=dict)
    _type: dict[str, str] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.time)

    def _key(
        self, name: str, labels: dict[str, str] | None
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        items = tuple(sorted((labels or {}).items()))
        return name, items

    def counter(self, name: str, help_text: str = "") -> None:
        self._help.setdefault(name, help_text)
        self._type[name] = "counter"

    def gauge(self, name: str, help_text: str = "") -> None:
        self._help.setdefault(name, help_text)
        self._type[name] = "gauge"

    def summary(self, name: str, help_text: str = "") -> None:
        self._help.setdefault(name, help_text)
        self._type[name] = "summary"

    def inc(
        self,
        name: str,
        value: float = 1.0,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._type.setdefault(name, "counter")
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._type.setdefault(name, "gauge")
            self._gauges[key] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a summary observation (e.g. stage duration seconds)."""
        key = self._key(name, labels)
        with self._lock:
            self._type.setdefault(name, "summary")
            self._summary_sum[key] = self._summary_sum.get(key, 0.0) + value
            self._summary_count[key] = self._summary_count.get(key, 0.0) + 1.0

    def job_started(self, *, profile: str = "unknown") -> None:
        self.inc("auditor_jobs_started_total", labels={"profile": profile})

    def job_finished(self, *, status: str, profile: str = "unknown") -> None:
        self.inc(
            "auditor_jobs_finished_total",
            labels={"status": status, "profile": profile},
        )

    def set_inflight(self, n: int) -> None:
        self.set_gauge("auditor_jobs_inflight", float(n))

    def stage_duration(self, stage: str, seconds: float) -> None:
        self.observe(
            "auditor_stage_duration_seconds",
            seconds,
            labels={"stage": stage},
        )

    def render(self, *, version: str = "0.0.0") -> str:
        """Prometheus text exposition format (0.0.4)."""
        lines: list[str] = []
        # Build-time / process info
        lines.append("# HELP auditor_build_info Auditor engine build metadata")
        lines.append("# TYPE auditor_build_info gauge")
        lines.append(f'auditor_build_info{{version="{_escape_label_value(version)}"}} 1')
        lines.append("# HELP auditor_process_start_time_seconds Process start time")
        lines.append("# TYPE auditor_process_start_time_seconds gauge")
        lines.append(f"auditor_process_start_time_seconds {self._started_at:.3f}")

        with self._lock:
            by_name: dict[str, list[tuple[tuple[tuple[str, str], ...], str, float]]] = defaultdict(
                list
            )
            for (name, labels), value in self._counters.items():
                by_name[name].append((labels, "counter", value))
            for (name, labels), value in self._gauges.items():
                by_name[name].append((labels, "gauge", value))
            for (name, labels), value in self._summary_sum.items():
                by_name[name].append((labels, "summary_sum", value))
            for (name, labels), value in self._summary_count.items():
                by_name[name].append((labels, "summary_count", value))

            names = sorted(set(by_name) | set(self._help) | set(self._type))
            for name in names:
                help_text = self._help.get(name, name)
                mtype = self._type.get(name, "untyped")
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {mtype}")
                entries = by_name.get(name, [])
                if mtype == "summary":
                    # group by labels
                    sums: dict[tuple[tuple[str, str], ...], float] = {}
                    counts: dict[tuple[tuple[str, str], ...], float] = {}
                    for labels, kind, value in entries:
                        if kind == "summary_sum":
                            sums[labels] = value
                        elif kind == "summary_count":
                            counts[labels] = value
                    all_labels = sorted(set(sums) | set(counts))
                    for labels in all_labels:
                        label_dict = dict(labels)
                        ls = _format_labels(label_dict)
                        lines.append(f"{name}_sum{ls} {sums.get(labels, 0.0)}")
                        lines.append(f"{name}_count{ls} {int(counts.get(labels, 0.0))}")
                else:
                    for labels, _kind, value in sorted(entries, key=lambda x: x[0]):
                        label_dict = dict(labels)
                        ls = _format_labels(label_dict)
                        # Counters/gauges: integers when whole numbers
                        if float(value).is_integer():
                            lines.append(f"{name}{ls} {int(value)}")
                        else:
                            lines.append(f"{name}{ls} {value}")

        return "\n".join(lines) + "\n"


_GLOBAL: MetricsRegistry | None = None
_GLOBAL_LOCK = threading.Lock()


def get_metrics() -> MetricsRegistry:
    """Process-wide metrics registry (lazy singleton)."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            reg = MetricsRegistry()
            reg.counter(
                "auditor_jobs_started_total",
                "Audit jobs accepted / started",
            )
            reg.counter(
                "auditor_jobs_finished_total",
                "Audit jobs finished by terminal status",
            )
            reg.gauge(
                "auditor_jobs_inflight",
                "Currently running audit jobs",
            )
            reg.summary(
                "auditor_stage_duration_seconds",
                "Pipeline stage wall time in seconds",
            )
            _GLOBAL = reg
        return _GLOBAL


def reset_metrics_for_tests() -> MetricsRegistry:
    """Replace the global registry (tests only)."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        reg = MetricsRegistry()
        reg.counter("auditor_jobs_started_total", "Audit jobs accepted / started")
        reg.counter(
            "auditor_jobs_finished_total",
            "Audit jobs finished by terminal status",
        )
        reg.gauge("auditor_jobs_inflight", "Currently running audit jobs")
        reg.summary(
            "auditor_stage_duration_seconds",
            "Pipeline stage wall time in seconds",
        )
        _GLOBAL = reg
        return reg
