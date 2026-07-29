"""Acceptance: wall-clock timeout kills a hung process tree."""

from __future__ import annotations

import sys
import time

import pytest

from auditor.security import CommandTimeoutError, SecurityConfig, run_command


def test_run_command_success() -> None:
    result = run_command(
        [sys.executable, "-c", "print('ok')"],
        timeout_seconds=5,
        config=SecurityConfig(
            timeout_seconds=5,
            memory_limit_bytes=None,
            rlimit_cpu_seconds=None,
        ),
    )
    assert result.ok
    assert b"ok" in result.stdout


def test_timeout_kills_sleep() -> None:
    """A sleep longer than the budget must raise and return quickly."""
    cfg = SecurityConfig(
        timeout_seconds=30,
        term_grace_seconds=0.5,
        memory_limit_bytes=None,
        rlimit_cpu_seconds=None,
    )
    started = time.monotonic()
    with pytest.raises(CommandTimeoutError) as exc_info:
        run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout_seconds=1,
            config=cfg,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"kill took too long: {elapsed:.2f}s"
    assert exc_info.value.timeout_seconds == 1


def test_timeout_kills_process_group_children() -> None:
    """Child-of-child sleeps must die with the group, not leak past timeout."""
    # Spawn a subprocess that itself spawns sleep; killing only the middle
    # process would leak the grandchild without process-group kill.
    code = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
time.sleep(120)
"""
    cfg = SecurityConfig(
        timeout_seconds=30,
        term_grace_seconds=0.5,
        memory_limit_bytes=None,
        rlimit_cpu_seconds=None,
    )
    started = time.monotonic()
    with pytest.raises(CommandTimeoutError):
        run_command(
            [sys.executable, "-c", code],
            timeout_seconds=1,
            config=cfg,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"process group kill took too long: {elapsed:.2f}s"


def test_nonzero_exit_not_timeout() -> None:
    result = run_command(
        [sys.executable, "-c", "raise SystemExit(17)"],
        timeout_seconds=5,
        config=SecurityConfig(
            timeout_seconds=5,
            memory_limit_bytes=None,
            rlimit_cpu_seconds=None,
        ),
    )
    assert result.returncode == 17
    assert not result.ok
