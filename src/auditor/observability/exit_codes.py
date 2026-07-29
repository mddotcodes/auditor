"""Process exit codes for non-server (CLI / batch) mode.

Cloud orchestrators (Fargate, Cloud Run, K8s Jobs) should treat:

- ``0`` — audit completed (artifacts under job root; check findings separately)
- ``1`` — job failed (compile/static/pipeline hard-fail)
- ``2`` — usage / config / input error (do not retry the same payload blindly)
- ``3`` — wall-clock timed out (may retry with higher budget)
- ``4`` — cancelled

Non-zero always means the process did not finish as ``completed``. Orchestrators
that only need success/failure can use ``exit_code == 0``.
"""

from __future__ import annotations

from auditor.contracts.enums import JobStatus

EXIT_OK = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE = 2
EXIT_TIMED_OUT = 3
EXIT_CANCELLED = 4


def exit_code_for_status(status: JobStatus) -> int:
    """Map a terminal (or unexpected) job status to a process exit code."""
    if status is JobStatus.COMPLETED:
        return EXIT_OK
    if status is JobStatus.TIMED_OUT:
        return EXIT_TIMED_OUT
    if status is JobStatus.CANCELLED:
        return EXIT_CANCELLED
    if status is JobStatus.FAILED:
        return EXIT_JOB_FAILED
    # Non-terminal after run() is unexpected — treat as failure.
    return EXIT_JOB_FAILED
