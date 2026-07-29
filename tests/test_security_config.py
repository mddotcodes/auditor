"""Tests for SecurityConfig resolution and docker flag helpers."""

from __future__ import annotations

import pytest

from auditor.security import NetworkPolicy, SecurityConfig


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("AUDIT_"):
            monkeypatch.delenv(key, raising=False)

    cfg = SecurityConfig.from_env()
    assert cfg.timeout_seconds == 300
    assert cfg.network_policy is NetworkPolicy.DENY
    assert cfg.job_root == "/work/jobs"
    assert cfg.read_only_root is True
    assert cfg.drop_capabilities is True
    assert cfg.memory_limit_bytes == 2 * 1024 * 1024 * 1024


def test_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("AUDIT_NETWORK_POLICY", "allow_llm")
    monkeypatch.setenv("AUDIT_JOB_ROOT", "/tmp/jobs")
    monkeypatch.setenv("AUDIT_MEMORY_LIMIT_BYTES", "1048576")
    monkeypatch.setenv("AUDIT_CPU_LIMIT", "1.5")
    monkeypatch.setenv("AUDIT_READ_ONLY_ROOT", "false")

    cfg = SecurityConfig.from_env()
    assert cfg.timeout_seconds == 60
    assert cfg.network_policy is NetworkPolicy.ALLOW_LLM
    assert cfg.job_root == "/tmp/jobs"
    assert cfg.memory_limit_bytes == 1_048_576
    assert cfg.cpu_limit == "1.5"
    assert cfg.read_only_root is False


def test_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="AUDIT_TIMEOUT_SECONDS"):
        SecurityConfig.from_env()


def test_invalid_network_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_NETWORK_POLICY", "open-bar")
    with pytest.raises(ValueError, match="AUDIT_NETWORK_POLICY"):
        SecurityConfig.from_env()


def test_docker_run_flags_include_hardening() -> None:
    cfg = SecurityConfig(
        timeout_seconds=300,
        network_policy=NetworkPolicy.DENY,
        memory_limit_bytes=2 * 1024 * 1024 * 1024,
        cpu_limit="2",
        max_pids=256,
        read_only_root=True,
        drop_capabilities=True,
        no_new_privileges=True,
    )
    flags = cfg.docker_run_flags()
    assert "--read-only" in flags
    assert "--network=none" in flags
    assert "--cap-drop" in flags
    assert "ALL" in flags
    assert "--security-opt=no-new-privileges:true" in flags
    assert "--pids-limit=256" in flags
    assert "--user" in flags
    assert "10001:10001" in flags
    # Must never suggest privileged or docker.sock
    joined = " ".join(flags)
    assert "privileged" not in joined
    assert "docker.sock" not in joined


def test_docker_run_flags_allow_llm_omits_network_none() -> None:
    cfg = SecurityConfig(network_policy=NetworkPolicy.ALLOW_LLM)
    flags = cfg.docker_run_flags()
    assert "--network=none" not in flags
