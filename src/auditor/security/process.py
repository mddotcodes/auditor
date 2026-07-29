"""Process-group aware command execution with hard timeouts.

Untrusted ``forge`` / Slither / LLM-generated tests can hang or fork. Every
external tool invocation should go through :func:`run_command` so that:

1. Children run in a new session/process group.
2. Wall-clock timeout sends SIGTERM to the **entire** group, then SIGKILL.
3. Optional address-space / CPU rlimits apply to the child group leader.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO

from auditor.security.config import SecurityConfig


class CommandTimeoutError(TimeoutError):
    """Raised when a command exceeds its wall-clock budget and is killed."""

    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.command = list(command)
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr
        cmd_display = " ".join(self.command)
        super().__init__(
            f"Command timed out after {timeout_seconds:g}s and was killed: {cmd_display}"
        )


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Completed process result (exited before timeout)."""

    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _signal_process_group(pgid: int, sig: signal.Signals | int) -> bool:
    """Send ``sig`` to a process group. Returns False if the group is gone."""
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can raise EPERM for dying/zombie groups; fall back to the leader.
        try:
            os.kill(pgid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def _process_group_alive(pgid: int) -> bool:
    """Best-effort check whether any process in the group remains."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            os.kill(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Group may be zombies awaiting wait(2); treat as still present.
            return True


def kill_process_group(pgid: int, *, grace_seconds: float = 2.0) -> None:
    """Terminate a process group, escalating to SIGKILL after ``grace_seconds``.

    ``pgid`` is typically the child PID when the child was started with a new
    session (``start_new_session=True`` / ``setsid``), so pgid == pid.
    """
    if pgid <= 0:
        msg = f"pgid must be positive, got {pgid}"
        raise ValueError(msg)

    if not _signal_process_group(pgid, signal.SIGTERM):
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _process_group_alive(pgid):
            return
        time.sleep(0.05)

    _signal_process_group(pgid, signal.SIGKILL)

    # Brief wait so callers can reap without racing the kernel.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _process_group_alive(pgid):
            return
        time.sleep(0.05)


def _build_preexec(
    *,
    memory_limit_bytes: int | None,
    rlimit_cpu_seconds: int | None,
) -> Callable[[], None] | None:
    """Return a preexec_fn that applies rlimits in the child before exec.

    Note: ``start_new_session=True`` already creates a new session; we still use
    it on Popen and only set rlimits here (no second setsid).
    """
    if memory_limit_bytes is None and rlimit_cpu_seconds is None:
        return None

    def _preexec() -> None:
        # Import inside child-facing function so tests can mock resource if needed.
        import resource

        if memory_limit_bytes is not None and memory_limit_bytes > 0:
            # RLIMIT_AS: virtual address space (best-effort; platform-dependent).
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_limit_bytes, memory_limit_bytes),
            )
        if rlimit_cpu_seconds is not None and rlimit_cpu_seconds > 0:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (rlimit_cpu_seconds, rlimit_cpu_seconds),
            )

    return _preexec


def _reap(proc: subprocess.Popen[bytes], *, wait_seconds: float) -> tuple[bytes, bytes]:
    """Collect stdout/stderr and ensure the process is reaped."""
    try:
        stdout, stderr = proc.communicate(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(proc.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    return stdout or b"", stderr or b""


def run_command(
    command: Sequence[str],
    *,
    timeout_seconds: float | None = None,
    config: SecurityConfig | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | IO[bytes] | None = None,
    text_env_only: bool = True,
) -> CommandResult:
    """Run ``command`` in a new process group with a hard wall-clock timeout.

    Parameters
    ----------
    command:
        Executable and arguments (not a shell string). Shell is never used.
    timeout_seconds:
        Override wall-clock timeout; defaults to ``config.timeout_seconds``.
    config:
        Security defaults; loaded from the environment when omitted.
    cwd:
        Working directory for the child.
    env:
        Full environment mapping. When ``None``, inherits the current env.
    stdin:
        Passed to ``subprocess.Popen``.
    text_env_only:
        Reserved for future sanitization hooks; currently unused.
    """
    del text_env_only  # placeholder for future env scrubbing
    cfg = config if config is not None else SecurityConfig.from_env()
    budget = float(cfg.timeout_seconds if timeout_seconds is None else timeout_seconds)
    if budget <= 0:
        msg = "timeout_seconds must be positive"
        raise ValueError(msg)

    if not command:
        msg = "command must not be empty"
        raise ValueError(msg)

    cmd_list = [str(part) for part in command]
    preexec = _build_preexec(
        memory_limit_bytes=cfg.memory_limit_bytes,
        rlimit_cpu_seconds=cfg.rlimit_cpu_seconds,
    )

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd_list,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=preexec,
    )

    try:
        try:
            stdout, stderr = proc.communicate(timeout=budget)
        except subprocess.TimeoutExpired:
            kill_process_group(proc.pid, grace_seconds=cfg.term_grace_seconds)
            stdout, stderr = _reap(
                proc,
                wait_seconds=max(1.0, cfg.term_grace_seconds + 1.0),
            )
            raise CommandTimeoutError(
                cmd_list,
                budget,
                stdout=stdout,
                stderr=stderr,
            ) from None
    finally:
        # Ensure pipes are closed if communicate did not finish cleanly.
        if proc.stdout is not None and not proc.stdout.closed:
            proc.stdout.close()
        if proc.stderr is not None and not proc.stderr.closed:
            proc.stderr.close()

    duration = time.monotonic() - started
    return CommandResult(
        command=tuple(cmd_list),
        returncode=int(proc.returncode if proc.returncode is not None else -1),
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )
