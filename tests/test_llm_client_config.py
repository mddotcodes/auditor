"""Unit tests for LLM dual plan/code config (no network)."""

from __future__ import annotations

import pytest

from auditor.pipeline.llm.client import (
    DEFAULT_CODE_MODEL,
    DEFAULT_PLAN_MODEL,
    dual_mode_enabled,
    model_for_role,
)


def test_dual_off_uses_single_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_LLM_DUAL", raising=False)
    monkeypatch.setenv("AUDIT_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert dual_mode_enabled() is False
    assert model_for_role("plan") == "openai/gpt-4o-mini"
    assert model_for_role("code") == "openai/gpt-4o-mini"
    assert model_for_role("repair") == "openai/gpt-4o-mini"


def test_dual_on_splits_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_LLM_DUAL", "true")
    monkeypatch.setenv("AUDIT_LLM_PLAN_MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("AUDIT_LLM_CODE_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.delenv("AUDIT_LLM_REPAIR_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert dual_mode_enabled() is True
    assert model_for_role("plan") == "deepseek/deepseek-v4-pro"
    assert model_for_role("code") == "deepseek/deepseek-v4-flash"
    assert model_for_role("repair") == "deepseek/deepseek-v4-flash"


def test_dual_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_LLM_DUAL", "1")
    monkeypatch.delenv("AUDIT_LLM_PLAN_MODEL", raising=False)
    monkeypatch.delenv("AUDIT_LLM_CODE_MODEL", raising=False)
    monkeypatch.delenv("AUDIT_LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert model_for_role("plan") == DEFAULT_PLAN_MODEL
    assert model_for_role("code") == DEFAULT_CODE_MODEL


def test_repair_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_LLM_DUAL", "true")
    monkeypatch.setenv("AUDIT_LLM_CODE_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("AUDIT_LLM_REPAIR_MODEL", "custom/repair")
    assert model_for_role("repair") == "custom/repair"
