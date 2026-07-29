"""Observability hooks for cloud orchestrators (M7.3).

- Structured JSON logs (stdout)
- Prometheus text metrics (optional HTTP scrape)
- Process exit codes for non-server / batch mode
"""

from __future__ import annotations

from auditor.observability.exit_codes import (
    EXIT_CANCELLED,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_TIMED_OUT,
    EXIT_USAGE,
    exit_code_for_status,
)
from auditor.observability.logging import (
    bound_log_context,
    configure_logging,
    get_log_format,
    log_context,
)
from auditor.observability.prometheus import (
    MetricsRegistry,
    get_metrics,
    metrics_enabled,
    reset_metrics_for_tests,
)

__all__ = [
    "EXIT_CANCELLED",
    "EXIT_JOB_FAILED",
    "EXIT_OK",
    "EXIT_TIMED_OUT",
    "EXIT_USAGE",
    "MetricsRegistry",
    "bound_log_context",
    "configure_logging",
    "exit_code_for_status",
    "get_log_format",
    "get_metrics",
    "log_context",
    "metrics_enabled",
    "reset_metrics_for_tests",
]
