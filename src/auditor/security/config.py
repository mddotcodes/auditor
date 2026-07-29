"""Security and resource defaults for job execution.

These settings are enforced by the orchestration process *inside* the container
where possible (wall-clock timeout, process-group kill, optional rlimits).
CPU / memory / network / read-only root are primarily enforced by the **container
runtime** (Docker/Podman/Kubernetes). See ``docs/security/runtime-defaults.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class NetworkPolicy(StrEnum):
    """Outbound network posture for a job phase.

    The container runtime should match this policy (e.g. ``--network=none`` for
    :attr:`DENY`). The engine records the intended policy on each phase so hosts
    and future supervisors can enforce it.
    """

    DENY = "deny"
    """No outbound network. Default for compile, Slither, and forge test."""

    ALLOW_LLM = "allow_llm"
    """Limited egress for LLM provider HTTPS only (host/orchestrator enforces)."""

    ALLOW_FETCH = "allow_fetch"
    """Optional one-shot fetch (e.g. gist). Still size- and time-bounded."""


# Spec default: 5 minutes wall clock for a full job.
DEFAULT_TIMEOUT_SECONDS: Final[int] = 300
DEFAULT_TERM_GRACE_SECONDS: Final[float] = 2.0
DEFAULT_JOB_ROOT: Final[str] = "/work/jobs"
DEFAULT_MAX_PIDS: Final[int] = 256

# Soft defaults for *documented* cgroup limits (not all enforceable in pure Python).
DEFAULT_MEMORY_BYTES: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_CPU_LIMIT: Final[str] = "2"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid integer"
        raise ValueError(msg) from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid float"
        raise ValueError(msg) from exc


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid integer"
        raise ValueError(msg) from exc


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    """Resolved security knobs for one engine process / job supervisor."""

    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    """Hard wall-clock budget for a job (entire process tree)."""

    term_grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS
    """Seconds to wait after SIGTERM before SIGKILL on the process group."""

    job_root: str = DEFAULT_JOB_ROOT
    """Root directory for per-job workspaces (must be writable)."""

    network_policy: NetworkPolicy = NetworkPolicy.DENY
    """Default network policy for heavy phases (compile/static/test)."""

    memory_limit_bytes: int | None = DEFAULT_MEMORY_BYTES
    """Hint / optional RLIMIT_AS address-space cap for child processes."""

    cpu_limit: str = DEFAULT_CPU_LIMIT
    """Documented cgroup CPU limit (e.g. Docker ``--cpus``). Not a Python rlimit."""

    rlimit_cpu_seconds: int | None = None
    """Optional ``RLIMIT_CPU`` (CPU-seconds) applied to child process groups."""

    max_pids: int = DEFAULT_MAX_PIDS
    """Documented ``--pids-limit`` for the container runtime."""

    read_only_root: bool = True
    """Expect a read-only root filesystem; only job mounts are writable."""

    drop_capabilities: bool = True
    """Expect ``--cap-drop=ALL`` (and no ``--privileged``)."""

    no_new_privileges: bool = True
    """Expect ``no-new-privileges`` security opt."""

    @classmethod
    def from_env(cls) -> SecurityConfig:
        """Load config from process environment (container / local)."""
        network_raw = os.environ.get("AUDIT_NETWORK_POLICY", NetworkPolicy.DENY.value)
        try:
            network_policy = NetworkPolicy(network_raw.strip().lower())
        except ValueError as exc:
            allowed = ", ".join(p.value for p in NetworkPolicy)
            msg = f"AUDIT_NETWORK_POLICY must be one of: {allowed} (got {network_raw!r})"
            raise ValueError(msg) from exc

        memory = _env_optional_int("AUDIT_MEMORY_LIMIT_BYTES")
        if memory is None and "AUDIT_MEMORY_LIMIT_BYTES" not in os.environ:
            memory = DEFAULT_MEMORY_BYTES

        timeout = _env_int("AUDIT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        if timeout <= 0:
            msg = "AUDIT_TIMEOUT_SECONDS must be a positive integer"
            raise ValueError(msg)

        grace = _env_float("AUDIT_TERM_GRACE_SECONDS", DEFAULT_TERM_GRACE_SECONDS)
        if grace < 0:
            msg = "AUDIT_TERM_GRACE_SECONDS must be >= 0"
            raise ValueError(msg)

        return cls(
            timeout_seconds=timeout,
            term_grace_seconds=grace,
            job_root=os.environ.get("AUDIT_JOB_ROOT", DEFAULT_JOB_ROOT),
            network_policy=network_policy,
            memory_limit_bytes=memory,
            cpu_limit=os.environ.get("AUDIT_CPU_LIMIT", DEFAULT_CPU_LIMIT),
            rlimit_cpu_seconds=_env_optional_int("AUDIT_RLIMIT_CPU_SECONDS"),
            max_pids=_env_int("AUDIT_MAX_PIDS", DEFAULT_MAX_PIDS),
            read_only_root=_env_bool("AUDIT_READ_ONLY_ROOT", default=True),
            drop_capabilities=_env_bool("AUDIT_DROP_CAPABILITIES", default=True),
            no_new_privileges=_env_bool("AUDIT_NO_NEW_PRIVILEGES", default=True),
        )

    def docker_run_flags(self) -> list[str]:
        """Recommended ``docker run`` flags matching this config.

        Hosts should apply these (or equivalent Podman/K8s constraints). They are
        not applied automatically by :func:`run_command`.
        """
        flags: list[str] = []
        if self.read_only_root:
            flags.append("--read-only")
        flags.extend(
            [
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=256m",
                "--tmpfs",
                "/work:rw,exec,nosuid,size=2g",
                "--tmpfs",
                "/artifacts:rw,noexec,nosuid,size=512m",
                f"--memory={self.memory_limit_bytes or DEFAULT_MEMORY_BYTES}",
                f"--cpus={self.cpu_limit}",
                f"--pids-limit={self.max_pids}",
                "--user",
                "10001:10001",
            ]
        )

        if self.drop_capabilities:
            flags.extend(["--cap-drop", "ALL"])
        if self.no_new_privileges:
            flags.append("--security-opt=no-new-privileges:true")
        if self.network_policy is NetworkPolicy.DENY:
            flags.append("--network=none")
        # ALLOW_LLM / ALLOW_FETCH: leave default bridge (or custom network) —
        # operator supplies an allowlisted network; we never request --privileged
        # or a Docker socket mount.
        return flags


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
